"""Transactional, replay-validated offline order journal. No broker IO.

Requests verify retained command history and use replay-validated checkpoints.
Fresh processes replay prefixes before accepting cached state.
No positions or account balances are inferred from reference-notional fills.
"""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from nexus.order_gateway import OrderGateway
from nexus.order_accounting import AccountingOrderGateway, MAX_ARCHIVED_INTENTS, MAX_ARCHIVE_BATCHES
from nexus import execution_portfolio, order_checkpoints
from nexus.execution_capacity import report as capacity_report, CONTROL_RESERVE

MAX_COMMANDS = 10000
MAX_RETAINED_COMMANDS = 100000
METHODS = frozenset(('submit', 'simulate_event', 'cancel', 'emergency_halt', 'reset', 'initialize_account', 'simulate_priced_fill', 'link_portfolio', 'set_execution_terms', 'expire_order', 'simulate_quote_fill', 'expire_due', 'archive_terminal'))


class DurableOrderGateway:
    def __init__(self, path=None, checkpoints_enabled=True):
        if type(checkpoints_enabled) is not bool:raise ValueError('INVALID_CHECKPOINT_OPTION')
        self.path = Path(path) if path is not None else None
        self.checkpoints_enabled = checkpoints_enabled
        self.restore_diagnostics = {}

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
        db.execute('CREATE TABLE IF NOT EXISTS order_checkpoints(sequence INTEGER PRIMARY KEY,prefix_sha256 TEXT NOT NULL,state_json TEXT NOT NULL)')
        return db

    def _restore(self, db):
        rows = db.execute('SELECT sequence,payload FROM commands ORDER BY sequence').fetchmany(MAX_RETAINED_COMMANDS+1)
        if len(rows)>MAX_RETAINED_COMMANDS:raise ValueError('RETAINED_COMMAND_HISTORY_CAP_EXCEEDED')
        if any(type(sequence) is not int or sequence!=expected for expected,(sequence,_) in enumerate(rows,1)):
            raise ValueError('ORDER_JOURNAL_RECOVERY_FAILED')
        def replay(commands,gateway=None):
            if gateway is None:gateway=AccountingOrderGateway(clock=lambda:0)
            try:
                for sequence,payload in commands:
                    command=json.loads(payload)
                    if command['method'] not in METHODS:raise ValueError('INVALID_COMMAND')
                    if command['method']=='archive_terminal' and (len(command['args'])!=2 or command['args'][1]!=sequence):
                        raise ValueError('INVALID_ARCHIVE_BOUNDARY')
                    getattr(gateway,command['method'])(*command['args'],**command['kwargs'])
            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                raise ValueError('ORDER_JOURNAL_RECOVERY_FAILED') from exc
            return gateway
        if self.checkpoints_enabled:
            gateway,self.restore_diagnostics=order_checkpoints.restore(db,rows,replay)
        else:
            gateway=replay(rows)
            self.restore_diagnostics=dict(checkpoint_sequence=0,prefix_replayed=len(rows),tail_replayed=0,cache_hit=False)
        if len(rows)-gateway.archival_boundary>MAX_COMMANDS:raise ValueError('ORDER_JOURNAL_CAP_EXCEEDED')
        return gateway,len(rows)

    def _call(self, method, *args, **kwargs):
        # Validate JSON before opening a transaction; NaN is never persisted.
        payload = json.dumps(dict(method=method, args=args, kwargs=kwargs), allow_nan=False)
        with closing(self._connect()) as db, db:
            portfolio=execution_portfolio.attach(db,self._path().parent/'paper_portfolio.sqlite3')
            db.execute('BEGIN IMMEDIATE')
            gateway, count = self._restore(db)
            active_count=count-gateway.archival_boundary
            if method=='archive_terminal':
                args=(*args,count+1)
                payload=json.dumps(dict(method=method,args=args,kwargs=kwargs),allow_nan=False)
            if gateway.portfolio_linked:
                if method not in ('emergency_halt','cancel','expire_order','expire_due'):
                    execution_portfolio.compare(portfolio,gateway.account)
                if method=='simulate_event' and (kwargs.get('kind') or (args[3] if len(args)>3 else None))=='FILL':
                    raise ValueError('LINKED_ACCOUNT_REQUIRES_PRICED_FILLS')
            before = (gateway.halted,gateway.portfolio_linked, json.dumps([gateway.intents, gateway.archived_intents, gateway.archive_batches, gateway.archival_boundary, gateway.account, gateway.order_terms, sorted((str(k), v) for k,v in gateway.executions.items()), sorted((str(k), v) for k,v in gateway.quote_events.items())], sort_keys=True))
            result = getattr(gateway, method)(*args, **kwargs)
            after = (gateway.halted,gateway.portfolio_linked, json.dumps([gateway.intents, gateway.archived_intents, gateway.archive_batches, gateway.archival_boundary, gateway.account, gateway.order_terms, sorted((str(k), v) for k,v in gateway.executions.items()), sorted((str(k), v) for k,v in gateway.quote_events.items())], sort_keys=True))
            if before != after:
                if method=='link_portfolio':
                    execution_portfolio.link(portfolio,gateway.account)
                    portfolio.execute("INSERT OR REPLACE INTO meta VALUES ('execution_linked','1')")
                if method in ('simulate_priced_fill','simulate_quote_fill') and gateway.portfolio_linked and not result.get('expired'):
                    oid=args[0] if args else kwargs['client_order_id']
                    eid=args[1] if len(args)>1 else kwargs['event_id']
                    order=gateway.intents[oid]
                    if order['side']=='BUY':
                        from nexus.tier_permissions import require_entry
                        require_entry(order['tier'],order['notional'])
                    execution_portfolio.apply_fill(portfolio,order,eid,gateway.executions[(oid,eid)],gateway.account)
                # Halt/cancel/reset must remain possible when normal intake is full.
                if (active_count >= MAX_COMMANDS-CONTROL_RESERVE or count>=MAX_RETAINED_COMMANDS-CONTROL_RESERVE) and method in ('submit','simulate_event','simulate_priced_fill','initialize_account','link_portfolio','set_execution_terms','simulate_quote_fill'):
                    raise ValueError('ORDER_JOURNAL_CAP_REACHED')
                if (active_count >= MAX_COMMANDS and method!='archive_terminal') or count>=MAX_RETAINED_COMMANDS:
                    raise ValueError('ORDER_JOURNAL_CAP_REACHED')
                db.execute('INSERT INTO commands VALUES (?,?)', (count+1, payload))
                if self.checkpoints_enabled and (count+1)%order_checkpoints.INTERVAL==0:
                    order_checkpoints.save(db,gateway,count+1)
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

    def set_execution_terms(self, *args, **kwargs):
        return self._call('set_execution_terms', *args, **kwargs)

    def expire_order(self, *args, **kwargs):
        return self._call('expire_order', *args, **kwargs)

    def simulate_quote_fill(self, *args, **kwargs):
        return self._call('simulate_quote_fill', *args, **kwargs)

    def expire_due(self, evaluated_at):
        return self._call('expire_due', evaluated_at)

    def archive_terminal(self,batch_id):
        return self._call('archive_terminal',batch_id)

    def connections(self):
        return OrderGateway().connections()

    def order_report(self,client_order_id):
        import copy
        with closing(self._connect()) as db,db:
            db.execute('BEGIN')
            gateway,_=self._restore(db)
            record=gateway.lookup(client_order_id)
            if record is None:raise ValueError('UNKNOWN_ORDER_ID')
            return dict(read_only=True,archived=client_order_id in gateway.archived_intents,
                        order=copy.deepcopy(record),execution_terms=copy.deepcopy(gateway.order_terms.get(client_order_id)),
                        executions=[dict(event_id=eid,**copy.deepcopy(detail)) for (oid,eid),detail in gateway.executions.items() if oid==client_order_id],
                        quote_events=[dict(event_id=eid,**copy.deepcopy(detail)) for (oid,eid),detail in gateway.quote_events.items() if oid==client_order_id],
                        live_orders_enabled=False)

    def recovery_report(self):
        with closing(self._connect()) as db, db:
            portfolio=execution_portfolio.attach(db,self._path().parent/'paper_portfolio.sqlite3')
            db.execute('BEGIN')
            gateway, count = self._restore(db)
            if gateway.portfolio_linked:
                execution_portfolio.compare(portfolio,gateway.account)
        return dict(mode='OFFLINE_ORDER_RECOVERY', status='REPLAY_VERIFIED',
                    commands=count, halted=gateway.halted,
                    orders=list(gateway.intents.values()),execution_terms=gateway.order_terms,
                    quote_model_events=len(gateway.quote_events), live_orders_enabled=False,
                    portfolio_reconciled=gateway.portfolio_linked, execution_account=gateway.account_report(),
                    recovery_diagnostics=dict(self.restore_diagnostics),
                    retention=dict(command_history_deleted=False,checkpoint_limit=2,journal_command_limit=MAX_COMMANDS,retained_history_command_limit=MAX_RETAINED_COMMANDS),
                    capacity=capacity_report(count-gateway.archival_boundary,len(gateway.intents),gateway.halted,MAX_COMMANDS,
                        retained_commands=count,retained_limit=MAX_RETAINED_COMMANDS),
                    archival=dict(active_epoch_commands=count-gateway.archival_boundary,
                                  retained_commands=count,retained_limit=MAX_RETAINED_COMMANDS,
                                  archived_order_count=len(gateway.archived_intents),archived_order_limit=MAX_ARCHIVED_INTENTS,
                                  archive_batch_limit=MAX_ARCHIVE_BATCHES,batches=list(gateway.archive_batches.values()),
                                  command_history_deleted=False,physical_compaction=False),
                    note='Reference notionals cannot establish quantities, positions or ownership.')


gateway = DurableOrderGateway()
