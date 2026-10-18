"""Bounded public exploration cannot expand runtime order authority."""
from urllib.parse import parse_qs,urlsplit
import json
import threading
import pytest
from fastapi.testclient import TestClient
from nexus import found_markets as fm

NOW=1_800_000_000

def catalog(count=8):
    return [dict(id=f'A{n}-USD',quote_currency='USD',base_currency=f'A{n}',status='online',quote_increment='.01') for n in range(count)]

def history(g,now=NOW,count=240):
    end=int(now//g)*g
    return [[end-(count-i)*g,99+i*.1,101+i*.1,100+i*.1,100+i*.1,1_000_000] for i in range(count)]

@pytest.fixture
def worker(monkeypatch):
    clock=[1000,NOW];calls=[]
    def get(url):
        calls.append(url)
        return catalog() if url==fm.URL else history(int(parse_qs(urlsplit(url).query)['granularity'][0]),clock[1])
    w=fm.FoundMarkets(get,lambda:clock[0],lambda:clock[1]);w._admission=lambda:True;w._stop.clear()
    monkeypatch.setattr(w._stop,'wait',lambda *_:w._stop.is_set())
    return w,clock,calls


def test_burst_cap_rotation_shortlist_and_no_authority(worker,tmp_path,monkeypatch):
    w,clock,calls=worker;monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'unused'))
    first=w.step();assert len(calls)==9 and first['fresh_summaries']==4 and len(first['selected'])==4
    assert not (tmp_path/'unused').exists()
    assert all(not first[k] for k in ('policy_changed','paper_orders_enabled','live_orders_enabled','money_movement_enabled','profitability_validated'))
    assert all(len(r['horizons'])==3 and not r['execution_allowed'] for r in first['selected'])
    assert w.step()['requests_attempted']==9
    clock[0]+=900;clock[1]+=900
    second=w.step();assert len(calls)==18 and len(second['selected'])==6 and second['fresh_summaries']==8
    clock[0]+=3601;clock[1]+=3601
    assert w.status()['selected']==[]
    assert w.status()['summaries_retained']==8


@pytest.mark.parametrize('fault',['gap','stale','thin','falling','insufficient'])
def test_council_vetoes_bad_evidence_without_filling_shortlist(fault):
    h=fm.bars(history(3600),3600,NOW);d=fm.bars(history(86400),86400,NOW)
    if fault=='gap':h.pop(-3);d.pop(-3)
    elif fault=='stale':h=h[:-4];d=d[:-4]
    elif fault=='thin':d=[(t,c,0) for t,c,v in d]
    elif fault=='falling':h=[(t,1000-c,v) for t,c,v in h];d=[(t,1000-c,v) for t,c,v in d]
    else:h=h[:2];d=d[:2]
    assert all(not r['eligible_for_research'] and r['reasons'] for r in fm.council_screen(h,d,NOW))


def test_request_budget_cannot_be_reset_by_toggle_and_errors_are_fixed(worker):
    w,clock,calls=worker
    w._requests=[clock[0]]*fm.HOURLY_REQUESTS
    assert w.step()['reason']=='SOURCE_OR_BUDGET_REVIEW_REQUIRED' and not calls
    w.stop();w._stop.clear();w._next=0
    assert w.step()['requests_attempted']==0 and not calls
    w.transport=lambda _:(_ for _ in ()).throw(ValueError('PRIVATE_SOURCE_MESSAGE'))
    clock[0]+=3601;clock[1]+=3601
    result=w.step();assert 'PRIVATE' not in json.dumps(result) and not result['selected']


def test_stop_during_request_fences_publication_and_future_requests(worker):
    w,clock,calls=worker;original=w.transport
    def stop_get(url):
        value=original(url)
        if '/candles?' in url:w.stop()
        return value
    w.transport=stop_get
    result=w.step();assert result['selected']==[] and not result['enabled'] and len(calls)==2
    w.step();assert len(calls)==2


def test_later_market_failure_cannot_publish_partial_burst(worker):
    w,clock,calls=worker;original=w.transport
    def fail_second_market(url):
        if len(calls)==3:raise OSError('private upstream detail')
        return original(url)
    w.transport=fail_second_market
    result=w.step()
    assert len(calls)==3 and result['summaries_retained']==0 and result['reviewed']==[]
    assert result['selected']==[] and not result['selection_current']
    assert result['reason']=='SOURCE_OR_BUDGET_REVIEW_REQUIRED'


def test_failed_refresh_hides_prior_shortlist_until_complete_burst(worker):
    w,clock,calls=worker;original=w.transport
    first=w.step();assert first['selection_current'] and first['selected']
    retained=dict(w._rows)
    clock[0]+=900;clock[1]+=900;w._next=0
    w.transport=lambda url: (_ for _ in ()).throw(OSError('private failure')) if url==fm.URL else original(url)
    failed=w.step()
    assert w._rows==retained and failed['reviewed'] and failed['selected']==[] and not failed['selection_current']
    w.transport=original;clock[0]+=900;clock[1]+=900;w._next=0
    recovered=w.step()
    assert recovered['selection_current'] and recovered['selected'] and recovered['reason'] is None


def test_empty_catalog_cannot_revalidate_prior_shortlist(worker):
    w,clock,calls=worker;w.step()
    clock[0]+=900;clock[1]+=900;w._next=0;w.transport=lambda _:[]
    result=w.step()
    assert result['catalog_candidates']==0 and result['reviewed']
    assert result['selected']==[] and not result['selection_current']
    assert result['reason']=='SOURCE_OR_BUDGET_REVIEW_REQUIRED'


