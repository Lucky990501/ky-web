"""FIXTURE_ONLY: bounded original images and separate Word-only copies."""
import asyncio
from dataclasses import replace
import hashlib
import io
import random
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import httpx
from PIL import Image
import pytest

from app.preview_illustration_provider import MAX_IMAGE,MAX_ORIGINAL_IMAGE,IllustrationConfig,IllustrationProvider,IllustrationError,validated_image,word_image_copy
from app.document_generator import ActivityPlanDocumentService
from test_preview_illustrations import setup,image_fixture,completed_task,picture
from test_preview_illustration_download_contract import oss,HOST,URL


def noisy_png(seed=0,alpha=False):
    mode='RGBA' if alpha else 'RGB';rng=random.Random(seed)
    image=Image.frombytes(mode,(1024,1024),rng.randbytes(1024*1024*len(mode)))
    if alpha:image.paste((0,0,0,0),(0,0,32,32))
    out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()


def test_word_copy_small_original_keeps_exact_bytes():
    original=picture();word,meta=word_image_copy(original)
    assert word==original and meta['conversion']=='original_bytes' and meta['sha256']==hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize('alpha',[False,True])
def test_large_1024_png_preserved_and_separate_word_jpeg_is_bounded_and_same_ratio(alpha):
    original=noisy_png(7,alpha);digest=hashlib.sha256(original).hexdigest()
    assert MAX_IMAGE<len(original)<MAX_ORIGINAL_IMAGE
    with pytest.raises(IllustrationError):validated_image(original)  # Shared Word limit stays 2MB.
    metadata=validated_image(original,'image/png',max_bytes=MAX_ORIGINAL_IMAGE)
    word,small=word_image_copy(original)
    assert hashlib.sha256(original).hexdigest()==digest==metadata['sha256']
    assert len(word)<=MAX_IMAGE and small['mime_type']=='image/jpeg' and small['width']==small['height']==1024
    assert small['sha256']!=digest and small['conversion'].startswith('word_jpeg_q')
    with Image.open(io.BytesIO(word)) as image:
        assert image.format=='JPEG' and image.mode=='RGB'
        if alpha:assert min(image.getpixel((16,16)))>=245


def test_large_url_original_download_uses_explicit_bounded_8mb_only_for_preview(monkeypatch,oss):
    original=noisy_png();oss.bodies=[original]
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kwargs:httpx.MockTransport(lambda request:httpx.Response(200,json={'data':[{'url':URL}]})))
    config=IllustrationConfig(key='FIXTURE_ONLY',enabled=True,asset_hosts=(HOST,));events=[]
    body,metadata=asyncio.run(IllustrationProvider(config).generate('FIXTURE_ONLY',lambda *args:events.append(args)))
    assert body==original and metadata['bytes']==len(original)>MAX_IMAGE and metadata['response_format']=='url'
    assert oss.reads==[MAX_ORIGINAL_IMAGE+1] and len(events)==1 and events[0][0]==200
    assert set(oss.requests[0]['headers'])=={'Accept'}


@pytest.mark.parametrize('body',[b'x'*(MAX_ORIGINAL_IMAGE+1),b'not a png'],ids=['over_original_limit','invalid_image'])
def test_original_limits_and_fake_image_never_get_word_copy(body):
    with pytest.raises(IllustrationError):word_image_copy(body)


def test_activity_original_and_word_copy_stored_separately_and_embedded_at_same_index(setup,monkeypatch):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    originals=[noisy_png(1),noisy_png(2)];calls=[]
    async def generate(prompt,record_http):
        content=originals[len(calls)];calls.append(prompt);record_http(200,'FIXTURE_ONLY')
        return content,{**validated_image(content,max_bytes=MAX_ORIGINAL_IMAGE),'response_format':'url'}
    monkeypatch.setattr(service.provider,'generate',generate)
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    result=service.get(actor,plan['id']);assert result['status']=='completed' and len(result['assets'])==2
    document=ActivityPlanDocumentService(service.storage).get(actor.tenant_id,actor.user_id,result['document']['document_id'])
    stored=service.assets(plan['id'])
    with ZipFile(io.BytesIO(document.content)) as archive:
        xml=ET.fromstring(archive.read('word/document.xml'));rels={r.get('Id'):r.get('Target') for r in ET.fromstring(archive.read('word/_rels/document.xml.rels'))}
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
        for index,(public,private,original) in enumerate(zip(result['assets'],stored,originals)):
            actual,_=service.asset(actor,public['id']);assert actual==original
            assert 'word_storage_key' not in public and 'storage_key' not in public
            assert public['position']==index and public['sha256']==hashlib.sha256(original).hexdigest()
            assert private['storage_key']!=private['word_storage_key']
            word=service.storage.get(private['word_storage_key']);assert len(word)<=MAX_IMAGE
            assert hashlib.sha256(word).hexdigest()==public['word_variant']['sha256']
            rows=[row for row in xml.findall('.//w:tr',ns) if public['label'] in ''.join(row.itertext())];assert len(rows)==1
            blip=rows[0].findall('w:tc',ns)[3].find('.//a:blip',ns);assert blip is not None
            target=rels[blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')]
            assert archive.read('word/'+target)==word
    service.finish_export(actor,plan['id']);assert len(calls)==2 and old==[] and budget.summary()['image']['requests']==0


def test_word_copy_failed_readback_stops_before_next_provider_request(setup,monkeypatch):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor);plan=service.plan(actor,task['id']);calls=[]
    body=noisy_png(9)
    async def generate(prompt,record_http):
        calls.append(prompt);record_http(200,'FIXTURE_ONLY');return body,validated_image(body,max_bytes=MAX_ORIGINAL_IMAGE)
    monkeypatch.setattr(service.provider,'generate',generate)
    original_get=service.storage.get
    def get(key):
        content=original_get(key)
        return b'FIXTURE_ONLY_CORRUPTION' if key.endswith('.jpg') else content
    monkeypatch.setattr(service.storage,'get',get)
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    result=service.get(actor,plan['id'])
    assert result['status']=='failed' and result['error_code']=='ILLUSTRATION_STORAGE_FAILED'
    assert len(calls)==1 and result['assets']==[] and 'document' not in result
    with service.db() as c:
        row=c.execute('select status,asset_json from illustration_requests where plan_id=? and item_index=0',(plan['id'],)).fetchone()
    import json
    evidence=json.loads(row['asset_json']);assert row['status']=='attempted'
    assert evidence['sha256']==hashlib.sha256(body).hexdigest() and original_get(evidence['storage_key'])==body
