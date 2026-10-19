"""Preview-only persona override. Shared catalog and immutable Skills unchanged."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path

AGENT='copywriting-agent'
PROMPT_PATH=Path(__file__).resolve().parents[1]/'preview_prompts/copywriting-video-script.md'


def prompt():
    text=PROMPT_PATH.read_text(encoding='utf-8').strip()
    if not text.startswith('# 真实共情型·成长转化视频逐字稿'):
        raise ValueError('Preview copywriting prompt missing or invalid')
    return text


def install():
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true':
        raise RuntimeError('Copywriting override is Preview-only')
    from app.agent_catalog import CATALOG
    # Keep id, manifest/profile hash and old conversations compatible. Never
    # republish or edit historical Skill artifacts to change this persona.
    CATALOG[AGENT]=replace(CATALOG[AGENT],instructions=prompt())


def provider_request(body,task):
    if not task or task['agent_id']!=AGENT:return body,None
    if os.environ.get('CUSTOMER_PREVIEW_MODE')!='true':
        raise RuntimeError('Copywriting override is Preview-only')
    text=prompt()
    instructions=body.get('instructions') or ''
    if text not in instructions:
        body={**body,'instructions':instructions+'\n\n'+text}
    return body,hashlib.sha256(text.encode()).hexdigest()