def test_stop_after_first_market_cannot_publish_partial_burst(worker):
    w,clock,calls=worker;original=w.transport
    def stop_on_second_market(url):
        value=original(url)
        if len(calls)==4:w.stop()
        return value
    w.transport=stop_on_second_market
    result=w.step()
    assert len(calls)==4 and result['summaries_retained']==0 and result['reviewed']==[]
    assert result['selected']==[] and not result['selection_current'] and not result['enabled']


def test_duplicate_or_malformed_prices_are_rejected():
    rows=history(3600)
    for value in (True,'NaN','Infinity','0','1e-300'):
        bad=[list(r) for r in rows];bad[-1][4]=value
        with pytest.raises(ValueError):fm.bars(bad,3600,NOW)
    with pytest.raises(ValueError):fm.bars(rows+[rows[-1]],3600,NOW)


def test_clock_jump_removes_selections_and_cannot_publish(worker):
    w,clock,calls=worker;w.step();clock[1]+=10000
    assert w.status()['selected']==[]
    assert w.step()['selected']==[] and len(calls)==9


def test_transport_rejects_foreign_paths_and_redirects():
    for url in ('https://example.com/products','http://api.exchange.coinbase.com/products',fm.URL+'/BTC-USD/orders'):
        with pytest.raises(ValueError):fm.public_get(url)
    with pytest.raises(ValueError):fm._NoRedirect().redirect_request(None,None,None,None,None,None)


def test_get_checklist_sections_are_passive_and_start_requires_portal(tmp_path,monkeypatch):
    from nexus import app as backend
    from nexus import startup_tasks
    w=fm.FoundMarkets(lambda _:pytest.fail('GET requested public data'))
    monkeypatch.setattr(backend,'found_market_worker',w);monkeypatch.setattr(fm,'worker',w)
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'unused'));monkeypatch.setattr(backend,'_paused',True)
    client=TestClient(backend.app)
    response=client.get('/api/research/found-markets');assert response.status_code==200 and response.headers['cache-control']=='no-store'
    items={x['id']:x for x in startup_tasks.checklist()['items']}
    assert items['found_markets']['necessity']=='OPTIONAL' and not items['found_markets']['required_for_baseline']
    assert {k for k,v in items.items() if v['required_for_baseline']}=={'portal','storage','budget','tier','portfolio','quotes'}
    assert all(items[k]['necessity']=='FUTURE' for k in ('payment','broker','cloud'))
    assert client.post('/api/research/found-markets/control',json={'enabled':True}).status_code==409
    assert client.post('/api/research/found-markets/control',json={'enabled':False}).status_code==200
    assert not (tmp_path/'unused').exists()


def test_actual_optional_discovery_controller():
    from pathlib import Path
    import shutil,subprocess
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual controller')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/found_markets_smoke.cjs')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_real_worker_stop_during_catalog_prevents_followup_and_restart():
    entered=threading.Event();finish=threading.Event();calls=[]
    def get(url):
        calls.append(url);entered.set();assert finish.wait(2);return catalog()
    w=fm.FoundMarkets(get)
    try:
        w.start(lambda:True);assert entered.wait(2)
        w.stop()
        with pytest.raises(ValueError):w.start(lambda:True)
    finally:finish.set();w.shutdown()
    assert calls==[fm.URL] and not w.status()['enabled'] and not w.status()['selected']


def test_summary_retention_does_not_grow_with_catalog(worker):
    w,clock,calls=worker
    for i in range(fm.RETAINED):
        w._rows[f'Z{i}-USD']=dict(product=f'Z{i}-USD',observed_mono=0,observed_at=NOW-10000,horizons=[],execution_allowed=False)
    assert w.step()['summaries_retained']==fm.RETAINED
    assert 'Z0-USD' not in w._rows and len(calls)==9


def test_portal_defaults_do_not_start_optional_discovery_for_legacy_callers(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from nexus import app as backend
    started=[];stopped=[]
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path/'unused'));monkeypatch.setattr(backend,'_paused',True)
    monkeypatch.setattr(backend,'observer',SimpleNamespace(start=lambda:None,stop=lambda:None))
    monkeypatch.setattr(backend,'refresh_worker',SimpleNamespace(start=lambda *a,**k:None,stop=lambda *_:None))
    monkeypatch.setattr(backend,'paper_forward_runner',SimpleNamespace(stop=lambda *_:None))
    monkeypatch.setattr(backend,'audit',lambda *_:None)
    monkeypatch.setattr(backend,'found_market_worker',SimpleNamespace(start=lambda guard:started.append(guard()),stop=lambda reason:stopped.append(reason)))
    client=TestClient(backend.app)
    assert client.post('/api/portal',json={'enabled':True}).status_code==200 and started==[]
    assert client.get('/api/onboarding/tasks').json()['items'][0]['checked'] is True
    assert client.post('/api/portal',json={'enabled':False}).status_code==200
    assert client.post('/api/portal',json={'enabled':True,'discover_markets':True}).status_code==200 and started==[True]
    assert client.post('/api/portal',json={'enabled':False}).status_code==200 and stopped==['PORTAL_PAUSE','PORTAL_PAUSE']


def test_council_vetoes_do_not_take_priority_revisit_slots(worker):
    w,clock,calls=worker;original=w.transport
    def thin(url):
        rows=original(url)
        if 'granularity=86400' in url:
            for row in rows:row[-1]=0
        return rows
    w.transport=thin;assert w.step()['selected']==[]
    w.transport=original;calls.clear();clock[0]+=3601;clock[1]+=3601
    result=w.step()
    markets={urlsplit(url).path.split('/')[2] for url in calls if '/candles?' in url}
    assert markets=={'A4-USD','A5-USD','A6-USD','A7-USD'} and len(result['selected'])==4
