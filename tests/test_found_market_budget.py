"""Shared public-discovery request admission is durable and fail closed."""
import os
import sqlite3
import threading

import pytest

from nexus.found_market_budget import SharedRequestBudget
from nexus import found_market_budget as budget_module
from nexus import found_markets as fm

NOW = 1_800_000_000


def test_missing_inspection_is_passive_and_first_admission_is_durable(tmp_path):
    path=tmp_path/'missing'/'budget.sqlite3';budget=SharedRequestBudget(path,limit=2)
    assert budget.inspect()['admissions']==0 and not path.parent.exists()
    assert budget.admit(lambda:NOW)['remaining']==1
    assert path.exists() and SharedRequestBudget(path,limit=2).inspect()['admissions']==1


def test_independent_instances_share_limit_and_restart_cannot_reset(tmp_path):
    path=tmp_path/'budget.sqlite3'
    a=SharedRequestBudget(path,limit=3);b=SharedRequestBudget(path,limit=3)
    a.admit(lambda:NOW);b.admit(lambda:NOW+1);SharedRequestBudget(path,limit=3).admit(lambda:NOW+2)
    with pytest.raises(ValueError,match='FOUND_MARKET_BUDGET_EXHAUSTED'):
        a.admit(lambda:NOW+3)
    assert b.inspect()['admissions']==3


def test_expiry_boundary_and_clock_regression_are_conservative(tmp_path):
    path=tmp_path/'budget.sqlite3';budget=SharedRequestBudget(path,limit=1)
    budget.admit(lambda:NOW)
    with pytest.raises(ValueError,match='EXHAUSTED'):budget.admit(lambda:NOW+3599)
    assert budget.admit(lambda:NOW+3600)['remaining']==0
    before=budget.inspect()
    with pytest.raises(ValueError,match='CLOCK_REGRESSION'):budget.admit(lambda:NOW+3599)
    assert budget.inspect()==before


def test_concurrent_instances_never_over_admit(tmp_path):
    path=tmp_path/'budget.sqlite3';results=[];lock=threading.Lock()
    def run(index):
        try:SharedRequestBudget(path,limit=12).admit(lambda:NOW);value=True
        except ValueError as error:value=str(error)
        with lock:results.append(value)
    threads=[threading.Thread(target=run,args=(i,)) for i in range(30)]
    for thread in threads:thread.start()
    for thread in threads:thread.join()
    assert results.count(True)==12
    assert results.count('FOUND_MARKET_BUDGET_EXHAUSTED')==18
    assert SharedRequestBudget(path,limit=12).inspect()['admissions']==12


def test_transient_sqlite_writer_contention_retries_without_losing_capacity(tmp_path,monkeypatch):
    path=tmp_path/'budget.sqlite3';budget=SharedRequestBudget(path,limit=2)
    budget.admit(lambda:NOW)
    real_connect=sqlite3.connect;raised=[]
    class BusyOnce:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def execute(self,sql,*args):
            if sql=='BEGIN IMMEDIATE' and not raised:
                raised.append(True);error=sqlite3.OperationalError('private contention detail')
                error.args=('database is locked',)
                raise error
            return self.db.execute(sql,*args)
    def connect(*args,**kwargs):
        db=real_connect(*args,**kwargs)
        return BusyOnce(db) if not raised else db
    monkeypatch.setattr(budget_module.sqlite3,'connect',connect)
    assert budget.admit(lambda:NOW+1)['remaining']==0
    assert raised==[True] and budget.inspect()['admissions']==2


def test_first_use_creator_keeps_initialization_role_across_contention(tmp_path,monkeypatch):
    path=tmp_path/'budget.sqlite3';real_connect=sqlite3.connect;raised=[]
    class BusyCreatorOnce:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def execute(self,sql,*args):
            if sql=='BEGIN IMMEDIATE' and not raised:
                raised.append(True);error=sqlite3.OperationalError('private contention detail')
                error.args=('database is locked',)
                raise error
            return self.db.execute(sql,*args)
    def connect(*args,**kwargs):
        db=real_connect(*args,**kwargs)
        return BusyCreatorOnce(db) if not raised else db
    monkeypatch.setattr(budget_module.sqlite3,'connect',connect)
    assert SharedRequestBudget(path,limit=2).admit(lambda:NOW)['remaining']==1
    assert raised==[True]
    assert SharedRequestBudget(path,limit=2).inspect()['admissions']==1


