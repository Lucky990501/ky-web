"""Read-only integration surface; no Task, credential or network side effects.

Schema/capability approval is still required. Do not replace this gate with an
enabled boolean or wire it to the historical controlled Test PREPARE ticket.
"""
from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import JSONResponse

from app.wechat_prepare_reader import PrepareReadError


def wechat_draft_router(reader, current_user):
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

    @router.get('/prepare')
    def prepare(agent_id: str, message_id: str, workbench_session: str | None = Cookie(default=None)):
        return JSONResponse(owned(workbench_session, agent_id, message_id),
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @router.get('/availability')
    def availability(agent_id: str, message_id: str, workbench_session: str | None = Cookie(default=None)):
        return JSONResponse(pending(owned(workbench_session, agent_id, message_id)), headers={'Cache-Control': 'no-store'})

    @router.post('')
    async def create_draft(request: Request, agent_id: str, message_id: str,
                           workbench_session: str | None = Cookie(default=None)):
        # Auth first. Even a same-origin authenticated request cannot mint an
        # Action until the durable-operation contract is approved and installed.
        current_user(workbench_session)
        if (request.headers.get('origin') != str(request.base_url).rstrip('/') or
                request.headers.get('sec-fetch-site') != 'same-origin' or
                request.headers.get('x-workbench-action') != 'CREATE_DRAFT'):
            raise HTTPException(403, 'WECHAT_ACTION_CSRF_REJECTED')
        # Do not echo or consume any client article, path, tenant or secret.
        article = owned(workbench_session, agent_id, message_id)
        return JSONResponse({**pending(article), 'code': 'CREATE_DRAFT_SCHEMA_APPROVAL_REQUIRED'},
                            status_code=409, headers={'Cache-Control': 'no-store'})

    return router
