"""Disposable cross-database recovery exercise with injected quotes, never market IO."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _review(session):
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    root=Path(os.environ['NEXUS_PORTABLE_DATA'])
    # Missing database copies must fail rather than become fresh empty accounts.
    for name in ('order_lifecycle.sqlite3','paper_portfolio.sqlite3','paper_forward.sqlite3','portal_settings.sqlite3'):
        if not (root/name).is_file():raise ValueError('RECOVERY_DRILL_DATABASE_MISSING')
    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    report=gateway.recovery_report()
    runner=PaperForwardRunner(gateway)
    evidence=runner.evidence_report(session)
    orders=gateway.session_orders(session)
    if not report['halted'] or not report['portfolio_reconciled']:
        raise ValueError('RECOVERY_DRILL_HALT_OR_LINK_FAILED')
    if runner.status()['enabled'] or not evidence['local_consistency_ok']:
        raise ValueError('RECOVERY_DRILL_EVIDENCE_OR_RESUME_FAILED')
    if len(orders)!=2 or report['orders'] or report['execution_account']['account']['positions']:
        raise ValueError('RECOVERY_DRILL_ARCHIVE_OR_POSITION_FAILED')
    return dict(session=session,halted=True,portfolio_reconciled=True,forward_stopped=True,
                archived_session_orders=len(orders),
                order_sha256=fingerprint(orders),account_sha256=fingerprint(report['execution_account']),
                quote_head_sha256=evidence['quote_evidence']['head_sha256'],
                quote_observations=evidence['quote_evidence']['observations'],
                policy_sha256=evidence['policy']['policy_sha256'],
                modeled_buy_orders=evidence['execution_crosscheck']['modeled_buy_orders'],
                modeled_sell_orders=evidence['execution_crosscheck']['modeled_sell_orders'],
                source_modes=evidence['quote_evidence']['source_modes'])


def _create():
    from datetime import datetime,timezone
    from nexus.durable_orders import DurableOrderGateway
    from nexus.paper_forward import PaperForwardRunner
    from nexus.tier_permissions import configure
    configure('terrestrial',True)
    root=Path(os.environ['NEXUS_PORTABLE_DATA'])
    gateway=DurableOrderGateway(root/'order_lifecycle.sqlite3')
    clock={'now':1000,'price':100}
    def quote(product):
        return dict(product=product,provider='coinbase_exchange_public',read_only=True,orders_allowed=False,
                    bid=clock['price'],ask=clock['price']+.01,last=clock['price'],received_at=clock['now'],
                    exchange_time=datetime.fromtimestamp(clock['now'],timezone.utc).isoformat())
    runner=PaperForwardRunner(gateway,quote,lambda:clock['now'])
    runner.prepare('terrestrial')
    for i in range(7):
        clock.update(now=1000+i*5,price=100+i*.1)
        result=runner.single_cycle()
    if result['decision']!='BUY':raise ValueError('RECOVERY_DRILL_ENTRY_FAILED')
    configure('terrestrial',False)
    clock.update(now=1035,price=102)
    if runner.single_cycle()['decision']!='SELL':raise ValueError('RECOVERY_DRILL_EXIT_FAILED')
    runner.stop('DISPOSABLE_RECOVERY_DRILL')
    gateway.emergency_halt()
    gateway.archive_terminal('recovery_drill_archive')
    return _review(runner.status()['session'])


def _child(directory,session=None):
    expression='from nexus.recovery_drill import _create,_review; import json; print(json.dumps('+('_create()' if session is None else '_review('+repr(session)+')')+'))'
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(directory)}
    result=subprocess.run([sys.executable,'-c',expression],env=env,capture_output=True,text=True,timeout=60)
    if result.returncode:raise ValueError('RECOVERY_DRILL_CHILD_FAILED: '+result.stderr[-2000:])
    return json.loads(result.stdout)


def run():
    from nexus.recovery_bundle import backup,restore,verify
    with tempfile.TemporaryDirectory(prefix='nexus-recovery-drill-') as temporary:
        root=Path(temporary);data=root/'source';data.mkdir()
        baseline=_child(data)
        backup(data,root/'backup',application_closed=True)
        verified=verify(root/'backup')
        restore(root/'backup',root/'restored',application_closed=True)
        recovered=_child(root/'restored',baseline['session'])
        if baseline!=recovered:raise ValueError('RECOVERY_DRILL_RESTORED_STATE_DISAGREEMENT')
        # Fresh-process validation must not change any retained backup file.
        verify(root/'backup')
        return dict(mode='DISPOSABLE_INJECTED_QUOTE_RECOVERY_DRILL',passed=True,
                    verified_files=len(verified['files']),recovered=recovered,
                    broker_connected=False,live_orders_enabled=False,release_approved=False,
                    limitations='Temporary fixture and fresh Python processes; not a real-feed run, process-kill test, cross-version migration or independent release acceptance.')


def main():
    try:print(json.dumps(run(),sort_keys=True,indent=2))
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        raise SystemExit(f'Recovery drill failed: {exc}')


if __name__=='__main__':main()
