import json
import math
from pathlib import Path
import shutil
import sqlite3
import subprocess
import pytest
from fastapi.testclient import TestClient
from nexus.app import app
from nexus import startup_tasks as tasks


@pytest.fixture
def local(tmp_path,monkeypatch):
    root=tmp_path/'new-install';monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root))
    monkeypatch.setattr(tasks,'ai_status',lambda:dict(configured=False))
    return root


def rows():return {x['id']:x for x in tasks.checklist()['items']}


def test_fresh_setup_check_has_no_network_writes_or_required_payments(local,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *_a,**_k:pytest.fail('Checklist made network request'))
    report=tasks.checklist();items={x['id']:x for x in report['items']}
    assert not local.exists() and not report['network_requests_made']
    assert items['engine']['checked'] and not items['external_ai']['checked']
    assert items['payment']['status']=='UNAVAILABLE' and items['broker']['status']=='UNAVAILABLE'
    assert not items['portfolio']['checked'] and not items['budget']['checked']
    assert not report['starts_automatically'] and not report['live_orders_enabled']
    assert len({x['id'] for x in report['items']})==len(report['items'])


def test_saved_budget_and_tier_checks_track_changes_without_writing(local):
    from nexus.state import DEFAULTS,save_settings
    from nexus.tier_permissions import configure
    save_settings(DEFAULTS);configure('terrestrial',True)
    before=(local/'portal_settings.sqlite3').read_bytes();items=rows()
    assert items['budget']['checked'] and items['tier']['checked']
    assert (local/'portal_settings.sqlite3').read_bytes()==before
    configure('terrestrial',False);assert not rows()['tier']['checked']
    with sqlite3.connect(local/'portal_settings.sqlite3') as db:
        db.execute("UPDATE configuration SET value='60' WHERE key='solar_allocation'")
    assert not rows()['budget']['checked']


def test_portfolio_setup_is_read_only_and_not_an_integrity_claim(local):
    from nexus.paper_portfolio import initialize
    assert not rows()['portfolio']['checked']
    initialize(1000);before=(local/'paper_portfolio.sqlite3').read_bytes();item=rows()['portfolio']
    assert item['checked'] and 'does not prove integrity' in item['detail']
    assert (local/'paper_portfolio.sqlite3').read_bytes()==before
    with sqlite3.connect(local/'paper_portfolio.sqlite3') as db:db.execute("UPDATE meta SET value='-1' WHERE key='cash'")
    assert not rows()['portfolio']['checked']


def test_corrupt_storage_reports_unknown_without_repair_or_secret_echo(local):
    local.mkdir();path=local/'portal_settings.sqlite3';path.write_bytes(b'not sqlite PRIVATE-CREDENTIAL-FIXTURE')
    report=tasks.checklist();items={x['id']:x for x in report['items']}
    assert items['budget']['status']=='UNKNOWN' and items['tier']['status']=='UNKNOWN'
    assert path.read_bytes()==b'not sqlite PRIVATE-CREDENTIAL-FIXTURE'
    assert 'PRIVATE-CREDENTIAL' not in json.dumps(report)


def test_optional_ai_configuration_never_claims_a_provider_connection(local,monkeypatch):
    monkeypatch.setattr(tasks,'ai_status',lambda:dict(configured=True,provider_connected=False,secret='PRIVATE'))
    item=rows()['external_ai'];assert item['checked'] and 'not a working provider test' in item['detail']
    assert 'PRIVATE' not in json.dumps(tasks.checklist()) and not local.exists()


