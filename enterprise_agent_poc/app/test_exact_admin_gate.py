"""Test-only, exact leased administrator request scope. No admin-management API.

Production does not read/import Test authority or change its permission model.
The Test successor must require this gate before any real Grant is allowed.
"""
from __future__ import annotations

import json
import os

from fastapi.responses import JSONResponse, Response

CAPABILITY_PATH = '/api/v1/internal/test/exact-admin-capability'


def install_exact_admin_gate(app, *, environment, store, sessions, service=None, catalog=None, task_service=None):
    if environment != 'test':
        return False
    from scripts.exact_test_admin_lifecycle import ExactTestAdmin, Blocked, CONTRACT, REGISTRATION, need
    mode = os.environ.get(REGISTRATION, 'false')
    need(mode in ('true', 'false'), 'EXACT_ADMIN_REQUIRED_FLAG_INVALID')
    if mode == 'false':
        return False
    service = service or ExactTestAdmin(store._store, environment=environment)
    if catalog is not None:
        from scripts.wechat_runtime_native_successor import NativeSuccessor
        need(catalog.runtime_tester is not None and task_service is not None, 'EXACT_ADMIN_FORMAL_RUNTIME_GATE_REQUIRED')
        checker = NativeSuccessor()
        catalog.runtime_tester.isolation_guard = lambda: checker.verify(for_execution=True)
        task_service.pre_execute_guard = lambda: checker.verify(for_execution=True)

    def principal(request, scope):
        from app.auth import AuthenticationError
        token = request.cookies.get('workbench_session')
        need(bool(token), 'EXACT_ADMIN_AUTHENTICATED_SESSION_REQUIRED')
        try:
            identity = sessions.verify(token)
        except AuthenticationError:
            raise Blocked('EXACT_ADMIN_AUTHENTICATED_SESSION_REQUIRED') from None
        need((identity.user_id, identity.tenant_id) == (scope['principal_id'], scope['tenant_id']),
            'EXACT_ADMIN_AUTHENTICATED_PRINCIPAL_MISMATCH')
        user = store.user_by_id(identity.user_id, identity.tenant_id)
        need(user is not None and user['account_status'] == 'enabled', 'EXACT_ADMIN_ACCOUNT_DISABLED')
        full = store.user_by_email(user['email'])
        need(identity.auth_version is not None and full is not None
            and identity.auth_version == sessions.credential_version(full['password_hash']),
            'EXACT_ADMIN_CREDENTIAL_VERSION_REQUIRED')
        return identity

    @app.middleware('http')
    async def exact_admin_scope(request, call_next):
        path = request.url.path
        protected = path.startswith('/api/v1/platform/') or path == CAPABILITY_PATH
        if not protected:
            return await call_next(request)
        ticket = None
        try:
            scope = service.authority(); who = principal(request, scope)
            run_id = request.headers.get('x-exact-test-admin-run-id')
            need(run_id == scope['run_id'], 'EXACT_ADMIN_RUN_HEADER_REQUIRED')
            if path == CAPABILITY_PATH:
                need(request.method == 'GET', 'EXACT_ADMIN_CAPABILITY_READ_ONLY')
                return JSONResponse({'contract': CONTRACT, 'run_id': scope['run_id'],
                    'source': scope['application_source'], 'tree': scope['application_tree'], 'gate_required': True})
            raw = await request.body()
            need(len(raw) <= 4096, 'EXACT_ADMIN_REQUEST_BODY_TOO_LARGE')
            try: body = json.loads(raw) if raw else None
            except ValueError: raise Blocked('EXACT_ADMIN_REQUEST_JSON_REJECTED') from None
            ticket = service.begin_operation(who, request.method, path, body, run_id)
            response = await call_next(request)
            # Catalog responses are JSON, never SSE. Buffer only this bounded
            # control-plane response to record IDs, never article/secret bodies.
            chunks = []; size = 0
            async for chunk in response.body_iterator:
                size += len(chunk)
                need(size <= 131072, 'EXACT_ADMIN_RESPONSE_TOO_LARGE')
                chunks.append(chunk)
            output = b''.join(chunks); evidence = {}
            if request.method == 'POST' and path.endswith('/test') and response.status_code == 202:
                value = json.loads(output)
                evidence = {k: value[k] for k in ('runtime_test_id', 'task_id', 'status')}
            service.finish_operation(ticket, status_code=response.status_code, evidence=evidence)
            ticket = None
            return Response(content=output, status_code=response.status_code,
                headers=dict(response.headers), media_type=response.media_type, background=response.background)
        except Blocked as exc:
            return JSONResponse({'detail': str(exc)}, status_code=403)
        finally:
            if ticket is not None:
                # A DB/audit failure remains outstanding and needs the protected
                # dead-operation recovery path, never an unaudited DELETE.
                service.finish_operation(ticket, status_code=500)
    return True
