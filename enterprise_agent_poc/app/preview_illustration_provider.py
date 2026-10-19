"""Independent qwen adapter. No inherited image key or automatic retry."""
import asyncio
import base64
from dataclasses import dataclass,field
from decimal import Decimal
import hashlib
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
from urllib.parse import urlsplit

import httpx
from PIL import Image

MODEL='qwen-image-3.0'
MAX_IMAGE=2*1024*1024  # Existing Word image contract, checked before storage.


class IllustrationError(ValueError):
    def __init__(self,code,message,safe_details=None):
        super().__init__(message);self.code=code;self.safe_details=safe_details or {}


def private_file(path):
    path=Path(path)
    if path.is_symlink():raise IllustrationError('ILLUSTRATION_CONFIG_INVALID','配图配置必须是独立受限文件。')
    if os.name!='nt' and stat.S_IMODE(path.stat().st_mode)&0o077:
        raise IllustrationError('ILLUSTRATION_CONFIG_INVALID','配图配置文件权限必须为0600。')
    return path


@dataclass(frozen=True)
class IllustrationConfig:
    key:str=field(default='',repr=False)
    enabled:bool=False
    base_url:str='https://api.n1n.ai'
    model:str=MODEL
    size:str='1024x1024'
    asset_hosts:tuple=()

    @classmethod
    def load(cls,path):
        if not Path(path).is_file():return cls()
        values={}
        for line in private_file(path).read_text(encoding='utf-8-sig').splitlines():
            key,separator,value=line.partition('=')
            if separator and key.startswith('N1N_ILLUSTRATION_'):values[key]=value.strip()
        expected={'BASE_URL':'https://api.n1n.ai','MODEL':MODEL,'DEFAULT_SIZE':'1024x1024',
                  'WATERMARK':'true','PROMPT_EXTEND':'true','PROMPT_EXTEND_MODE':'direct','SORT':'success_rate'}
        if any(values.get('N1N_ILLUSTRATION_'+key,value)!=value for key,value in expected.items()):
            raise IllustrationError('ILLUSTRATION_CONFIG_INVALID','配图模型或固定参数不符合本轮合同。')
        hosts=tuple(filter(None,values.get('N1N_ILLUSTRATION_ASSET_HOSTS','').split(',')))
        if any(not re.fullmatch(r'[a-z0-9.-]+',h) or '*' in h for h in hosts):raise IllustrationError('ILLUSTRATION_CONFIG_INVALID','图片资源域名合同无效。')
        return cls(key=values.get('N1N_ILLUSTRATION_API_KEY',''),enabled=values.get('N1N_ILLUSTRATION_ENABLED','false')=='true',asset_hosts=hosts)


def validated_image(content,mime=None):
    if not content or len(content)>MAX_IMAGE:raise IllustrationError('ILLUSTRATION_IMAGE_INVALID','配图为空或超过当前Word支持的2MB限制。')
    try:
        with Image.open(io.BytesIO(content)) as image:
            actual=image.format;width,height=image.size
            if width<64 or height<64 or max(width,height)>4096 or width*height>16777216:raise ValueError()
            image.verify()
        with Image.open(io.BytesIO(content)) as image:image.load()
    except Exception:raise IllustrationError('ILLUSTRATION_IMAGE_INVALID','返回文件不是有效图片。') from None
    if actual not in {'PNG','JPEG'} or width<64 or height<64 or max(width,height)>4096 or width*height>16777216:
        raise IllustrationError('ILLUSTRATION_IMAGE_INVALID','配图格式或实际像素不符合支持范围。')
    extension,actual_mime=('.png','image/png') if actual=='PNG' else ('.jpg','image/jpeg')
    if mime and mime.split(';')[0].strip()!=actual_mime:raise IllustrationError('ILLUSTRATION_IMAGE_INVALID','配图MIME与实际格式不一致。')
    return {'extension':extension,'mime_type':actual_mime,'width':width,'height':height,'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)}


def download_vetted_image(url,allowed_hosts):
    """Exact approved host, public-IP pin, TLS SNI verification, no redirects."""
    try:parsed=urlsplit(url);port=parsed.port
    except (ValueError,TypeError):raise IllustrationError('ILLUSTRATION_URL_BLOCKED','图片资源地址合同未通过校验。') from None
    if (parsed.scheme!='https' or parsed.hostname not in allowed_hosts or parsed.username or parsed.password
            or port not in {None,443} or parsed.fragment or len(url)>4096):
        host=parsed.hostname or ''
        raise IllustrationError('ILLUSTRATION_URL_BLOCKED','返回图片域名尚未验证，未下载外部资源。',
                                {'asset_host':host} if re.fullmatch(r'[a-z0-9.-]{1,200}',host) else {})
    try:
        addresses=list({entry[4][0] for entry in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)})
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):raise ValueError()
        # Connect to a validated IP, not a second DNS lookup. No bearer key is
        # forwarded to storage hosts, even if the URL is signed.
        class PinnedHTTPS(http.client.HTTPSConnection):
            def connect(self):
                raw=socket.create_connection((addresses[0],443),timeout=20)
                try:self.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=parsed.hostname)
                except Exception:raw.close();raise
        client=PinnedHTTPS(parsed.hostname,timeout=20)
        try:
            client.request('GET',parsed.path+('?' +parsed.query if parsed.query else ''),headers={'Accept':'image/png,image/jpeg'})
            response=client.getresponse()
            if response.status!=200:raise ValueError()
            if int(response.getheader('content-length','0'))>MAX_IMAGE:raise ValueError()
            content=response.read(MAX_IMAGE+1);mime=response.getheader('content-type','')
            validated_image(content,mime);return content
        finally:client.close()
    except IllustrationError:raise
    except Exception:raise IllustrationError('ILLUSTRATION_DOWNLOAD_FAILED','配图下载或验证失败，未自动重试。') from None