def test_sqlite_writer_contention_timeout_is_fixed_and_preserves_history(tmp_path,monkeypatch):
    path=tmp_path/'budget.sqlite3';budget=SharedRequestBudget(path,limit=2)
    budget.admit(lambda:NOW);before=path.read_bytes();real_connect=sqlite3.connect
    class AlwaysBusy:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def execute(self,sql,*args):
            if sql=='BEGIN IMMEDIATE':
                error=sqlite3.OperationalError('private contention detail')
                error.sqlite_errorcode=sqlite3.SQLITE_LOCKED
                raise error
            return self.db.execute(sql,*args)
    monkeypatch.setattr(budget_module.sqlite3,'connect',lambda *a,**k:AlwaysBusy(real_connect(*a,**k)))
    monkeypatch.setattr(budget_module,'LOCK_RETRY_SECONDS',0.0)
    with pytest.raises(ValueError,match='^FOUND_MARKET_BUDGET_CONTENTION_TIMEOUT$'):
        budget.admit(lambda:NOW+1)
    assert path.read_bytes()==before and budget.inspect()['admissions']==1


def test_malformed_sidecars_links_and_oversize_fail_closed(tmp_path):
    malformed=tmp_path/'malformed.sqlite3';malformed.write_text('not sqlite')
    with pytest.raises(ValueError):SharedRequestBudget(malformed).admit(lambda:NOW)
    target=tmp_path/'target';target.write_bytes(b'x')
    link=tmp_path/'link.sqlite3';link.symlink_to(target)
    with pytest.raises(ValueError,match='SYMLINK'):SharedRequestBudget(link).admit(lambda:NOW)
    hard=tmp_path/'hard.sqlite3';os.link(target,hard)
    with pytest.raises(ValueError,match='HARDLINK'):SharedRequestBudget(hard).admit(lambda:NOW)
    huge=tmp_path/'huge.sqlite3';huge.write_bytes(b'x'*(1024*1024+1))
    with pytest.raises(ValueError,match='DATABASE_CAP'):SharedRequestBudget(huge).admit(lambda:NOW)
    side=tmp_path/'side.sqlite3';side.with_name(side.name+'-wal').write_bytes(b'x')
    with pytest.raises(ValueError,match='WAL_SIDECAR'):SharedRequestBudget(side).admit(lambda:NOW)


def test_schema_or_row_tampering_fails_closed(tmp_path):
    path=tmp_path/'budget.sqlite3';SharedRequestBudget(path,limit=2).admit(lambda:NOW)
    with sqlite3.connect(path) as db:db.execute('UPDATE binding SET schema="wrong"')
    with pytest.raises(ValueError,match='BINDING'):SharedRequestBudget(path,limit=2).admit(lambda:NOW+1)


def test_found_markets_commits_budget_before_transport_and_hides_failures():
    events=[]
    class Budget:
        def admit(self,clock):events.append(('budget',clock()))
    def transport(url):events.append(('transport',url));raise OSError('private detail')
    w=fm.FoundMarkets(transport,lambda:1000,lambda:NOW,budget=Budget())
    w._admission=lambda:True;w._stop.clear()
    result=w.step()
    assert [event[0] for event in events]==['budget','transport']
    assert result['reason']=='SOURCE_OR_BUDGET_REVIEW_REQUIRED' and 'private' not in str(result)


def test_budget_denial_prevents_transport_and_does_not_count_attempt():
    class Deny:
        def admit(self,_):raise ValueError('FOUND_MARKET_BUDGET_EXHAUSTED')
    calls=[];w=fm.FoundMarkets(lambda url:calls.append(url),lambda:1000,lambda:NOW,budget=Deny())
    w._admission=lambda:True;w._stop.clear()
    result=w.step()
    assert calls==[] and result['requests_attempted']==0 and not result['selection_current']


def test_default_public_worker_is_passive_until_explicit_scan(tmp_path,monkeypatch):
    root=tmp_path/'data';monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(root))
    w=fm.FoundMarkets();w.transport=lambda _:(_ for _ in ()).throw(OSError('offline fixture'))
    assert w.status()['request_budget_scope']=='SHARED_COOPERATING_LOCAL_DATA_ROOT'
    assert not root.exists()
    w._admission=lambda:True;w._stop.clear()
    assert w.step()['reason']=='SOURCE_OR_BUDGET_REVIEW_REQUIRED'
    path=root/'found_market_request_budget.sqlite3'
    assert SharedRequestBudget(path).inspect()['admissions']==1
