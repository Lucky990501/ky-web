"""Offline Workbench contract/security tests. No real WeChat or Provider calls."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from app import wechat_skill as adapter
from app.skill_registry import NativeSkillArchive, SkillRegistry
from app.skills import SkillDeployment
from app.store import POCStore
from app.product_store import ProductStore

SCRIPTS = adapter.SOURCE / 'scripts'
sys.path.insert(0,str(SCRIPTS))
import wechat_draft as flow
import workbench_guard as guard
from PIL import Image


class Scratch:
    """Keep inherited Windows sandbox ACLs; identical TempDirectory API for tests."""
    def __init__(self,*args,**kwargs):
        base=Path(__file__).resolve().parents[2]/'.codex-wht/test-workspaces'
        base.mkdir(parents=True,exist_ok=True)
        self.base=base
        self.root=base/uuid.uuid4().hex;self.root.mkdir(mode=0o755);self.name=str(self.root)
    def cleanup(self):
        if self.root.exists():
            assert self.root.resolve().parent==self.base.resolve() and not self.root.is_symlink()
            shutil.rmtree(self.root)
    def __enter__(self):return self.name
    def __exit__(self,*args):self.cleanup()


def picture():
    out=io.BytesIO();Image.new('RGB',(30,20),'green').save(out,format='PNG');return out.getvalue()


def dns(host,port,**kwargs):
    ips={'public.example':['93.184.216.34'],'mixed.example':['93.184.216.34','10.0.0.1']}
    return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)) for ip in ips.get(host,[host])]


class SecurityTests(unittest.TestCase):
    def test_private_and_local_targets(self):
        for host in ('localhost','127.0.0.1','10.2.3.4','172.16.1.2','192.168.1.1','169.254.169.254','0.0.0.0','[::1]','[::ffff:127.0.0.1]','mixed.example'):
            with self.subTest(host=host),self.assertRaises(guard.SecurityError):
                guard.public_target('http://'+host+'/image',dns)

    def test_public_target(self):
        self.assertEqual(guard.public_target('https://public.example/a.png',dns)[-1],['93.184.216.34'])

    def test_bad_schemes_credentials_ports(self):
        for url in ('file:///etc/passwd','ftp://public.example/a','https://user:secret@public.example/a','https://public.example:18100/a'):
            with self.assertRaises(guard.SecurityError):guard.public_target(url,dns)

    def test_redirect_public_to_private(self):
        class Connection:
            calls=0
            def __init__(self,*args):Connection.calls+=1
            def request(self,*args,**kwargs):pass
            def getresponse(self):return SimpleNamespace(status=302,getheader=lambda name,*a:'http://127.0.0.1/secret')
            def close(self):pass
        with self.assertRaises(guard.SecurityError):guard.fetch_image('https://public.example/a',100,resolver=dns,connect=Connection)
        self.assertEqual(Connection.calls,1)

    def test_connection_uses_validated_ip(self):
        with patch.object(guard.socket,'create_connection') as connect:
            conn=guard.PinnedHTTP('public.example',80,'93.184.216.34',False);conn.connect()
            self.assertEqual(connect.call_args.args[0],('93.184.216.34',80))

    def test_paths_all_escapes(self):
        with Scratch() as name:
            root=Path(name)
            for value in ('../secret','%2e%2e/secret','/etc/passwd','C:/secret','\\\\server\\share','file:///secret'):
                with self.subTest(path=value),self.assertRaises(guard.SecurityError):guard.contained(root,value)

    def test_html_css_cover_image_map_escape(self):
        with Scratch() as name:
            root=Path(name);config=root/'article.json'
            cfg=dict(account='synthetic',title='Title',digest='Digest',html='../bad.html',cover='cover.png')
            with self.assertRaises(guard.SecurityError):flow.prepare_html(cfg,config)
            (root/'body.html').write_text('<link rel="stylesheet" href="../bad.css"><section id="article">Text</section>',encoding='utf-8')
            with self.assertRaises(guard.SecurityError):flow.prepare_html(dict(cfg,html='body.html'),config)
            with self.assertRaises(guard.SecurityError):flow.local_image('a',root,dict(cfg,image_map={'a':'../secret'}),config)
            with self.assertRaises(guard.SecurityError):flow.local_image('../secret',root,cfg,config)

    def test_symlink_escape(self):
        with Scratch() as name:
            root=Path(name);outside=root.parent/'outside'
            # Deterministic resolver simulation covers containment without requiring
            # Windows symlink privileges; Native ZIP symlinks are independently rejected.
            with patch.object(Path,'resolve',side_effect=lambda *a,**k: root if k.get('strict') else outside):
                with self.assertRaises(guard.SecurityError):guard.contained(root,'link')

    def test_secret_field_blocked_and_error_redacted(self):
        with Scratch() as name:
            p=Path(name)/'article.json';p.write_text(json.dumps({'access_token':'TOP-SECRET'}))
            with self.assertRaises(flow.WorkflowError) as caught:flow.read_config(p)
            self.assertNotIn('TOP-SECRET',str(caught.exception))

    def test_no_publish_send_delete_endpoints(self):
        api=object.__new__(flow.WeChat)
        for endpoint in ('freepublish/submit','message/mass/sendall','draft/delete','draft/update','../other'):
            with self.assertRaises(flow.WorkflowError):api.request('POST',endpoint)


class ContractTests(unittest.TestCase):
    def test_skill_revision_load_and_source_lock(self):
        contract=adapter.verify_revision_contract()
        archive=adapter.native_package();view=NativeSkillArchive.inspect(archive,adapter.SLUG)
        self.assertEqual(contract['version'],'1.0.0');self.assertEqual(view['sha256'],contract['artifact_sha256'])
        self.assertIn('wechat-html-draft/agents/openai.yaml',view['files'])
        self.assertIn('wechat-html-draft/scripts/workbench_guard.py',view['files'])

    def test_source_revision_drift_rejected(self):
        wrong=adapter.revision_contract();wrong['artifact_sha256']='0'*64
        with patch.object(adapter,'revision_contract',return_value=wrong):
            with self.assertRaisesRegex(adapter.WechatSkillError,'REVISION_IDENTITY_BLOCKED'):adapter.verify_revision_contract()

    def test_registry_import_publication_and_revision_immutability(self):
        with Scratch() as name:
            root=Path(name);store=POCStore(root/'test.db');store.seed_demo_data()
            product=ProductStore(store);product.initialize()
            product.create_user('tenant-a','wechat-fixture@example.invalid','unused','Synthetic fixture','member')
            actor=product.user_by_email('wechat-fixture@example.invalid')['id']
            registry=SkillRegistry(store,root/'registry',Path(__file__).resolve().parents[1]/'skill_packages');registry.initialize()
            version=adapter.import_revision(registry,actor)
            self.assertEqual(version['status'],'draft');self.assertEqual(adapter.import_revision(registry,actor)['id'],version['id'])
            self.assertEqual(registry.test_version(version['id'])['status'],'passed')
            registry.publish(version['id'],actor)
            target=root/'profile/skills';SkillDeployment(registry.published_root).deploy({adapter.SLUG:adapter.VERSION},target)
            self.assertTrue((target/adapter.SLUG/'scripts/wechat_draft.py').is_file())
            with self.assertRaises(ValueError):registry.import_archive(adapter.SLUG,adapter.VERSION,'Changed','',b'bad','test-actor')

    def test_existing_agent_binding_plan_and_rename(self):
        revision=dict(id='skill-v1',skill_id='skill',slug=adapter.SLUG,version=adapter.VERSION,status='published',checksum=adapter.revision_contract()['artifact_sha256'])
        class Catalog:
            def list_templates(self):return [dict(id='existing-agent',slug=adapter.AGENT_SLUG,definition_source='productized',name='公众号运营助手')]
            def detail(self,agent):return dict(id=agent,versions=[dict(id='draft-v2',status='draft',skills=[dict(skill_id='old-skill',skill_version_id='old-v1')])])
        plan=adapter.binding_plan(Catalog(),revision,'draft-v2')
        self.assertEqual(plan['agent_id'],'existing-agent');self.assertEqual(len(plan['bindings']),2)
        self.assertFalse(plan['runtime_binding_authorized'])

    def test_formal_productized_revision_binding_in_isolated_fixture(self):
        from app.agent_productization import AgentProductization
        with Scratch() as name:
            root=Path(name);store=POCStore(root/'binding.db');store.seed_demo_data()
            product=ProductStore(store);product.initialize()
            product.create_user('tenant-a','wechat-fixture@example.invalid','unused','Synthetic fixture','member')
            actor=product.user_by_email('wechat-fixture@example.invalid')['id']
            registry=SkillRegistry(store,root/'registry',Path(__file__).resolve().parents[1]/'skill_packages');registry.initialize()
            catalog=AgentProductization(store);catalog.initialize()
            # Synthetic local fixture only, matching existing control-plane tests.
            with store.connection() as conn:
                conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advance_origin='controlled_advance',advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='wechat-unit-fixture',advanced_by_source_commit=? WHERE scope='agent_data_contract'",('f'*40,))
            existing=catalog.create_template({'slug':adapter.AGENT_SLUG,'name':'公众号运营助手'},actor)
            existing=catalog.create_version(existing['id'],{'persona':'Synthetic fixture'},actor)
            before=len(catalog.list_templates())
            skill=adapter.import_revision(registry,actor);skill=registry.publish(skill['id'],actor)
            skill=registry.version(skill['id'])
            plan=adapter.binding_plan(catalog,skill,existing['versions'][0]['id'])
            catalog.bind_skills(plan['agent_id'],plan['agent_revision_id'],plan['bindings'],actor)
            loaded=catalog.detail(existing['id'])['versions'][0]['skills']
            self.assertEqual([(x['slug'],x['version']) for x in loaded],[(adapter.SLUG,adapter.VERSION)])
            self.assertEqual(len(catalog.list_templates()),before)

    def test_missing_agent_rejects_no_creation(self):
        with self.assertRaises(adapter.WechatSkillError):adapter.binding_plan(SimpleNamespace(list_templates=lambda:[]),{},'draft')

    def test_prepare_without_credentials_and_outputs(self):
        with Scratch() as name:
            root=Path(name);(root/'cover.png').write_bytes(picture())
            (root/'article.html').write_text('<section id="article"><p>合成测试正文。</p></section>',encoding='utf-8')
            cfg=root/'article.json';cfg.write_text(json.dumps(dict(account='TEST ONLY',title='Title',digest='Digest',html='article.html',cover='cover.png',body_selector='#article')))
            with patch.dict(os.environ,{'WECHAT_ACCESS_TOKEN':'MUST_NOT_INHERIT','WECHAT_APP_SECRET':'MUST_NOT_INHERIT'}):
                # Offline legacy behavior fixture only, NOT a Linux runtime proof.
                # -I deliberately removes PYTHONPATH; explicitly inject the supplied
                # local dependency location in the child for this synthetic test.
                child_run=__import__('subprocess').run
                def offline_child(args,**kwargs):
                    dependency_path=os.environ.get('PYTHONPATH')
                    args=list(args)
                    if dependency_path:
                        index=args.index('-c')+1
                        args[index]='import sys;sys.path.insert(0,'+repr(dependency_path)+');'+args[index]
                    return child_run(args,**kwargs)
                runtime=SimpleNamespace(root=root.parent/'synthetic-managed-runtime',resolve=lambda *args:Path(sys.executable))
                with patch.object(adapter.subprocess,'run',side_effect=offline_child):
                    report=adapter.execute('PREPARE',root,cfg,revision={'synthetic':True},runtime=runtime)
            self.assertEqual(report['wechat_calls'],0);self.assertTrue((root/'run/prepared.html').is_file())
            self.assertTrue((root/'verification.json').is_file());self.assertFalse((root/'run/state.json').exists())

    def test_prepare_traversal_rejected(self):
        with Scratch() as name:
            root=Path(name)
            with self.assertRaises(adapter.WechatSkillError):adapter.execute('PREPARE',root,root.parent/'outside.json')

    def test_create_draft_cannot_take_ambient_secret(self):
        with patch.dict(os.environ,{'WECHAT_APP_ID':'wxTEST','WECHAT_ACCESS_TOKEN':'SECRET'}):
            with self.assertRaisesRegex(adapter.WechatSkillError,'SECRET_AND_EGRESS_CONTRACT'):adapter.execute('CREATE_DRAFT',Path('.'),Path('article.json'),upload_requested=True)

    def test_create_draft_requires_explicit_request(self):
        with self.assertRaisesRegex(adapter.WechatSkillError,'UPLOAD_NOT_AUTHORIZED'):
            adapter.execute('CREATE_DRAFT',Path('.'),Path('article.json'))

    def test_cover_traversal_blocked_before_any_api(self):
        with Scratch() as name:
            root=Path(name);(root/'article.html').write_text('<section id="article">Text</section>')
            config=root/'article.json';config.write_text(json.dumps(dict(account='Test',title='Title',digest='Digest',html='article.html',cover='../outside.png',body_selector='#article')))
            with self.assertRaises(adapter.WechatSkillError):adapter.execute('PREPARE',root,config)

    def test_ordinary_chat_and_other_agent_tasks_do_not_invoke(self):
        for text in ('你好','写一句招生广告','生成一张海报','安排一个社区活动'):
            self.assertIsNone(adapter.action_for_request(text))
        self.assertEqual(adapter.action_for_request('帮我排版公众号文章'),'PREPARE')
        self.assertEqual(adapter.action_for_request('创建公众号草稿'),'CREATE_DRAFT')
        self.assertEqual(adapter.action_for_request('公众号排版，不要上传'),'PREPARE')

    def test_unknown_action_rejected(self):
        with self.assertRaises(adapter.WechatSkillError):adapter.execute('PUBLISH',Path('.'),Path('article.json'))


if __name__=='__main__':unittest.main()
