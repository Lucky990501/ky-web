"""Offline contract tests. Linux probes/install commands are synthetic, not live.

Real descriptor/Source/Native artifact/hash checks run against independent file
fixtures. Never execute Linux wheels or provision a real venv on Windows.
"""
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from app import skill_python_runtime as rt
from app import wechat_skill
from test_wechat_skill_integration import Scratch


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=Scratch();self.root=self.temp.root
        self.project=self.root/'checkout/enterprise_agent_poc';self.project.mkdir(parents=True)
        descriptor=rt.read_json(rt.PROJECT/rt.DESCRIPTOR)
        for name in [rt.DESCRIPTOR,*descriptor['adapter_files']]:
            path=self.project/name;path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(rt.PROJECT/name,path)
        self.descriptor=descriptor
        self.lock=rt.read_json(self.project/descriptor['lock_file'])
        self.packages={p['package']:p['version'] for p in self.lock['packages']}
        self.approval=dict(source_commit='a'*40,source_tree='b'*40,
            descriptor_sha256=rt.sha((self.project/rt.DESCRIPTOR).read_bytes()),
            lock_sha256=descriptor['lock_sha256'],artifact_sha256=descriptor['artifact_sha256'],
            adapter_digest=rt.sha(rt.canonical(descriptor['adapter_files'])),environment='test',
            managed_root=str(self.root/'managed'))
        self.revision=dict(id=str(uuid.uuid4()),skill_id=str(uuid.uuid4()),slug='wechat-html-draft',
            version='1.0.0',status='published',checksum=descriptor['artifact_sha256'])
        self.runtime=rt.SkillPythonRuntime(self.root/'managed',self.approval,project=self.project)
        self.git=patch.object(rt,'git_identity',return_value={k:self.approval[k] for k in ('source_commit','source_tree')})
        self.git.start();self.addCleanup(self.git.stop)

    def tearDown(self):self.temp.cleanup()

    def actual(self,venv=None):
        value=dict(python='3.11.16',implementation='cpython',os='linux',architecture='x86_64',
            glibc=['glibc','2.35'],abi='cpython-311-x86_64-linux-gnu')
        if venv is not None:value.update(packages=self.packages,installed=list(self.packages),imports='PASS',
             prefix=str(venv),base_prefix='/synthetic/base-python')
        return value

    def ready(self):
        directory=self.runtime.directory(self.revision)
        (directory/'venv/bin').mkdir(parents=True);(directory/'venv/bin/python').write_bytes(b'SYNTHETIC_NOT_EXECUTABLE')
        (directory/'lock').mkdir();(directory/'receipt').mkdir()
        (directory/'lock/runtime-lock.json').write_bytes((self.project/self.descriptor['lock_file']).read_bytes())
        (directory/'lock/requirements.txt').write_bytes((self.project/'integrations'/self.lock['requirements_file']).read_bytes())
        binding=self.runtime.binding(self.revision,self.descriptor)
        (directory/'receipt/binding.json').write_bytes(rt.canonical(binding))
        self.receipt=dict(binding=binding,creation_status='READY',pip_check='PASS',
            venv_identity=rt.sha(rt.canonical(binding)),lock_sha256=self.descriptor['lock_sha256'],
            python_version='3.11.16',wheel_hashes={p['filename']:p['sha256'] for p in self.lock['packages']},
            environment_files_sha256=rt.file_tree_digest(directory/'venv'))
        (directory/'receipt/runtime.json').write_bytes(rt.canonical(self.receipt))
        return directory

    def resolve(self,action='PREPARE'):
        with patch.object(rt,'probe',side_effect=lambda py,packages,venv=None:self.actual(venv)):
            return self.runtime.resolve(self.revision,action)

    def test_01_revision_resolves_own_runtime(self):
        directory=self.ready();self.assertEqual(self.resolve(),directory/'venv/bin/python')

    def test_02_missing_runtime_blocks(self):
        with self.assertRaisesRegex(rt.SkillRuntimeError,'SKILL_RUNTIME_NOT_READY'):self.resolve()

    def test_03_lock_mismatch_blocks(self):
        self.ready();path=self.project/self.descriptor['lock_file'];path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_04_python_version_mismatch_blocks(self):
        self.ready()
        with patch.object(rt,'probe',return_value=dict(self.actual(self.runtime.directory(self.revision)/'venv'),python='3.12.14')):
            with self.assertRaises(rt.SkillRuntimeError):self.runtime.resolve(self.revision,'PREPARE')

    def test_05_v2_does_not_reuse_v1(self):
        self.ready();self.revision['version']='2.0.0'
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_06_both_actions_same_runtime(self):
        self.ready();self.assertEqual(self.resolve(),self.resolve('CREATE_DRAFT'))

    def test_07_install_only_targets_revision_venv_no_main_mutation(self):
        wheelhouse=self.root/'wheels';wheelhouse.mkdir()
        # Synthetic file hashes are deliberately not used to bypass the actual
        # wheel guard. Supply real already verified Linux wheels read-only.
        real=Path(os.environ.get('WECHAT_RUNTIME_TEST_WHEELHOUSE',
                  str(rt.PROJECT.parent/'.codex-wechat-revision-runtime-v1/wheelhouse')))
        self.assertTrue(real.exists(),'Set WECHAT_RUNTIME_TEST_WHEELHOUSE to the verified revision wheelhouse')
        calls=[]
        def commands(args,**kwargs):
            calls.append([str(arg) for arg in args])
            if 'venv' in args:
                (Path(args[-1])/'bin').mkdir(parents=True)
                (Path(args[-1])/'bin/python').write_bytes(b'SYNTHETIC_NOT_EXECUTABLE')
            return b''
        base=self.root/'main-python';base.write_bytes(b'UNMODIFIED_MAIN_INTERPRETER')
        before=rt.file_tree_digest(wheelhouse)
        with patch.object(rt,'probe',side_effect=lambda py,packages,venv=None:self.actual(venv)),patch.object(rt,'run',side_effect=commands):
            receipt=self.runtime.install(self.revision,base,real)
        self.assertEqual(base.read_bytes(),b'UNMODIFIED_MAIN_INTERPRETER');self.assertEqual(before,rt.file_tree_digest(wheelhouse))
        install=next(call for call in calls if 'install' in call)
        self.assertEqual(install[install.index('--python')+1],str(self.runtime.directory(self.revision)/'venv/bin/python'))
        for flag in ('--no-index','--no-deps','--require-hashes','--only-binary=:all:','--no-cache-dir'):self.assertIn(flag,install)
        self.assertNotIn('sudo',str(calls));self.assertEqual(receipt['pip_check'],'PASS')
        self.assertIn('--without-pip',calls[0]);self.assertIn('--copies',calls[0])

    def test_08_artifact_mismatch_blocks(self):
        self.ready();(self.project/self.descriptor['artifact_file']).write_bytes(b'WRONG')
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_09_adapter_source_mismatch_blocks(self):
        self.ready();(self.project/'app/wechat_skill.py').write_bytes(b'WRONG_SOURCE')
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_10_receipt_no_secret(self):
        self.ready();payload=json.dumps(self.receipt)
        for secret in ('WECHAT_APP_SECRET','WECHAT_ACCESS_TOKEN','SYNTHETIC-SECRET'):self.assertNotIn(secret,payload)

    def test_11_complete_hashed_lock(self):
        self.assertEqual(len(self.lock['packages']),12)
        self.assertTrue(all(len(p['sha256'])==64 and p['official_sha_verified'] for p in self.lock['packages']))
        self.assertEqual(rt.sha((self.project/'integrations'/self.lock['requirements_file']).read_bytes()),self.lock['requirements_sha256'])

    def test_12_unsupported_platform_probe_blocks(self):
        with patch.object(rt,'run',return_value=json.dumps(dict(self.actual(),os='win32')).encode()):
            with self.assertRaises(rt.SkillRuntimeError):rt.probe(Path('synthetic'),[])

    def test_13_source_commit_mismatch_blocks(self):
        self.ready();self.approval['source_commit']='c'*40
        wrong=rt.SkillPythonRuntime(self.runtime.root,self.approval,project=self.project)
        with self.assertRaises(rt.SkillRuntimeError):wrong.resolve(self.revision,'PREPARE')

    def test_14_wrong_revision_uuid_cannot_reuse_receipt(self):
        self.ready();self.revision['id']=str(uuid.uuid4())
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_15_environment_file_drift_blocks(self):
        directory=self.ready();(directory/'venv/injected.py').write_bytes(b'WRONG')
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_16_import_failure_blocks(self):
        self.ready()
        with patch.object(rt,'probe',side_effect=rt.SkillRuntimeError()):
            with self.assertRaises(rt.SkillRuntimeError):self.runtime.resolve(self.revision,'PREPARE')

    def test_17_pip_check_failure_receipt_blocks(self):
        directory=self.ready();self.receipt['pip_check']='FAIL'
        (directory/'receipt/runtime.json').write_bytes(rt.canonical(self.receipt))
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_18_venv_inside_source_blocked(self):
        approval=dict(self.approval,managed_root=str(self.project/'runtime'))
        runtime=rt.SkillPythonRuntime(self.project/'runtime',approval,project=self.project)
        with self.assertRaises(rt.SkillRuntimeError):runtime.directory(self.revision)

    def test_19_draft_revision_blocked(self):
        self.ready();self.revision['status']='draft'
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_20_no_interpreter_fallback(self):
        with self.assertRaisesRegex(wechat_skill.WechatSkillError,'SKILL_RUNTIME_NOT_READY'):
            wechat_skill.runtime_for_action('PREPARE',self.revision,None)

    def test_21_unrelated_installed_package_blocks(self):
        self.ready();actual=self.actual(self.runtime.directory(self.revision)/'venv');actual['installed'].append('unknown')
        with patch.object(rt,'probe',return_value=actual):
            with self.assertRaises(rt.SkillRuntimeError):self.runtime.resolve(self.revision,'PREPARE')

    def test_22_descriptor_drift_blocks(self):
        self.ready();path=self.project/rt.DESCRIPTOR;path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_23_resolve_never_installs(self):
        self.ready()
        with patch.object(rt,'run',side_effect=AssertionError('unexpected subprocess')):
            # git/probe are substituted I/O only; actual source/hash guards run.
            self.resolve()

    def test_24_bad_uuid_path_blocks(self):
        for value in ('../x','/tmp/x','V1'):
            self.revision['id']=value
            with self.assertRaises(rt.SkillRuntimeError):self.resolve()

    def test_25_glibc_and_abi_mismatch_block(self):
        for changes in (dict(glibc=['glibc','2.17']),dict(abi='cpython-312-linux'),dict(architecture='aarch64')):
            with patch.object(rt,'run',return_value=json.dumps(dict(self.actual(),**changes)).encode()):
                with self.assertRaises(rt.SkillRuntimeError):rt.probe(Path('synthetic'),[])

    def test_26_bad_wheel_hash_blocks_before_any_install_write(self):
        wheels=self.root/'bad-wheels';wheels.mkdir()
        for item in self.lock['packages']:(wheels/item['filename']).write_bytes(b'INVALID_WHEEL')
        with patch.object(rt,'probe',return_value=self.actual()),patch.object(rt,'run') as commands:
            with self.assertRaises(rt.SkillRuntimeError):self.runtime.install(self.revision,Path('base-python'),wheels)
        commands.assert_not_called();self.assertFalse(self.runtime.directory(self.revision).exists())

    def test_27_same_revision_cannot_rebind_another_source(self):
        directory=self.ready();before=rt.file_tree_digest(directory)
        approval=dict(self.approval,source_commit='c'*40)
        other=rt.SkillPythonRuntime(self.runtime.root,approval,project=self.project)
        with patch.object(rt,'git_identity',return_value={k:approval[k] for k in ('source_commit','source_tree')}):
            with self.assertRaises(rt.SkillRuntimeError):other.install(self.revision,Path('unused-base'),Path('unused-wheels'))
        self.assertEqual(before,rt.file_tree_digest(directory))

    def test_28_production_or_different_managed_root_denied(self):
        for changes in (dict(environment='production'),dict(managed_root=str(self.root/'other'))):
            other=rt.SkillPythonRuntime(self.runtime.root,dict(self.approval,**changes),project=self.project)
            with self.assertRaises(rt.SkillRuntimeError):other.directory(self.revision)

    def test_29_prepare_command_uses_only_resolved_python(self):
        task=self.root/'task';task.mkdir();config=task/'article.json';config.write_text('{}')
        python=self.root/'managed/dedicated-python'
        runtime=SimpleNamespace(root=self.root/'managed',resolve=lambda *args:python)
        with patch.object(wechat_skill.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as child:
            with patch.dict(os.environ,{'PYTHONPATH':'UNTRUSTED','WECHAT_APP_SECRET':'SYNTHETIC'}):
                wechat_skill.execute('PREPARE',task,config,revision=self.revision,runtime=runtime)
        self.assertEqual(child.call_args.args[0][0],str(python));self.assertIn('-I',child.call_args.args[0])
        self.assertNotIn('PYTHONPATH',child.call_args.kwargs['env'])
        self.assertNotIn('WECHAT_APP_SECRET',child.call_args.kwargs['env'])


if __name__=='__main__':unittest.main()
