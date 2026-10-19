"""FIXTURE_ONLY: OSS download/HTTP/SQLite/DOCX/ZIP, no real network or billing."""
import asyncio
from dataclasses import replace
import hashlib
import io
import json
from types import SimpleNamespace
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import httpx
import pytest

from app.preview_illustration_provider import download_vetted_image,IllustrationError,MAX_IMAGE,validated_image
from app.preview_illustration_routes import illustration_request
from app.preview_illustration_service import PREFIX
from app.document_generator import ActivityPlanDocumentService
from test_preview_illustrations import setup,image_fixture,completed_task,picture

HOST='dashscope-463f.oss-accelerate.aliyuncs.com'
URL='https://'+HOST+'/FIXTURE_ONLY.png?Expires=FIXTURE_ONLY'


@pytest.fixture
def oss(monkeypatch):
    state=SimpleNamespace(dns=['8.8.8.8'],connections=[],requests=[],sni=[],reads=[],closed=0,
                          bodies=[picture()],status=200,mime='image/png',length=None,tls_fail=False)
    def resolve(host,port,**kwargs):
        assert host==HOST and port==443
        return [(2,1,6,'',(ip,443)) for ip in state.dns]
    class Socket:
        def close(self):state.closed+=1
    def connect(address,timeout):state.connections.append((address,timeout));return Socket()
    class Context:
        def wrap_socket(self,raw,server_hostname):
            state.sni.append(server_hostname)
            if state.tls_fail:raise ValueError('FIXTURE_ONLY certificate mismatch')
            return raw
    class Response:
        @property
        def status(self):return state.status
        def getheader(self,name,default=''):
            return {'content-length':state.length if state.length is not None else str(len(state.bodies[0])),
                    'content-type':state.mime,'location':'https://127.0.0.1/FIXTURE_ONLY'}.get(name,default)
        def read(self,maximum):
            state.reads.append(maximum);body=state.bodies.pop(0);return body[:maximum]
    def request(client,method,target,headers):
        state.requests.append({'method':method,'target':target,'headers':headers,'host':client.host});client.connect()
    monkeypatch.setattr('socket.getaddrinfo',resolve)
    monkeypatch.setattr('socket.create_connection',connect)
    monkeypatch.setattr('ssl.create_default_context',lambda:Context())
    monkeypatch.setattr('http.client.HTTPSConnection.request',request)
    monkeypatch.setattr('http.client.HTTPSConnection.getresponse',lambda client:Response())
    return state


def test_oss_exact_host_pinned_public_ip_sni_no_authorization_and_hash(oss):
    expected=oss.bodies[0];body=download_vetted_image(URL,(HOST,))
    assert body==expected and validated_image(body)['sha256']==hashlib.sha256(expected).hexdigest()
    assert oss.connections==[(('8.8.8.8',443),20)] and oss.sni==[HOST]
    assert oss.requests==[{'method':'GET','target':'/FIXTURE_ONLY.png?Expires=FIXTURE_ONLY','headers':{'Accept':'image/png,image/jpeg'},'host':HOST}]
    assert oss.reads==[MAX_IMAGE+1] and oss.closed==1


@pytest.mark.parametrize('url',['https://dashscope-other.oss-accelerate.aliyuncs.com/a.png',
    'http://'+HOST+'/a.png','https://'+HOST+'.evil.invalid/a.png','https://user:secret@'+HOST+'/a.png'])
def test_oss_unverified_bucket_and_non_https_never_connect(oss,url):
    with pytest.raises(IllustrationError) as error:download_vetted_image(url,(HOST,))
    assert error.value.code=='ILLUSTRATION_URL_BLOCKED' and not oss.connections and not oss.requests


@pytest.mark.parametrize('addresses',[['127.0.0.1'],['10.0.0.1'],['169.254.169.254'],['::1'],['8.8.8.8','192.168.1.1']])
def test_oss_private_or_mixed_dns_never_connects(oss,addresses):
    oss.dns=addresses
    with pytest.raises(IllustrationError):download_vetted_image(URL,(HOST,))
    assert not oss.connections and not oss.requests


def test_oss_certificate_failure_closes_raw_socket_and_never_reads(oss):
    oss.tls_fail=True
    with pytest.raises(IllustrationError):download_vetted_image(URL,(HOST,))
    assert oss.sni==[HOST] and oss.closed==1 and not oss.reads


@pytest.mark.parametrize('status',[301,302,307,308,403])
def test_oss_redirect_and_non_success_never_follow_location(oss,status):
    oss.status=status
    with pytest.raises(IllustrationError) as error:download_vetted_image(URL,(HOST,))
    assert error.value.code=='ILLUSTRATION_DOWNLOAD_FAILED' and error.value.safe_details=={'failure_phase':'asset_http','http_status':status}
    assert len(oss.requests)==len(oss.connections)==1 and not oss.reads


