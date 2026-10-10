"""Credential-only adapter for the existing WeChat AppID/AppSecret token contract.

Fixed HTTPS token endpoint; no draft/material calls, token cache, proxy, redirects
or HTTP URL logging. Errors are constants, never raw credential-bearing responses.
"""
import http.client
import json
import socket
from urllib.parse import urlencode

ERRORS = {
    'WECHAT_CREDENTIAL_INVALID': 'AppID或AppSecret验证失败',
    'WECHAT_IP_NOT_ALLOWED': '当前服务器IP未加入微信公众号IP白名单',
    'WECHAT_SERVICE_UNAVAILABLE': '微信公众号服务暂时不可用，请稍后重试',
    'WECHAT_CONNECTION_TIMEOUT': '连接微信公众号超时',
}


class WechatConnectionError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else 'WECHAT_SERVICE_UNAVAILABLE'
        super().__init__(self.code)


class WechatCredentialTester:
    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or http.client.HTTPSConnection

    def verify(self, app_id, app_secret):
        connection = None
        try:
            connection = self.connection_factory('api.weixin.qq.com', timeout=10)
            connection.set_debuglevel(0)
            # Same token grant contract as the sealed wechat-html-draft Skill.
            target = '/cgi-bin/token?' + urlencode(dict(grant_type='client_credential', appid=app_id, secret=app_secret))
            connection.request('GET', target, headers={'Accept': 'application/json'})
            response = connection.getresponse()
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise WechatConnectionError('WECHAT_SERVICE_UNAVAILABLE')
            value = json.loads(raw)
            if not isinstance(value, dict): raise ValueError()
            code = str(value.get('errcode', 0))
            if code != '0':
                category = 'WECHAT_IP_NOT_ALLOWED' if code == '40164' else 'WECHAT_CREDENTIAL_INVALID' if code in {'40001','40013','40125','40002'} else 'WECHAT_SERVICE_UNAVAILABLE'
                raise WechatConnectionError(category)
            token = value.get('access_token')
            if not isinstance(token, str) or not token.strip(): raise ValueError()
            # The token is used only as connection proof and is never returned.
            value.clear()
            token = None
        except WechatConnectionError:
            raise
        except (TimeoutError, socket.timeout):
            raise WechatConnectionError('WECHAT_CONNECTION_TIMEOUT') from None
        except Exception:
            raise WechatConnectionError('WECHAT_SERVICE_UNAVAILABLE') from None
        finally:
            if connection:
                try: connection.close()
                except Exception: pass
