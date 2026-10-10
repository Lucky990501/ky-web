"""Internal revision-pinned Skill child, invoked only by SkillActionDispatcher.

Every mutation waits for durable parent commit. Credential-bearing params and
Skill diagnostics never enter the journal channel. No standalone public entry.
"""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()


def run(script, workspace, read_only=False):
    output=sys.stdout
    def exchange(message):
        output.write(json.dumps(message,separators=(',',':'))+'\n');output.flush()
        line=sys.stdin.readline(8192)
        if not line or len(line)>=8192:raise RuntimeError('JOURNAL_DISCONNECTED')
        reply=json.loads(line)
        if reply.get('allow') is not True:raise RuntimeError('JOURNAL_PERMISSION_DENIED')
        return reply
    sys.path.insert(0,str(script.parent))
    spec=importlib.util.spec_from_file_location('revision_wechat_draft',script)
    skill=importlib.util.module_from_spec(spec);spec.loader.exec_module(skill)
    manifest=json.loads((workspace/'upload-manifest.v2.json').read_bytes())
    article=manifest['article'];run_dir=workspace/'run';run_dir.mkdir(exist_ok=True)
    original=skill.WeChat.request
    def request(api,method,endpoint,**kwargs):
        mutation=endpoint in ('material/add_material','media/uploadimg','draft/add')
        if read_only and mutation:raise RuntimeError('READBACK_ONLY')
        if (method,endpoint) not in {('GET','token'),('POST','material/add_material'),('POST','media/uploadimg'),('POST','draft/add'),('POST','draft/get')}:
            raise RuntimeError('ENDPOINT_BLOCKED')
        files=kwargs.get('files') or {}
        request_hash=sha(canonical(dict(method=method,endpoint=endpoint,payload=kwargs.get('payload'),
            files={k:sha(v[1]) for k,v in files.items()})))
        key=('cover:' if endpoint=='material/add_material' else 'body:')+sha(files['media'][1]) if files else endpoint
        permit=exchange(dict(kind='intent' if mutation else 'read',endpoint=endpoint,request_sha256=request_hash,artifact_key=key))
        result=original(api,method,endpoint,**kwargs)
        if mutation:
            exchange(dict(kind='receipt',intent_id=permit['intent_id'],
                          result={k:result[k] for k in ('media_id','url') if k in result}))
        if endpoint=='draft/get':api.readback_hash=sha(canonical(result))
        return result
    skill.WeChat.request=request
    with open(os.devnull,'w') as sink,contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
        content=skill.BeautifulSoup((workspace/'prepared.html').read_text(encoding='utf-8'),'html.parser')
        images={name:skill.image_data((workspace/name).read_bytes()) for name in set(article['images'])}
        cover=skill.image_data((workspace/article['cover']).read_bytes(),cover=True)
        cfg=dict(title=article['title'],digest=article['digest'])
        api=skill.WeChat()
        if read_only:
            state=json.loads((workspace/'readback-state.json').read_bytes())
            for tag in content.find_all('img'):
                tag['src']=state['body'][sha(images[tag['src']][0])]
            payload=dict(title=article['title'],digest=article['digest'],thumb_media_id=state['cover'],content=str(content))
            skill.verify_draft(api,dict(draft_id=state['media_id']),payload,run_dir)
        else:
            skill.publish_to_draft(cfg,content,images,cover,run_dir,manifest['content_version'],api)
        exchange(dict(kind='confirmed',readback_sha256=api.readback_hash))


if __name__=='__main__':
    try:
        run(Path(sys.argv[1]),Path(sys.argv[2]),len(sys.argv)==4 and sys.argv[3]=='readback')
    except BaseException:
        sys.exit(1)
