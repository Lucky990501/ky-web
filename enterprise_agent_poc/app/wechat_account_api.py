"""Profile account endpoints; no front-end/UI or network connection tester."""
from fastapi import APIRouter, Cookie, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from app.tenant_secret_reference import SecretReferenceError
from app.wechat_action_contract import WechatActionError


def wechat_account_router(service,current_user):
    router=APIRouter(prefix='/api/v1/profile/wechat-account',tags=['profile'])

    def safe(operation):
        try: return operation()
        except (SecretReferenceError,WechatActionError) as error:
            code=str(error)
            known={'WECHAT_SECRET_INPUT_INVALID','WECHAT_CONFIG_PERMISSION_REQUIRED',
                'WECHAT_SECRET_BACKEND_BLOCKED','WECHAT_SECRET_VERSION_CONFLICT',
                'WECHAT_CREDENTIAL_REFERENCE_UNAVAILABLE','WECHAT_ACCOUNT_CONFIG_BLOCKED',
                'TENANT_SECRET_REFERENCE_BLOCKED'}
            if code not in known: code='WECHAT_SECRET_BACKEND_BLOCKED'
            status=403 if code=='WECHAT_CONFIG_PERMISSION_REQUIRED' else 503 if code=='WECHAT_SECRET_BACKEND_BLOCKED' else 409 if code=='WECHAT_SECRET_VERSION_CONFLICT' else 422
            raise HTTPException(status,code) from None
        except Exception: raise HTTPException(503,'WECHAT_SECRET_BACKEND_BLOCKED') from None

    async def body(request):
        # Avoid default Pydantic errors echoing plaintext credential input.
        if request.headers.get('content-type','').split(';')[0]!='application/json': raise HTTPException(422,'WECHAT_SECRET_INPUT_INVALID')
        origin=request.headers.get('origin')
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc!=request.url.netloc: raise HTTPException(403,'WECHAT_CONFIG_PERMISSION_REQUIRED')
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>8192: raise HTTPException(422,'WECHAT_SECRET_INPUT_INVALID')
            raw.extend(chunk)
        try:
            import json
            def unique(pairs):
                result={}
                for key,value in pairs:
                    if key in result: raise ValueError()
                    result[key]=value
                return result
            value=json.loads(raw,object_pairs_hook=unique)
            if not isinstance(value,dict): raise ValueError()
            return value
        except Exception: raise HTTPException(422,'WECHAT_SECRET_INPUT_INVALID') from None

    @router.get('')
    def get_account(workbench_session: str|None=Cookie(default=None)):
        principal=current_user(workbench_session)
        return safe(lambda:service.status(principal))

    @router.get('/verification-status')
    def get_verification(workbench_session: str|None=Cookie(default=None)):
        value=get_account(workbench_session)
        return {key:value[key] for key in ('verification_status','app_secret_configured','secret_version')}

    @router.put('')
    async def save_account(request: Request,workbench_session: str|None=Cookie(default=None)):
        principal=current_user(workbench_session)
        payload=await body(request)
        return await run_in_threadpool(safe,lambda:service.save(principal,payload))

    @router.post('/secret/rotate')
    async def rotate_secret(request: Request,workbench_session: str|None=Cookie(default=None)):
        principal=current_user(workbench_session)
        payload=await body(request)
        return await run_in_threadpool(safe,lambda:service.save(principal,payload,rotate_only=True))

    @router.delete('')
    def revoke_account(request: Request,workbench_session: str|None=Cookie(default=None)):
        principal=current_user(workbench_session)
        origin=request.headers.get('origin')
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc!=request.url.netloc: raise HTTPException(403,'WECHAT_CONFIG_PERMISSION_REQUIRED')
        return safe(lambda:service.revoke(principal))

    return router
