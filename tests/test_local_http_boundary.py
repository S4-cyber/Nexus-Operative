"""Actual ASGI/HTTP rejection before body, storage or secret access."""
import asyncio
import pytest
from fastapi.testclient import TestClient
from nexus.local_http_boundary import LocalHTTPBoundary
from nexus.app import app


@pytest.mark.parametrize('headers,client',[
    ([(b'host',b'evil.example')],('127.0.0.1',1)),
    ([(b'host',b'127.0.0.1'),(b'host',b'localhost')],('127.0.0.1',1)),
    ([(b'host',b'localhost:0')],('127.0.0.1',1)),
    ([(b'host',b'localhost:65536')],('127.0.0.1',1)),
    ([(b'host',b'127.0.0.1'),(b'origin',b'https://evil.example')],('127.0.0.1',1)),
    ([(b'host',b'127.0.0.1'),(b'origin',b'null')],('127.0.0.1',1)),
    ([(b'host',b'127.0.0.1'),(b'origin',b'http://127.0.0.1'),(b'origin',b'http://127.0.0.1')],('127.0.0.1',1)),
    ([(b'host',b'127.0.0.1')],('203.0.113.1',1)),
    ([(b'host',b'127.0.0.1'),(b'sec-fetch-site',b'cross-site')],('127.0.0.1',1)),
    ([(b'host',b'127.0.0.1'),(b'x-forwarded-host',b'localhost')],('203.0.113.1',1)),
])
def test_untrusted_request_does_not_read_body_or_run_route(headers,client):
    async def run():
        sent=[]
        async def forbidden(*_):pytest.fail('untrusted request reached route/body')
        async def send(message):sent.append(message)
        await LocalHTTPBoundary(forbidden)({'type':'http','method':'POST','scheme':'http','path':'/api/status','headers':headers,'client':client},forbidden,send)
        assert sent[0]['status']==403 and dict(sent[0]['headers'])[b'cache-control']==b'no-store'
    asyncio.run(run())


def test_valid_local_api_responses_are_private_and_not_frameable():
    response=TestClient(app).get('/api/status')
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.headers['x-frame-options']=='DENY' and response.headers['x-content-type-options']=='nosniff'
    assert response.headers['referrer-policy']=='no-referrer'


def test_real_app_rejects_rebinding_hosts_and_cross_site_posts():
    client=TestClient(app)
    assert client.get('/api/status',headers={'Host':'rebinding.example'}).status_code==403
    assert client.post('/api/paper/emergency-stop',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/status',headers={'Origin':'http://127.0.0.1'}).status_code==200
