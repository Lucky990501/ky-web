"""Article input/result adapter only. Execution belongs to SkillActionDispatcher."""
from __future__ import annotations

import base64
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re

from PIL import Image

from app.skill_dispatch import SkillDispatchError
from app.skill_python_runtime import sha


class SafeHTML(HTMLParser):
    def handle_starttag(self,tag,attrs):
        if tag.lower() in ('script','iframe','object','embed','link','base','form'):
            raise ValueError()
        for key,value in attrs:
            if (key.lower().startswith('on') or (value and re.search(r'javascript:|file:|\\|\.\./',value,re.I))):
                raise ValueError()
    handle_startendtag=handle_starttag


def safe_html(value):
    if not isinstance(value,str) or not value.strip() or len(value.encode())>256*1024:raise ValueError()
    SafeHTML().feed(value)
    if re.search(r'@import|url\s*\(',value,re.I):raise ValueError()


class WechatPrepareAdapter:
    def validate_input(self,payload):
        try:
            if not isinstance(payload,dict) or set(payload)!={'title','digest','html','cover_asset','assets'}:raise ValueError()
            for name,maximum in (('title',64),('digest',120)):
                if not isinstance(payload[name],str) or not 0<len(payload[name].strip())<=maximum:raise ValueError()
            safe_html(payload['html'])
            assets=payload['assets']
            if not isinstance(assets,dict) or not 1<=len(assets)<=12:raise ValueError()
            decoded={};total=0
            for name,value in assets.items():
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}\.(png|jpg|jpeg|webp)',name) or not isinstance(value,str) or len(value)>3*1024*1024:raise ValueError()
                raw=base64.b64decode(value,validate=True);total+=len(raw)
                if len(raw)>2*1024*1024 or total>8*1024*1024:raise ValueError()
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in ('PNG','JPEG','WEBP') or max(image.size)>4096:raise ValueError()
                    image.verify()
                decoded[name]=raw
            if payload['cover_asset'] not in decoded:raise ValueError()
            return dict(payload,assets=decoded)
        except Exception:raise SkillDispatchError('SKILL_INPUT_INVALID') from None

    def materialize(self,payload,workspace):
        (workspace/'assets').mkdir()
        for name,raw in payload['assets'].items():(workspace/'assets'/name).write_bytes(raw)
        (workspace/'article.html').write_text(payload['html'],encoding='utf-8')
        config=dict(account='OFFLINE PREPARE',title=payload['title'],digest=payload['digest'],
                    html='article.html',cover='assets/'+payload['cover_asset'],body_selector='#article')
        target=workspace/'article.json';target.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
        return target

    def normalize(self,workspace,relative):
        try:
            html=workspace/'run/prepared.html';preflight=workspace/'run/preflight.json'
            for path in (html,preflight):
                if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()) or not path.is_file():raise ValueError()
            safe_html(html.read_text(encoding='utf-8'))
            if preflight.stat().st_size>64*1024:raise ValueError()
            data=json.loads(preflight.read_bytes())
            verification=dict(offline=True,wechat_calls=0,visual_verified=False)
            for key in ('title_chars','digest_chars','image_count','remote_images_not_checked_offline'):
                value=data[key]
                if type(value) is not int or not 0<=value<=256*1024:raise ValueError()
                verification[key]=value
            if data['visual_verified'] is not False:raise ValueError()
            # Exclude title/digest/account/style warning text from Agent/audit.
            path=workspace/'verification.json'
            if path.exists() or path.is_symlink():raise ValueError()
            path.write_text(json.dumps(verification),encoding='utf-8')
            refs=[]
            for item,mime in ((html,'text/html'),(path,'application/json')):
                if item.stat().st_size>512*1024:raise ValueError()
                refs.append(dict(ref='workspace:'+relative+'/'+item.relative_to(workspace).as_posix(),
                    mime_type=mime,size_bytes=item.stat().st_size,sha256=sha(item.read_bytes())))
            return dict(artifact_refs=refs,summary='已生成公众号排版预览和离线校验文件；尚未上传。',verification=verification)
        except Exception:raise SkillDispatchError('SKILL_RESULT_INVALID') from None
