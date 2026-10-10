"""Read-only integration surface; no Task, credential or network side effects.

Schema/capability approval is still required. Do not replace this gate with an
enabled boolean or wire it to the historical controlled Test PREPARE ticket.
"""
from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import JSONResponse

from app.wechat_prepare_reader import PrepareReadError
from app.wechat_draft_operations import DraftOperationError


def wechat_draft_router(reader, current_user, execution=None, enqueue=None):
    router = APIRouter(prefix='/api/v1/agents/{agent_id}/messages/{message_id}/wechat-draft', tags=['wechat-draft'])

    def owned(workbench_session, agent_id, message_id):
        principal = current_user(workbench_session)
        try:
            return reader.read(principal, agent_id, message_id)
        except PrepareReadError:
            raise HTTPException(404, 'WECHAT_PREPARE_NOT_AVAILABLE') from None

    def pending(article):
        return dict(state='BACKEND_PENDING', can_create_draft=False,
                    agent_id=article['agent_id'], message_id=article['message_id'],
                    article_version=article['article_version'])

    def prior(principal,agent_id,message_id):
        if execution is None:return []
        try:
            return [execution.operations.public(row) for row in execution.operations.owned(principal,agent_id,message_id)]
        except DraftOperationError:return []

    def csrf(request):
        if (request.headers.get('origin') != str(request.base_url).rstrip('/') or
                request.headers.get('sec-fetch-site') != 'same-origin' or
                request.headers.get('x-workbench-action') != 'CREATE_DRAFT'):
            raise HTTPException(403,'WECHAT_ACTION_CSRF_REJECTED')

    def result(value,status=200):
        return JSONResponse(value,status_code=status,headers={'Cache-Control':'no-store'})

    @router.get('/prepare')
    def prepare(agent_id: str, message_id: str, workbench_session: str | None = Cookie(default=None)):
        return JSONResponse(owned(workbench_session, agent_id, message_id),
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @router.get('/availability')
    def availability(agent_id: str, message_id: str, workbench_session: str | None = Cookie(default=None)):
        principal=current_user(workbench_session)
        records=prior(principal,agent_id,message_id)
        if records:return result(dict(state=records[0]['state'],can_create_draft=False,prior_receipt=records[0]))
        article=owned(workbench_session,agent_id,message_id)
        if execution is not None:
            try:
                execution.operations.require_schema()
                execution.prepare_binding(principal,agent_id,message_id,article['article_version'])
                return result(dict(state='READY',can_create_draft=True,agent_id=agent_id,message_id=message_id,article_version=article['article_version']))
            except (DraftOperationError,PermissionError,ValueError):pass
        return result(pending(article))

    @router.get('/status')
    def status(agent_id: str,message_id: str,workbench_session: str | None=Cookie(default=None)):
        principal=current_user(workbench_session)
        records=prior(principal,agent_id,message_id)
        if not records:
            owned(workbench_session,agent_id,message_id)
            return result(dict(state='BACKEND_PENDING',prior_receipt=None))
        return result(dict(state=records[0]['state'],prior_receipt=records[0]))

    @router.post('')
    async def create_draft(request: Request, agent_id: str, message_id: str,
                           workbench_session: str | None = Cookie(default=None)):
        # Auth first. Even a same-origin authenticated request cannot mint an
        # Action until the durable-operation contract is approved and installed.
        principal=current_user(workbench_session)
        csrf(request)
        if execution is not None:
            try:
                raw=await request.body()
                if len(raw)>512:raise ValueError()
                import json
                payload=json.loads(raw)
                if set(payload)!={'article_version'} or not isinstance(payload['article_version'],str):raise ValueError()
            except Exception:
                raise HTTPException(422,'WECHAT_CONFIRMATION_INVALID') from None
            try:
                receipt=execution.create(principal,agent_id,message_id,payload['article_version'])
                if enqueue and receipt['state']=='QUEUED':
                    try:enqueue(receipt['task_id'])
                    except Exception:pass  # Durable operation survives enqueue failure.
                return result(receipt,202)
            except (DraftOperationError,PermissionError):
                return result(dict(state='BACKEND_PENDING',code='WECHAT_ACTION_NOT_AUTHORIZED'),409)
        # Do not echo or consume any client article, path, tenant or secret.
        article = owned(workbench_session, agent_id, message_id)
        return JSONResponse({**pending(article), 'code': 'CREATE_DRAFT_SCHEMA_APPROVAL_REQUIRED'},
                            status_code=409, headers={'Cache-Control': 'no-store'})

    @router.post('/reconcile')
    async def reconcile(request:Request,agent_id:str,message_id:str,workbench_session:str | None=Cookie(default=None)):
        principal=current_user(workbench_session);csrf(request)
        if execution is None:raise HTTPException(409,'BACKEND_PENDING')
        rows=execution.operations.owned(principal,agent_id,message_id)
        if len(rows)!=1:raise HTTPException(409,'WECHAT_MANUAL_REVIEW_REQUIRED')
        try:
            row=execution.operations.request_readback(rows[0]['id'],execution.reauthorize)
            if enqueue:enqueue(row['action_task_id'])
            return result(execution.operations.public(row),202)
        except Exception:
            raise HTTPException(409,'WECHAT_MANUAL_REVIEW_REQUIRED') from None

    return router