@pytest.mark.parametrize('mime',['','text/html','application/octet-stream','application/xml'])
def test_oss_missing_or_wrong_mime_refused_before_read(oss,mime):
    oss.mime=mime
    with pytest.raises(IllustrationError) as error:download_vetted_image(URL,(HOST,))
    assert error.value.code=='ILLUSTRATION_IMAGE_INVALID' and not oss.reads


def test_oss_actual_format_must_match_declared_mime(oss):
    oss.mime='image/jpeg'
    with pytest.raises(IllustrationError) as error:download_vetted_image(URL,(HOST,))
    assert error.value.code=='ILLUSTRATION_IMAGE_INVALID'


@pytest.mark.parametrize('header_declares_size',[True,False])
def test_oss_over_two_mb_is_rejected_not_truncated_into_valid_image(oss,header_declares_size):
    oss.bodies=[picture()+b'X'*MAX_IMAGE];oss.length=str(len(oss.bodies[0])) if header_declares_size else '0'
    with pytest.raises(IllustrationError) as error:download_vetted_image(URL,(HOST,))
    assert error.value.code=='ILLUSTRATION_IMAGE_INVALID'
    assert oss.reads==([] if header_declares_size else [MAX_IMAGE+1])


@pytest.mark.parametrize('kind',['activity','wechat'])
def test_oss_fixture_http_download_store_readback_and_actual_docx_or_html_zip(setup,monkeypatch,oss,kind):
    service,actors,original_calls,old_budget=setup;actor=actors[0]
    service.config=replace(service.config,asset_hosts=(HOST,));service.provider.config=service.config
    task=completed_task(service,actor,kind=kind);plan=service.plan(actor,task['id'])
    expected=[picture(color) for color in ('blue','orange','green')][:plan['count']];oss.bodies=expected.copy()
    provider_requests=[]
    def provider(request):
        provider_requests.append(request)
        return httpx.Response(200,json={'data':[{'url':URL}]},headers={'x-request-id':'FIXTURE_ONLY_ID'})
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kwargs:httpx.MockTransport(provider))
    async def execute():
        async def app(scope,receive,send):await illustration_request(service,actor,scope,receive,send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://fixture.invalid') as client:
            accepted=await client.post(PREFIX+'/plans/'+plan['id']+'/generate',headers={'Origin':'https://fixture.invalid'},json={'confirmed':True,'digest':plan['digest']})
            assert accepted.status_code==202;await asyncio.gather(*service.jobs)
            done=(await client.get(PREFIX+'/plans/'+plan['id'])).json();assert done['status']=='completed'
            for asset,body in zip(done['assets'],expected):
                response=await client.get(asset['url']);assert response.status_code==200 and response.content==body
                assert asset['sha256']==hashlib.sha256(body).hexdigest() and asset['response_format']=='url'
            if kind=='activity':
                document=ActivityPlanDocumentService(service.storage).get(actor.tenant_id,actor.user_id,done['document']['document_id'])
                with ZipFile(io.BytesIO(document.content)) as archive:
                    media=[archive.read(name) for name in archive.namelist() if name.startswith('word/media/')]
                    assert {hashlib.sha256(b).hexdigest() for b in media}=={hashlib.sha256(b).hexdigest() for b in expected}
                    rels=ET.fromstring(archive.read('word/_rels/document.xml.rels'))
                    assert all(r.get('TargetMode')!='External' for r in rels if r.get('Type','').endswith('/image'))
                    document_xml=ET.fromstring(archive.read('word/document.xml'));text=''.join(document_xml.itertext())
                    source=service.source(actor,task['id'])['structured_result']['data']
                    assert source['document_title'] in text and source['invitation_copy'] in text
                    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
                    by_id={r.get('Id'):r.get('Target') for r in rels}
                    for asset in done['assets']:
                        item=source['activity_items'][asset['position']]
                        rows=[row for row in document_xml.findall('.//w:tr',ns) if item['name'] in ''.join(row.itertext())]
                        assert len(rows)==1 and item['description'] in ''.join(rows[0].itertext())
                        blip=rows[0].findall('w:tc',ns)[3].find('.//a:blip',ns);assert blip is not None
                        target=by_id[blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')]
                        assert hashlib.sha256(archive.read('word/'+target)).hexdigest()==asset['sha256']
            else:
                html=await client.get(done['html_url']);package=await client.get(done['package_url'])
                assert html.status_code==package.status_code==200
                with ZipFile(io.BytesIO(package.content)) as archive:
                    content=archive.read('article.html').decode()
                    for asset,body in zip(done['assets'],expected):
                        assert 'images/'+asset['filename'] in content and archive.read('images/'+asset['filename'])==body
                    assert '<script' not in content and '/api/v1/storage/' not in content
            await client.get(PREFIX+'/tasks/'+task['id'])
            assert len(provider_requests)==plan['count']
    asyncio.run(execute())
    assert all(set(r['headers'])=={'Accept'} for r in oss.requests)
    assert len(oss.sni)==plan['count'] and original_calls==[] and old_budget.summary()['image']['requests']==0
