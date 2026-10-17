"""Local, bounded optional-AI secret controls; no provider requests or order authority."""
import asyncio
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from nexus import credential_vault
from nexus.paper_onboarding import _body

BASE = '/api/paper/ai-provider/vault'
HEADERS = {'Cache-Control':'no-store', 'Pragma':'no-cache'}


def _reply(data, status=200):
    return JSONResponse(data, status_code=status, headers=HEADERS)


def register(app, local_gate):
    def endpoint(action):
        async def handle(request: Request):
            try: local_gate(request)  # Reject before reading any secret-bearing body.
            except HTTPException: return _reply({'detail':'LOCAL_AI_PORTAL_REQUIRED'},403)
            try:
                if action == 'disconnect' and not request.headers.get('content-type'):
                    # Preserve the original empty-body disconnect contract.
                    if request.headers.get('content-length') not in (None, '0'): raise ValueError()
                    async for chunk in request.stream():
                        if chunk: raise ValueError()
                    data = {}
                else:
                    data = await _body(request)  # Shared 2 KiB, duplicate-free JSON parser.
                if action == 'connect':
                    if set(data) != {'api_key'}: raise ValueError()
                    credential_vault.validate_secret(data['api_key'])
                elif data: raise ValueError()
            except Exception:
                return _reply({'detail':'AI_ONBOARDING_BODY_INVALID'},422)
            try:
                if action == 'connect':
                    result = await asyncio.to_thread(credential_vault.install,data['api_key'])
                else:
                    result = await asyncio.to_thread(credential_vault.disconnect)
                return _reply(result)
            except Exception:
                return _reply({'detail':'AI_ONBOARDING_ACTION_UNAVAILABLE'},503)
        handle.__name__ = 'local_ai_credentials_' + action
        return handle
    for action in ('connect','disconnect'):
        app.add_api_route(BASE+'/'+action,endpoint(action),methods=['POST'])
