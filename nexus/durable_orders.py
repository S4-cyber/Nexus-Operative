"""Transactional, replay-validated offline order journal. No broker IO.

Every request rebuilds state from committed commands under a SQLite write lock.
This conservative bounded implementation favors recovery correctness over latency.
No positions or account balances are inferred from reference-notional fills.
"""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from nexus.order_gateway import OrderGateway
from nexus.order_accounting import AccountingOrderGateway
from nexus import execution_portfolio

MAX_COMMANDS = 10000
METHODS = frozenset(('submit', 'simulate_event', 'cancel', 'emergency_halt', 'reset', 'initialize_account', 'simulate_priced_fill', 'link_portfolio'))


class DurableOrderGateway:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None

    def _path(self):
        if self.path is not None:
            return self.path
        root = Path(os.environ.get('NEXUS_PORTABLE_DATA', str(Path.home()/'.nexus-operative')))
        return root.expanduser()/'order_lifecycle.sqlite3'

    def _connect(self):
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('''CREATE TABLE IF NOT EXISTS commands(
            sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)''')
        return db

    def _restore(self, db):
        gateway = AccountingOrderGateway(clock=lambda: 0)
        rows = db.execute('SELECT sequence,payload FROM commands ORDER BY sequence').fetchmany(MAX_COMMANDS+1)
        if len(rows)>MAX_COMMANDS:
            raise ValueError('ORDER_JOURNAL_CAP_EXCEEDED')
        try:
            for expected, (sequence, payload) in enumerate(rows, 1):
                command = json.loads(payload)
                if sequence != expected or command['method'] not in METHODS:
                    raise ValueError('INVALID_COMMAND')
                getattr(gateway, command['method'])(*command['args'], **command['kwargs'])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError('ORDER_JOURNAL_RECOVERY_FAILED') from exc
        return gateway, len(rows)

    def _call(self, method, *args, **kwargs):
        # Validate JSON before opening a transaction; NaN is never persisted.
        payload = json.dumps(dict(method=method, args=args, kwargs=kwargs), allow_nan=False)
        with closing(self._connect()) as db, db:
            portfolio=execution_portfolio.attach(db,self._path().parent/'paper_portfolio.sqlite3')
            db.execute('BEGIN IMMEDIATE')
            gateway, count = self._restore(db)
            if gateway.portfolio_linked:
                if method not in ('emergency_halt','cancel'):
                    execution_portfolio.compare(portfolio,gateway.account)
                if method=='simulate_event' and (kwargs.get('kind') or (args[3] if len(args)>3 else None))=='FILL':
                    raise ValueError('LINKED_ACCOUNT_REQUIRES_PRICED_FILLS')
            before = (gateway.halted,gateway.portfolio_linked, json.dumps([gateway.intents, gateway.account, sorted((str(k), v) for k,v in gateway.executions.items())], sort_keys=True))
            result = getattr(gateway, method)(*args, **kwargs)
            after = (gateway.halted,gateway.portfolio_linked, json.dumps([gateway.intents, gateway.account, sorted((str(k), v) for k,v in gateway.executions.items())], sort_keys=True))
            if before != after:
                if method=='link_portfolio':
                    execution_portfolio.link(portfolio,gateway.account)
                    portfolio.execute("INSERT OR REPLACE INTO meta VALUES ('execution_linked','1')")
                if method=='simulate_priced_fill' and gateway.portfolio_linked:
                    oid=args[0] if args else kwargs['client_order_id']
                    eid=args[1] if len(args)>1 else kwargs['event_id']
                    order=gateway.intents[oid]
                    if order['side']=='BUY':
                        from nexus.tier_permissions import require_entry
                        require_entry(order['tier'],order['notional'])
                    execution_portfolio.apply_fill(portfolio,order,eid,gateway.executions[(oid,eid)],gateway.account)
                # Halt/cancel/reset must remain possible when normal intake is full.
                if count >= MAX_COMMANDS-1001 and method in ('submit','simulate_event','simulate_priced_fill','initialize_account','link_portfolio'):
                    raise ValueError('ORDER_JOURNAL_CAP_REACHED')
                if count >= MAX_COMMANDS:
                    raise ValueError('ORDER_JOURNAL_CAP_REACHED')
                db.execute('INSERT INTO commands VALUES (?,?)', (count+1, payload))
        return result

    def submit(self, *args, **kwargs):
        return self._call('submit', *args, **kwargs)

    def simulate_event(self, *args, **kwargs):
        return self._call('simulate_event', *args, **kwargs)

    def cancel(self, *args, **kwargs):
        return self._call('cancel', *args, **kwargs)

    def emergency_halt(self):
        return self._call('emergency_halt')

    def reset(self):
        return self._call('reset')

    def initialize_account(self, capital):
        return self._call('initialize_account', capital)

    def simulate_priced_fill(self, *args, **kwargs):
        return self._call('simulate_priced_fill', *args, **kwargs)

    def link_portfolio(self):
        return self._call('link_portfolio')

    def connections(self):
        return OrderGateway().connections()

    def recovery_report(self):
        with closing(self._connect()) as db, db:
            portfolio=execution_portfolio.attach(db,self._path().parent/'paper_portfolio.sqlite3')
            db.execute('BEGIN')
            gateway, count = self._restore(db)
            if gateway.portfolio_linked:
                execution_portfolio.compare(portfolio,gateway.account)
        return dict(mode='OFFLINE_ORDER_RECOVERY', status='REPLAY_VERIFIED',
                    commands=count, halted=gateway.halted,
                    orders=list(gateway.intents.values()), live_orders_enabled=False,
                    portfolio_reconciled=gateway.portfolio_linked, execution_account=gateway.account_report(),
                    note='Reference notionals cannot establish quantities, positions or ownership.')


gateway = DurableOrderGateway()
