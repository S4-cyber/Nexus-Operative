"""Bounded disposable recovery-step crash trials; no real keys or provider IO.

Run: python -m nexus.paper_recovery_drill
Only aggregate fixture evidence is returned. No production path is accepted.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from nexus import alpaca_paper_inspection as transport
from nexus.alpaca_paper_contract import preview
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_read_pacing import PaperReadPacing
from nexus.paper_recovery_step import FLAGS, recover_next, review_next

ACCOUNT='01234567-89ab-cdef-0123-456789abcdef'
PHASES=('before_lookup_begin_commit','after_lookup_begin_commit','before_lookup_result_commit',
        'after_lookup_result_commit','after_pacing_result_commit')
CHECKS=('retained_prefix_matches_boundary','fresh_process_replay_equal','sqlite_files_unchanged',
        'restart_vault_silent','restart_network_silent','another_step_denied_before_vault',
        'no_attempt_rearmed','original_uncertainty_preserved','local_ownership_reacquired')
SQLITE_NAMES=tuple(name+suffix for name in ('requests.sqlite3','pacing.sqlite3')
    for suffix in ('','-journal','-wal','-shm'))


def _require(condition,code):
    if not condition:raise ValueError('PAPER_RECOVERY_DRILL_'+code)


def _snapshot(root):
    result=[]
    for name in SQLITE_NAMES:
        path=root/name
        if path.exists():
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(65536),b''):digest.update(chunk)
            result.append((name,path.stat().st_size,digest.hexdigest()))
    return result


def _seed(root):
    journal=BrokerRequestJournal(root/'requests.sqlite3',ACCOUNT)
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,
        fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    for identity in ('a_fixture','b_fixture'):
        plan=preview(identity,'BTC-USD','BUY','2','100',asset,metadata_at=1000,evaluated_at=1001)
        journal.prepare(plan);journal.mark_submission_uncertain(identity)


def _guards():
    """Child-local guards: any actual socket or vault access fails the drill."""
    from nexus import paper_credentials
    attempts=dict(external_connections=0,credential_operations=0)
    def audit(event,args):
        if event=='socket.connect':
            attempts['external_connections']+=1;raise RuntimeError('NETWORK_DENIED')
    def denied():
        attempts['credential_operations']+=1;raise RuntimeError('VAULT_DENIED')
    sys.addaudithook(audit);paper_credentials._vault=denied
    return attempts


def _crash(root,phase):
    """Internal child target. Hooks exist only on disposable local objects."""
    _require(phase in PHASES,'PHASE');root=Path(root);attempts=_guards()
    journal=BrokerRequestJournal(root/'requests.sqlite3',ACCOUNT)
    pacing=PaperReadPacing(root/'pacing.sqlite3',ACCOUNT)
    counts=root/'get-count.json';counts.write_text('0')
    raw_account=dict(id=ACCOUNT,currency='USD',status='ACTIVE',crypto_status='ACTIVE',
        account_blocked=False,trading_blocked=False,trade_suspended_by_user=False,
        cash='1000',non_marginable_buying_power='900',pending_transfer_in='0',pending_transfer_out='0',accrued_fees='0')
    order=dict(id='11234567-89ab-cdef-0123-456789abcdef',client_order_id='a_fixture',symbol='BTC/USD',
        asset_class='crypto',side='buy',type='limit',time_in_force='ioc',qty='2',limit_price='100',
        status='partially_filled',filled_qty='1',filled_avg_price='99',updated_at='2026-10-18T00:00:00Z')
    class Response:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,value):self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*args):self.body.close()
    class Reader:
        def open(self,request,timeout):
            _require(request.get_method()=='GET' and request.data is None,'GET_ONLY')
            from nexus.broker_lookup_policy import LOOKUP_URL
            _require(request.full_url in (transport.URLS[0],LOOKUP_URL+'?client_order_id=a_fixture'),'URL_SCOPE')
            counts.write_text(str(int(counts.read_text())+1))
            return Response(request.full_url,raw_account if request.full_url==transport.URLS[0] else order)
    insert=journal._insert;begin=journal.begin_lookup_checked;finish=journal.finish_lookup_checked;pace_finish=pacing.finish
    def inserted(db,seq,kind,body,previous):
        insert(db,seq,kind,body,previous)
        if kind=='LOOKUP_BEGIN' and phase==PHASES[0]:os._exit(81)
        if kind=='LOOKUP_RESULT' and phase==PHASES[2]:os._exit(83)
    def begun(*args,**kwargs):
        result=begin(*args,**kwargs)
        if phase==PHASES[1]:os._exit(82)
        return result
    def finished(*args,**kwargs):
        result=finish(*args,**kwargs)
        if phase==PHASES[3]:os._exit(84)
        return result
    def paced(*args,**kwargs):
        result=pace_finish(*args,**kwargs)
        if phase==PHASES[4]:os._exit(85)
        return result
    journal._insert=inserted;journal.begin_lookup_checked=begun;journal.finish_lookup_checked=finished;pacing.finish=paced
    recover_next(journal,'fixture_key_only_123','fixture_secret_only_123',expected_account_id=ACCOUNT,
        confirm_read_only_paper=True,opener=Reader(),wall_clock=lambda:1000,elapsed_clock=lambda:50,pacing=pacing)
    _require(not any(attempts.values()),'SIDE_EFFECT');raise ValueError('PAPER_RECOVERY_DRILL_BOUNDARY_MISSED')


def _fresh(root,phase):
    root=Path(root);attempts=_guards()
    journal=BrokerRequestJournal(root/'requests.sqlite3',ACCOUNT);pacing=PaperReadPacing(root/'pacing.sqlite3',ACCOUNT)
    before=_snapshot(root);history=journal.recovery();state=pacing.inspect()
    at=1000 if phase==PHASES[4] else 1060
    reviewed=review_next(journal,expected_account_id=ACCOUNT,evaluated_at=at,pacing=pacing)
    from nexus.paper_recovery_step import recover_next_saved
    denied=False
    try:recover_next_saved(journal,expected_account_id=ACCOUNT,confirm_read_only_paper=True,
        wall_clock=lambda:at,elapsed_clock=lambda:50,pacing=pacing)
    except ValueError as error:denied=str(error)=='PAPER_RECOVERY_STEP_LOCAL_REVIEW_REQUIRED'
    after=_snapshot(root)
    rows=history['requests'];first=rows[0]
    return dict(request_pending=first['lookup']['pending'],request_attempts=first['lookup']['attempts'],
        retained_filled_quantity=first['snapshot']['filled_quantity'] if first['snapshot'] else None,
        pacing_pending=state['pending_token'] is not None,pacing_sessions=state['sessions'],
        pacing_next_allowed_at=state['next_allowed_at'],request_count=len(rows),
        other_request_attempts=rows[1]['lookup']['attempts'],
        original_uncertainty_preserved=all(row['submission_attempt_marked'] for row in rows),
        review_blocked=reviewed['local_checks_clear'] is False,
        another_step_denied_before_vault=denied and attempts['credential_operations']==0,
        sqlite_files_unchanged=before==after,
        rollback_sidecar_observed=any(name.endswith('-journal') and size>0 for name,size,_ in before),
        **attempts)


def _child(expression,*args,expected=0):
    try:child=subprocess.run([sys.executable,'-c',expression,*map(str,args)],capture_output=True,text=True,timeout=25)
    except (OSError,subprocess.SubprocessError):raise ValueError('PAPER_RECOVERY_DRILL_CHILD_FAILED') from None
    _require(child.returncode==expected,'CHILD_BOUNDARY')
    if expected:return None
    try:return json.loads(child.stdout)
    except (ValueError,TypeError):raise ValueError('PAPER_RECOVERY_DRILL_CHILD_RESULT') from None


def fixture_report():
    results=[]
    with tempfile.TemporaryDirectory(prefix='nexus-recovery-step-drill-') as directory:
        base=Path(directory)
        for index,phase in enumerate(PHASES):
            root=base/phase;root.mkdir();_seed(root)
            _child('import sys;from nexus.paper_recovery_drill import _crash;_crash(sys.argv[1],sys.argv[2])',root,phase,expected=81+index)
            script='import json,sys;from nexus.paper_recovery_drill import _fresh;print(json.dumps(_fresh(sys.argv[1],sys.argv[2])))'
            before=_snapshot(root);first=_child(script,root,phase);second=_child(script,root,phase)
            observed=index>=3;pending=index in (1,2);gets=int((root/'get-count.json').read_text())
            expected_prefix=(first['request_pending'] is pending and first['request_attempts']==(0 if index==0 else 1) and
                first['retained_filled_quantity']==('1' if observed else None) and first['pacing_pending'] is (index<4) and
                first['pacing_sessions']==1 and first['pacing_next_allowed_at']==(1060 if index==4 else 0) and
                first['request_count']==2 and first['other_request_attempts']==0 and gets==(0 if index<2 else 3))
            # Kernel ownership must be released by process death; reopening never
            # clears a pending pacing reservation or changes the SQLite history.
            with PaperReadPacing(root/'pacing.sqlite3',ACCOUNT).ownership() as owner:owner.validate()
            checks=dict(retained_prefix_matches_boundary=expected_prefix,fresh_process_replay_equal=first==second,
                sqlite_files_unchanged=first['sqlite_files_unchanged'] and before==_snapshot(root),
                restart_vault_silent=first['credential_operations']==0,restart_network_silent=first['external_connections']==0,
                another_step_denied_before_vault=first['another_step_denied_before_vault'] and first['review_blocked'],
                no_attempt_rearmed=first['other_request_attempts']==0 and first['request_attempts']<=1,
                original_uncertainty_preserved=first['original_uncertainty_preserved'],local_ownership_reacquired=True)
            results.append(dict(phase=phase,**checks,rollback_sidecar_observed=first['rollback_sidecar_observed'],injected_get_requests=gets))
    rollback=all(results[index]['rollback_sidecar_observed'] for index in (0,2))
    return dict(mode='DISPOSABLE_BOUNDED_RECOVERY_CRASH_DRILL',source='SYNTHETIC_LOCAL_PROCESS_DEATH_FIXTURE',
        passed=rollback and all(all(case[k] is True for k in CHECKS) for case in results),
        cases=results,forced_process_exits=len(results),fresh_process_reviews=2*len(results),
        precommit_rollback_sidecars_observed=rollback,actual_provider_access=False,actual_vault_access=False,
        **{flag:False for flag in FLAGS},
        limitations='Five forced process exits and ten fresh-process reviews in disposable fixture storage. Injected GET responses and clocks, never actual credentials/provider/source/fills/fees/funds or sustained market/native Windows acceptance. SQLite main and rollback/WAL/shared-memory files are fingerprinted; kernel ownership release never clears a retained reservation. Request and pacing commits remain separate. No automatic resume, retry, interruption, deletion, order submission or release authority.')


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_RECOVERY_DRILL_USAGE_ERROR\n')


def main(argv=None):
    _Parser(description=__doc__).parse_args(argv)
    try:
        report=fixture_report();_require(report['passed'],'CHECK_FAILED')
    except (ValueError,OSError,subprocess.SubprocessError,KeyboardInterrupt):
        print('PAPER_RECOVERY_DRILL_FAILED');return 2
    print(json.dumps(report,sort_keys=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
