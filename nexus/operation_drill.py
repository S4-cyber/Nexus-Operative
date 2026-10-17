"""Bounded scripted paper-operation drill in disposable storage, no market signals.

Exercises BUY/HOLD/SELL, a tier veto, restart and logical archival together.
This is a local reliability fixture, not unattended live-data strategy operation.
"""
import tempfile
import threading
from pathlib import Path
from nexus.durable_orders import DurableOrderGateway
from nexus.state import isolated_settings
from nexus.tier_permissions import configure
from nexus import order_checkpoints

_LOCK=threading.Lock()


def drill(cycles=12):
    if type(cycles) is not int or not 1<=cycles<=24:raise ValueError('INVALID_DRILL_CYCLES')
    if not _LOCK.acquire(blocking=False):raise ValueError('OPERATION_DRILL_BUSY')
    try:
        with tempfile.TemporaryDirectory(prefix='nexus-operation-drill-') as directory,isolated_settings(directory):
            path=Path(directory)/'orders.sqlite3'
            g=DurableOrderGateway(path)
            g.initialize_account(10000);g.link_portfolio()
            vetoes=0
            for index in range(cycles):
                now=1000+index*30
                buy='BUY_'+str(index);sell='SELL_'+str(index)
                configure('terrestrial',True)
                g.submit(buy,'BTC-USD','BUY',100,tier='terrestrial')
                g.set_execution_terms(buy,now,10)
                g.simulate_quote_fill(buy,'entry',1,1,25,25.1,2,now+1,now+1)
                held=g.recovery_report()
                if held['execution_account']['account']['positions']['BTC-USD']['quantity']!='1':
                    raise ValueError('DRILL_HOLD_DISAGREEMENT')
                configure('terrestrial',False)
                try:g.simulate_quote_fill(buy,'veto',2,1,25,25.1,2,now+2,now+2)
                except ValueError as exc:
                    if str(exc)!='RISK_TIER_DISENGAGED':raise
                    vetoes+=1
                else:raise ValueError('DRILL_EXPECTED_TIER_VETO')
                if g.recovery_report()['commands']!=held['commands']:raise ValueError('DRILL_VETO_MUTATED_HISTORY')
                g.submit(sell,'BTC-USD','SELL',100,tier='terrestrial')
                g.set_execution_terms(sell,now+2,10)
                g.simulate_quote_fill(sell,'exit',1,1,25,25.1,2,now+3,now+3)
                g.cancel(buy);g.cancel(sell)
                g.emergency_halt()
                g.archive_terminal('epoch_'+str(index))
                order_checkpoints.clear_cache()
                g=DurableOrderGateway(path)
                report=g.recovery_report()
                if not report['halted'] or not report['portfolio_reconciled'] or report['execution_account']['account']['positions']:
                    raise ValueError('DRILL_RECOVERY_DISAGREEMENT')
                g.reset()
            report=g.recovery_report()
            # Old IDs remain deduplicated after every epoch and process-style reconstruction.
            replay=g.submit('BUY_0','BTC-USD','BUY',100,tier='terrestrial')
            if not replay['idempotent_replay']:raise ValueError('DRILL_ARCHIVED_ID_REUSED')
            return dict(mode='SCRIPTED_OFFLINE_OPERATION_DRILL',passed=True,cycles=cycles,
                        buy_fills=cycles,hold_checks=cycles,sell_fills=cycles,tier_vetoes=vetoes,
                        halted_restart_checks=cycles,archive_batches=len(report['archival']['batches']),
                        archived_orders=report['archival']['archived_order_count'],
                        retained_commands=report['commands'],old_id_deduplicated=True,
                        portfolio_reconciled=report['portfolio_reconciled'],history_deleted=False,
                        application_accounts_changed=False,live_orders_enabled=False,
                        limitations='Scripted synthetic fixture with reconstructed gateway instances; not live-data strategy operation, real process death, network-outage validation or market performance evidence.')
    finally:_LOCK.release()
