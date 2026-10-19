"""Small authenticated Preview ASGI adapter; never installed on Production."""
import json
import re
from starlette.requests import Request
from starlette.responses import JSONResponse,Response

from app.preview_illustration_provider import IllustrationError
from app.preview_illustration_service import PREFIX


async def illustration_request(service,actor,scope,receive,send):
    path=scope['path'];method=scope['method'];request=Request(scope,receive)
    try:
        if method=='POST':
            if request.headers.get('origin')!=str(request.base_url).rstrip('/'):raise PermissionError()
            raw=bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw)>4096:raise ValueError()
            body=json.loads(raw)
            if not isinstance(body,dict):raise ValueError()
        if path==PREFIX+'/capability' and method=='GET':result=service.capability(actor)
        elif path==PREFIX+'/plans' and method=='POST':
            if set(body)!={'task_id'} or not isinstance(body['task_id'],str):raise ValueError()
            result=service.plan(actor,body['task_id'])
        elif (match:=re.fullmatch(re.escape(PREFIX)+r'/tasks/([^/]+)',path)) and method=='GET':
            result={'plan':service.current(actor,match[1])}
        elif (match:=re.fullmatch(re.escape(PREFIX)+r'/plans/([0-9a-f]{32})/successor',path)) and method=='POST':
            if set(body)!={'confirmed','digest'}:raise ValueError()
            result=service.successor(actor,match[1],body['digest'],body['confirmed'])
        elif (match:=re.fullmatch(re.escape(PREFIX)+r'/plans/([0-9a-f]{32})/generate',path)) and method=='POST':
            if set(body)!={'confirmed','digest'}:raise ValueError()
            result=service.confirm(actor,match[1],body['digest'],body['confirmed'])
            if result['status']=='queued':service.schedule(actor,match[1])
            return await JSONResponse(result,202)(scope,receive,send)
        elif (match:=re.fullmatch(re.escape(PREFIX)+r'/plans/([0-9a-f]{32})',path)) and method=='GET':result=service.get(actor,match[1])
        elif (match:=re.fullmatch(re.escape(PREFIX)+r'/plans/([0-9a-f]{32})/export',path)) and method=='POST':
            if body!={}:raise ValueError()
            # Export recovery ONLY, never resubmit a Provider request.
            service.row(actor,match[1]);service.finish_export(actor,match[1]);result=service.get(actor,match[1])
        elif (match:=re.fullmatch(re.escape(PREFIX)+r'/assets/([0-9a-f]{32})',path)) and method=='GET':
            content,asset=service.asset(actor,match[1])
            return await Response(content,media_type=asset['mime_type'],headers={
                'content-disposition':'inline; filename="'+asset['filename']+'"','x-content-type-options':'nosniff',
                'cache-control':'private, no-store'})(scope,receive,send)
        elif (match:=re.fullmatch(re.escape(PREFIX)+r'/plans/([0-9a-f]{32})/(html|package)',path)) and method=='GET':
            package=match[2]=='package';content=service.export_article(actor,match[1],package)
            return await Response(content,media_type='application/zip' if package else 'text/html',headers={
                'content-disposition':'attachment; filename="article-images.zip"' if package else 'attachment; filename="article.html"',
                'x-content-type-options':'nosniff','cache-control':'private, no-store'})(scope,receive,send)
        else:raise LookupError()
        return await JSONResponse(result)(scope,receive,send)
    except LookupError:
        response=JSONResponse({'user_message':'配图任务或资产不存在或无权访问。'},404)
    except PermissionError:
        response=JSONResponse({'user_message':'当前用户、智能体或操作确认无效。'},403)
    except IllustrationError as error:
        response=JSONResponse({'error_code':error.code,'user_message':str(error)},503 if error.code in {'ILLUSTRATION_NOT_CONFIGURED','ILLUSTRATION_BUDGET_PENDING'} else 409)
    except (ValueError,TypeError,KeyError):
        response=JSONResponse({'user_message':'配图请求无效；不接受任意HTML、Tenant、路径或密钥。'},422)
    except Exception:
        response=JSONResponse({'error_code':'ILLUSTRATION_INTERNAL_ERROR','user_message':'配图处理暂不可用；正文及纯文字导出不受影响。'},500)
    return await response(scope,receive,send)