def test_model_check_requires_fresh_current_generation_and_engine(local,monkeypatch):
    from nexus import market_hub as h,hub_prediction as prediction
    data=[]
    for i in range(300):
        c=100+math.sin(i/8);data.append(dict(starts_at=(10000+i)*3600,low=str(c-1),high=str(c+1),open=str(c),close=str(c),volume='10'))
    h.ingest('BTC-USD',3600,data);pack=prediction.prepare('BTC-USD');at=pack['model']['forecast_as_of']
    monkeypatch.setattr(tasks.time,'time',lambda:at+1)
    assert rows()['learning']['checked']
    monkeypatch.setattr(tasks.time,'time',lambda:at+7201)
    assert rows()['learning']['status']=='NEEDS_REFRESH'
    monkeypatch.setattr(tasks.time,'time',lambda:at+1)
    monkeypatch.setattr(tasks,'ENGINE_VERSION','new-version')
    assert not rows()['learning']['checked']
    monkeypatch.setattr(tasks,'ENGINE_VERSION',pack['council']['engine_version'])
    next_bar={**data[-1],'starts_at':data[-1]['starts_at']+3600}
    h.ingest('BTC-USD',3600,[next_bar],now=at+3601)
    assert not rows()['learning']['checked']


def test_tasks_endpoint_cannot_complete_tasks_by_post_or_initialize(local):
    client=TestClient(app);report=client.get('/api/onboarding/tasks')
    assert report.status_code==200 and report.json()['mode']=='READ_ONLY_STARTUP_TASKS'
    assert client.post('/api/onboarding/tasks',json={'payment':True,'tier':True}).status_code==405
    assert not local.exists()


def test_workspace_controller_runs_real_script_with_safe_checkboxes():
    node=shutil.which('node')
    if not node:pytest.skip('Node needed for actual workspace controller check')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/workspace_tabs_smoke.cjs')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


def test_all_controls_belong_to_named_workspace_or_global_stop_bar():
    from html.parser import HTMLParser
    html=(Path(__file__).resolve().parents[1]/'dashboard/portal_preview.html').read_text()
    class Layout(HTMLParser):
        def __init__(self):super().__init__();self.stack=[];self.ids={};self.locations={};self.tabs={};self.panels={}
        def handle_starttag(self,tag,attrs):
            a=dict(attrs);ident=a.get('id');panel=next((x[1] for x in reversed(self.stack) if x[1] and x[1].startswith('panel-')),None)
            if ident:
                assert ident not in self.ids,ident
                self.ids[ident]=a;self.locations[ident]=panel
            if a.get('role')=='tab':self.tabs[ident]=a
            if a.get('role')=='tabpanel':self.panels[ident]=a
            if tag not in ('input','meta','br','hr','img','link'):self.stack.append((tag,ident))
        def handle_endtag(self,tag):
            for i in range(len(self.stack)-1,-1,-1):
                if self.stack[i][0]==tag:self.stack=self.stack[:i];break
    layout=Layout();layout.feed(html)
    names=('portal','risk','research','ai','accounts','todo','help')
    assert set(layout.tabs)=={'tab-'+x for x in names}
    assert set(layout.panels)=={'panel-'+x for x in names}
    for name in names:
        assert layout.tabs['tab-'+name]['aria-controls']=='panel-'+name
        assert layout.panels['panel-'+name]['aria-labelledby']=='tab-'+name
        assert ('hidden' in layout.panels['panel-'+name])==(name!='portal')
    for ident,panel in {'forward-start':'portal','risk-tier-controls':'risk','paperbudget':'risk','hub-build':'research','aikey':'ai','paperstart':'accounts','preview':'accounts','startup-refresh':'todo','release-roadmap-load':'todo'}.items():
        assert layout.locations[ident]=='panel-'+panel
    assert layout.locations['halt'] is None and layout.locations['activate'] is None
    assert 'get started' in html.lower() and 'Boxes are read-only' in html


def test_malformed_model_metadata_is_unknown_without_disabling_other_checks(local,monkeypatch):
    monkeypatch.setattr(tasks.hub,'latest_model',lambda *_:'a'*64)
    monkeypatch.setattr(tasks.hub,'read',lambda *_:dict(schema='nexus.hub-momentum-model.v1',product='BTC-USD',granularity=3600,dataset_generation=0,onboard_council='invalid'))
    items=rows()
    assert items['learning']['status']=='UNKNOWN' and not items['learning']['checked']
    assert items['engine']['checked'] and not local.exists()