class IllustrationProvider:
    def __init__(self,config):self.config=config

    async def generate(self,prompt,record_http):
        config=self.config
        if not config.enabled or not config.key:raise IllustrationError('ILLUSTRATION_NOT_CONFIGURED','配图模型未配置；纯文字生成与导出仍可使用。')
        body={'prompt':prompt,'model':MODEL,'provider':{'sort':'success_rate'},'size':config.size,
              'watermark':True,'prompt_extend':True,'prompt_extend_mode':'direct'}
        try:
            async with asyncio.timeout(180), httpx.AsyncClient(timeout=httpx.Timeout(180,connect=10),trust_env=False,follow_redirects=False,transport=httpx.AsyncHTTPTransport(retries=0)) as client:
                async with client.stream('POST',config.base_url+'/v1/images/generations?sort=success_rate',
                    headers={'Authorization':'Bearer '+config.key,'Content-Type':'application/json'},json=body) as response:
                    request_id=response.headers.get('x-request-id','')
                    record_http(response.status_code,request_id if re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',request_id) and not re.search('sk-|secret|token|key',request_id,re.I) else '')
                    if response.status_code!=200:raise IllustrationError('ILLUSTRATION_PROVIDER_REJECTED','配图供应商未成功返回；不自动重试。')
                    raw=bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>3*1024*1024:raise IllustrationError('ILLUSTRATION_IMAGE_INVALID','配图响应超过大小上限。')
            value=json.loads(raw);data=value.get('data')
            if not isinstance(data,list) or len(data)!=1 or not isinstance(data[0],dict):raise ValueError()
            picture=data[0]
            if bool(picture.get('b64_json'))==bool(picture.get('url')):raise ValueError()
            if picture.get('b64_json'):
                content=base64.b64decode(picture['b64_json'],validate=True);format_used='b64_json'
            else:
                if not isinstance(picture['url'],str):raise ValueError()
                content=await asyncio.to_thread(download_vetted_image,picture['url'],config.asset_hosts);format_used='url'
            return content,{**validated_image(content),'response_format':format_used}
        except IllustrationError:raise
        except (httpx.TransportError,TimeoutError) as error:
            error_type=type(error).__name__
            phase='connect' if isinstance(error,(httpx.ConnectError,httpx.ConnectTimeout)) else 'response_or_transport'
            raise IllustrationError('ILLUSTRATION_PROVIDER_UNKNOWN','配图网络或结果未确认，保留本次预留，不自动重试。',
                                    {'failure_phase':phase,'error_type':error_type}) from None
        except Exception:raise IllustrationError('ILLUSTRATION_PROVIDER_UNKNOWN','配图结果未确认，保留本次预留，不自动重试。') from None
