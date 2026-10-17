from email.message import Message
from io import BytesIO
import json
from urllib.request import HTTPSHandler,build_opener
from urllib.response import addinfourl
import pytest
import nexus.public_data as public
from test_public_data import NOW,payload


@pytest.mark.parametrize('code',[301,302,303,307,308])
@pytest.mark.parametrize('location',[
    'https://example.invalid/quote',
    'http://api.exchange.coinbase.com/products/BTC-USD/ticker',
    '/products/ETH-USD/ticker'])
def test_real_redirect_handler_never_requests_redirect_target(monkeypatch,code,location):
    calls=[];responses=[]
    class Transport(HTTPSHandler):
        def https_open(self,req):
            calls.append(req.full_url)
            headers=Message();headers['Location']=location
            response=addinfourl(BytesIO(b''),headers,req.full_url,code)
            response.msg='Redirect';responses.append(response)
            return response
    monkeypatch.setattr(public,'_opener',build_opener(public._NoRedirect(),Transport()))
    monkeypatch.setattr(public,'_cache',{})
    with pytest.raises(ValueError,match='Public market quote unavailable or invalid'):
        public.read_ticker('BTC-USD',now=NOW)
    assert calls==[public.BASE+'BTC-USD/ticker']
    assert all(response.closed for response in responses)
    assert not public._cache


@pytest.mark.parametrize('url,status,body',[
    ('https://example.invalid/quote',200,b'{}'),
    (public.BASE+'ETH-USD/ticker',200,b'{}'),
    (public.BASE+'BTC-USD/ticker',503,b'{}'),
    (public.BASE+'BTC-USD/ticker',200,b'x'*65537),
    (public.BASE+'BTC-USD/ticker',200,b'not json')],
    ids=['foreign-response-url','wrong-product-response-url','provider-unavailable','oversized-body','malformed-json'])
def test_default_transport_rejects_response_without_caching(monkeypatch,url,status,body):
    reads=[]
    class Response:
        def geturl(self):return url
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):reads.append(limit);return body[:limit]
    response=Response();response.status=status
    monkeypatch.setattr(public._opener,'open',lambda *args,**kwargs:response)
    monkeypatch.setattr(public,'_cache',{})
    with pytest.raises(ValueError,match='Public market quote unavailable or invalid'):
        public.read_ticker('BTC-USD',now=NOW)
    assert not public._cache
    if url!=public.BASE+'BTC-USD/ticker' or status!=200:assert not reads
    else:assert reads==[65537]


def test_default_get_only_request_is_exact_and_success_cached(monkeypatch):
    calls=[]
    class Response:
        status=200
        def geturl(self):return public.BASE+'BTC-USD/ticker'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return json.dumps(payload()).encode()
    def open_request(req,timeout):
        calls.append((req.full_url,req.get_method(),req.data,timeout));return Response()
    monkeypatch.setattr(public._opener,'open',open_request)
    monkeypatch.setattr(public,'_cache',{})
    first=public.read_ticker('BTC-USD',now=NOW)
    second=public.read_ticker('BTC-USD',now=NOW+1)
    assert first==second
    assert calls==[(public.BASE+'BTC-USD/ticker','GET',None,4)]
    assert first['provider']=='coinbase_exchange_public' and not first['orders_allowed']


@pytest.mark.parametrize('holding',[False,True])
def test_redirect_failure_pauses_actual_runner_without_inventing_fills(tmp_path,monkeypatch,holding):
    from test_paper_forward import setup,warmup
    r,g,c=setup(tmp_path,monkeypatch)
    if holding:assert warmup(r,c)['decision']=='BUY'
    before=g.recovery_report();calls=[]
    def redirect(*args,**kwargs):
        calls.append(args[0].full_url)
        raise ValueError('Public market redirects disabled')
    monkeypatch.setattr(public._opener,'open',redirect)
    monkeypatch.setattr(public,'_cache',{})
    r.fetcher=public.read_ticker
    for _ in range(3):
        c.now+=5;result=r.single_cycle()
    assert result['decision']=='PAUSED' and result['reason']=='QUOTE_UNAVAILABLE_OR_INVALID'
    assert not r.status()['enabled'] and not r.status()['ownership_held']
    after=g.recovery_report()
    assert before['commands']==after['commands']
    assert before['execution_account']==after['execution_account']
    assert after['portfolio_reconciled']
    assert bool(after['execution_account']['account']['positions'])==holding
    evidence=r.evidence_report(r.status()['session'])
    assert evidence['local_consistency_ok']
    assert evidence['quote_evidence']['rejected_observations']==3
    assert r.single_cycle()['reason']=='PAPER_FORWARD_STOPPED' and len(calls)==3
