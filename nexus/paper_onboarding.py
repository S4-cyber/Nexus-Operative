"""Local-origin, bounded-body paper credential controls. No order routes."""
import asyncio
import json

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from nexus import paper_credentials, paper_portal_probe
from nexus.alpaca_paper_inspection import PaperInspectionError

BASE = '/api/execution/alpaca-paper/local-credentials'
MAX_BODY = 2048
HEADERS = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}
FIELDS = {'save': {'api_key', 'api_secret', 'confirm_store'}, 'remove': {'confirm_remove'},
          'status': set(), 'inspect': {'confirm_read_only_paper', 'expected_account_id'},
          'asset-rules': {'confirm_read_only_paper','expected_account_id'}, 'paced-inspect': {'confirm_read_only_paper','expected_account_id'}, 'pacing-status': {'expected_account_id'}}


def _reply(data, status=200):
    return JSONResponse(data, status_code=status, headers=HEADERS)


async def _body(request):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise ValueError()
    length = request.headers.get('content-length')
    if length is not None and (not length.isascii() or not length.isdecimal() or len(length) > 4 or int(length) > MAX_BODY):
        raise ValueError()
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY:
            raise ValueError()
        raw.extend(chunk)
    data = json.loads(raw.decode('utf-8'), object_pairs_hook=paper_credentials._pairs)
    if not isinstance(data, dict):
        raise ValueError()
    return data


def register(app, local_gate):
    def endpoint(action):
        async def handle(request: Request):
            try:
                local_gate(request)  # Check peer, Host and exact Origin before reading secrets.
            except HTTPException:
                return _reply({'detail': 'LOCAL_PAPER_PORTAL_REQUIRED'}, 403)
            try:
                data = await _body(request)
                if set(data) - FIELDS[action]:
                    raise ValueError()
                if action != 'inspect' and set(data) != FIELDS[action]:
                    raise ValueError()
                if action == 'inspect' and 'confirm_read_only_paper' not in data:
                    raise ValueError()
            except Exception:
                return _reply({'detail': 'PAPER_ONBOARDING_BODY_INVALID'}, 422)
            try:
                operation = {'save': paper_credentials.install, 'remove': paper_credentials.remove,
                             'status': paper_credentials.status, 'inspect': paper_credentials.inspect_saved,
                             'asset-rules':paper_portal_probe.inspect_rules_saved,'paced-inspect':paper_portal_probe.inspect_saved,'pacing-status':paper_portal_probe.status}[action]
                result = await asyncio.to_thread(operation, **data)
                return _reply(result)
            except (paper_credentials.PaperVaultError, PaperInspectionError):
                return _reply({'detail': 'PAPER_ONBOARDING_ACTION_UNAVAILABLE'}, 503)
            except Exception:
                return _reply({'detail': 'PAPER_ONBOARDING_ACTION_UNAVAILABLE'}, 503)
        handle.__name__ = 'local_paper_credentials_' + action
        return handle
    for action in FIELDS:
        app.add_api_route(BASE + '/' + action, endpoint(action), methods=['POST'])
