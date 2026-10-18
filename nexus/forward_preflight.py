"""Read-only local start diagnostics. Never reserves admission or starts a worker."""
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import os
import sqlite3

from nexus import emergency_intent
from nexus.paper_policy import POLICY
from nexus.state import DEFAULTS,read_effective_settings
from nexus.tier_permissions import TIERS
from nexus.sqlite_journal_guard import check_existing
from nexus import execution_portfolio
from nexus.portfolio_governor import require_safe_ledger

MAX_FILE_BYTES=1024*1024*1024
BLOCKERS=frozenset(('PORTAL_STOPPED','TRADING_CUT_ACTIVE','RUNNER_ALREADY_ACTIVE',
    'LOCAL_HISTORY_REVIEW_REQUIRED','SETTINGS_REVIEW_REQUIRED','TIER_NOT_SELECTED','TIER_LIMIT_TOO_SMALL',
    'ACCOUNT_LINK_REVIEW_REQUIRED','BTC_ONLY_ACCOUNT_REQUIRED','PENDING_ORDERS_REVIEW_REQUIRED',
    'EVIDENCE_CAPACITY_REVIEW_REQUIRED','JOURNAL_CAPACITY_REVIEW_REQUIRED'))


def _existing(path):
    # Do not let SQLite attempt hot-journal recovery or create WAL sidecars.
    if os.path.lexists(Path(str(path)+'-journal')):raise ValueError('RETAINED_ROLLBACK_JOURNAL')
    return check_existing(path,MAX_FILE_BYTES,'PREFLIGHT_')


def _read(path,operation):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
        db.set_progress_handler(lambda:1,1000000)
        db.execute('BEGIN')
        return operation(db)


def _unused_portfolio(db,account):
    meta=dict(db.execute('SELECT key,value FROM meta'))
    if not meta:
        for table in ('ledger','positions','position_events','chain_seals','execution_fills'):
            if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():raise ValueError('NONEMPTY_UNINITIALIZED_PORTFOLIO')
        return
    require_safe_ledger(db)
    if db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]!=1:raise ValueError('TRADED_PORTFOLIO')
    execution_portfolio.compare(db,account)


def build(orders,runner,*,tier,portal_active,halted):
    if tier not in TIERS:raise ValueError('INVALID_PREFLIGHT_TIER')
    blocked=[];entry_mode='NEW_ENTRIES';prepare=False
    def result():
        reasons=sorted(set(blocked))
        return dict(mode='READ_ONLY_PAPER_START_PREFLIGHT',read_only=True,tier=tier,policy_id=POLICY['id'],
            local_checks_clear=not reasons,blockers=reasons,entry_mode=entry_mode,
            will_prepare_unused_account=prepare and not reasons,
            automatic_start=False,automatic_repair=False,running_permission_granted=False,
            external_owner_checked=False,fresh_prices_checked=False,strategy_validated=False,
            provider_contacted=False,live_orders_enabled=False,money_movement_enabled=False,
            limitations='Local sampled review only; separate files are not one atomic snapshot. No ownership lock, clock/fresh-price, provider, strategy or funds acceptance. Start repeats admission and may still refuse. Never treat this result as permission or a reserved account.')
    if not portal_active:blocked.append('PORTAL_STOPPED')
    if halted or emergency_intent.requested(orders._path()):blocked.append('TRADING_CUT_ACTIVE')
    status=runner.status()
    if status['enabled'] or status['worker_alive'] or status['ownership_held']:blocked.append('RUNNER_ALREADY_ACTIVE')
    if status['cut_requested']:blocked.append('TRADING_CUT_ACTIVE')
    if blocked:return result()
    root=orders._path().parent
    try:
        paths=[orders._path(),*(root/name for name in ('paper_portfolio.sqlite3','paper_forward.sqlite3','portal_settings.sqlite3'))]
        exists={path:_existing(path) for path in paths}
        recovery=orders.recovery_report();account=recovery['execution_account']['account']
        positions=(account or {}).get('positions',{})
        if recovery['halted']:blocked.append('TRADING_CUT_ACTIVE')
        if positions:entry_mode='RETAINED_EXIT_MONITORING'
        if any(product!=POLICY['product'] for product in positions):blocked.append('BTC_ONLY_ACCOUNT_REQUIRED')
        if any(order['status'] in ('SIMULATED_ACK_ONLY','SIMULATED_PARTIALLY_FILLED') for order in recovery['orders']):
            blocked.append('PENDING_ORDERS_REVIEW_REQUIRED')
        storage=runner.storage_capacity()
        if not storage['session_intake_allowed'] or not storage['quote_intake_allowed']:blocked.append('EVIDENCE_CAPACITY_REVIEW_REQUIRED')
        extra=int(not recovery['execution_account']['initialized'])+int(not recovery['portfolio_reconciled'])
        capacity=recovery['capacity']
        if (not capacity['lifecycle_intake_allowed'] or capacity['ordinary_commands_remaining']<(4 if positions else 12)+extra
            or capacity['intents_remaining']<(1 if positions else 2)):blocked.append('JOURNAL_CAPACITY_REVIEW_REQUIRED')
        settings=None
        if not positions:
            settings_path=root/'portal_settings.sqlite3'
            try:
                def config(db):
                    values=read_effective_settings(db)
                    switch=db.execute('SELECT enabled FROM tier_permissions WHERE tier=?',(tier,)).fetchone()
                    return values,switch
                settings,switch=_read(settings_path,config) if exists[settings_path] else (dict(DEFAULTS),None)
            except (ValueError,sqlite3.Error,TypeError,KeyError):
                blocked.append('SETTINGS_REVIEW_REQUIRED');return result()
            if not switch or type(switch[0]) is not int or switch[0]!=1:blocked.append('TIER_NOT_SELECTED')
            budget=Decimal(str(settings['paper_budget']))
            cap=min(Decimal(200),budget*TIERS[tier],budget*Decimal(settings[tier+'_allocation'])/100)
            initial=Decimal(account['initial']) if account else budget
            size=min(Decimal(POLICY['maximum_entry_usd']),initial*Decimal(POLICY['initial_entry_fraction_bps'])/10000)
            if cap<size:blocked.append('TIER_LIMIT_TOO_SMALL')
        if not recovery['portfolio_reconciled']:
            info=recovery['execution_account']
            if info['priced_fills'] or info['unpriced_fills'] or positions or (account and account['cash']!=account['initial']):
                blocked.append('ACCOUNT_LINK_REVIEW_REQUIRED')
            else:
                target=account or dict(initial=str(settings['paper_budget']),cash=str(settings['paper_budget']),positions={})
                portfolio_path=root/'paper_portfolio.sqlite3'
                if exists[portfolio_path]:
                    try:_read(portfolio_path,lambda db:_unused_portfolio(db,target))
                    except (ValueError,sqlite3.Error,ArithmeticError):blocked.append('ACCOUNT_LINK_REVIEW_REQUIRED')
                prepare=True
    except (ValueError,OSError,sqlite3.Error,ArithmeticError,TypeError,KeyError):blocked.append('LOCAL_HISTORY_REVIEW_REQUIRED')
    return result()
