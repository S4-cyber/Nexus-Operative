"""Transactional, replay-validated offline order journal. No broker IO.

Requests verify retained command history and use replay-validated checkpoints.
Fresh processes replay prefixes before accepting cached state.
No positions or account balances are inferred from reference-notional fills.
"""
import copy
import json
import math
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from pathlib import Path
from nexus.order_gateway import OrderGateway
from nexus.order_accounting import (AccountingOrderGateway, MAX_ARCHIVED_INTENTS, MAX_ARCHIVE_BATCHES,
    MAX_RETIRED_ORDER_ID_FINGERPRINTS, RETIRED_ID_POLICY, retired_id_set_sha256)
from nexus import emergency_intent, execution_portfolio, order_checkpoints
from nexus.execution_capacity import report as capacity_report, CONTROL_RESERVE

MAX_COMMANDS = 10000
MAX_RETAINED_COMMANDS = 100000
ROTATION_MARKER = 'nexus-order-rotation.intent'
METHODS = frozenset(('submit', 'simulate_event', 'cancel', 'emergency_halt', 'reset', 'initialize_account', 'simulate_priced_fill', 'link_portfolio', 'set_execution_terms', 'expire_order', 'simulate_quote_fill', 'expire_due', 'archive_terminal', 'bind_replacement_epoch'))


class DurableOrderGateway:
    def __init__(self, path=None, checkpoints_enabled=True, rotation_recovery=False):
        if type(checkpoints_enabled) is not bool:raise ValueError('INVALID_CHECKPOINT_OPTION')
        if type(rotation_recovery) is not bool:raise ValueError('INVALID_ROTATION_RECOVERY_OPTION')
        self.path = Path(path) if path is not None else None
        self.checkpoints_enabled = checkpoints_enabled
        self.rotation_recovery = rotation_recovery
        self.restore_diagnostics = {}

    def _path(self):
        if self.path is not None:
            return self.path
        root = Path(os.environ.get('NEXUS_PORTABLE_DATA', str(Path.home()/'.nexus-operative')))
        return root.expanduser()/'order_lifecycle.sqlite3'

    def _require_rotation_clear(self):
        if self.rotation_recovery:return
        marker=self._path().parent/ROTATION_MARKER
        try:marker.lstat()
        except FileNotFoundError:return
        except OSError:raise ValueError('ORDER_ROTATION_STATE_UNAVAILABLE') from None
        raise ValueError('ORDER_ROTATION_RECOVERY_REQUIRED')

    def _connect(self):
        self._require_rotation_clear()
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('''CREATE TABLE IF NOT EXISTS commands(
            sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)''')
        db.execute('CREATE TABLE IF NOT EXISTS order_checkpoints(sequence INTEGER PRIMARY KEY,prefix_sha256 TEXT NOT NULL,state_json TEXT NOT NULL)')
        return db

    @contextmanager
    def _read_connection(self):
        self._require_rotation_clear()
        path=self._path()
        if path.is_symlink():raise ValueError('ORDER_HISTORY_SYMLINK')
        try:observed=path.stat()
        except FileNotFoundError:
            yield None
            return
        if not stat.S_ISREG(observed.st_mode):raise ValueError('ORDER_HISTORY_REGULAR_FILE_REQUIRED')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
            db.execute('BEGIN')
            yield db

    def _read_restore(self, db, *, compare_portfolio=False):
        if db is None:
            self.restore_diagnostics=dict(checkpoint_sequence=0,prefix_replayed=0,tail_replayed=0,cache_hit=False)
            return AccountingOrderGateway(clock=lambda:0),0
        gateway,count=self._restore(db)
        if compare_portfolio and gateway.portfolio_linked:
            portfolio=execution_portfolio.attach_read_only(db,self._path().parent/'paper_portfolio.sqlite3')
            execution_portfolio.compare(portfolio,gateway.account)
        return gateway,count

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
        return self._call_many(((method,args,kwargs),))[0]

    def _call_many(self,steps,execution_clock=None):
        if execution_clock is not None and not callable(execution_clock):raise ValueError('INVALID_EXECUTION_CLOCK')
        steps=tuple(steps)
        if not steps or len(steps)>4:raise ValueError('INVALID_ORDER_TRANSACTION_SIZE')
        if any(step[0] in ('submit','simulate_event','simulate_priced_fill','simulate_quote_fill') for step in steps):
            try:cut=emergency_intent.requested(self._path())
            except (ValueError,OSError):cut=True
            if cut:raise ValueError('EMERGENCY_CUT_REQUESTED')
        if len(steps)>1:
            if [step[0] for step in steps]!=['submit','set_execution_terms','simulate_quote_fill','cancel']:
                raise ValueError('INVALID_QUOTE_ORDER_TRANSACTION')
            if len({step[1][0] for step in steps})!=1:raise ValueError('QUOTE_ORDER_TRANSACTION_ID_MISMATCH')
        prepared=[]
        for method,args,kwargs in steps:
            if method not in METHODS:raise ValueError('INVALID_COMMAND')
            payload=json.dumps(dict(method=method,args=args,kwargs=kwargs),allow_nan=False)
            prepared.append((method,args,kwargs,payload))
        with closing(self._connect()) as db,db:
            portfolio=execution_portfolio.attach(db,self._path().parent/'paper_portfolio.sqlite3')
            db.execute('BEGIN IMMEDIATE')
            gateway,count=self._restore(db)
            results=[]
            for method,args,kwargs,payload in prepared:
                if method=='simulate_quote_fill' and execution_clock is not None:
                    evaluated=execution_clock()
                    try:valid=type(evaluated) in (int,float) and math.isfinite(evaluated)
                    except OverflowError:valid=False
                    if not valid:raise ValueError('INVALID_EXECUTION_CLOCK_SAMPLE')
                    if len(args)>8:args=(*args[:8],evaluated,*args[9:])
                    else:kwargs={**kwargs,'evaluated_at':evaluated}
                    payload=json.dumps(dict(method=method,args=args,kwargs=kwargs),allow_nan=False)
                result,count=self._apply(db,portfolio,gateway,count,method,args,kwargs,payload)
                results.append(result)
        return results

    def _apply(self,db,portfolio,gateway,count,method,args,kwargs,payload):
        active_count=count-gateway.archival_boundary
        if method=='archive_terminal':
            args=(*args,count+1)
            payload=json.dumps(dict(method=method,args=args,kwargs=kwargs),allow_nan=False)
        if gateway.portfolio_linked:
            if method not in ('emergency_halt','cancel','expire_order','expire_due'):
                execution_portfolio.compare(portfolio,gateway.account)
            if method=='simulate_event' and (kwargs.get('kind') or (args[3] if len(args)>3 else None))=='FILL':
                raise ValueError('LINKED_ACCOUNT_REQUIRES_PRICED_FILLS')
        before = (gateway.halted,gateway.portfolio_linked, json.dumps([gateway.intents, gateway.archived_intents, gateway.archive_batches, gateway.archival_boundary, gateway.epoch_lineage, gateway.retired_order_id_fingerprints, gateway.account, gateway.order_terms, sorted((str(k), v) for k,v in gateway.executions.items()), sorted((str(k), v) for k,v in gateway.quote_events.items())], sort_keys=True))
        result = getattr(gateway, method)(*args, **kwargs)
        after = (gateway.halted,gateway.portfolio_linked, json.dumps([gateway.intents, gateway.archived_intents, gateway.archive_batches, gateway.archival_boundary, gateway.epoch_lineage, gateway.retired_order_id_fingerprints, gateway.account, gateway.order_terms, sorted((str(k), v) for k,v in gateway.executions.items()), sorted((str(k), v) for k,v in gateway.quote_events.items())], sort_keys=True))
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
            if (active_count >= MAX_COMMANDS-CONTROL_RESERVE or count>=MAX_RETAINED_COMMANDS-CONTROL_RESERVE) and method in ('submit','simulate_event','simulate_priced_fill','initialize_account','link_portfolio','set_execution_terms','simulate_quote_fill','bind_replacement_epoch'):
                raise ValueError('ORDER_JOURNAL_CAP_REACHED')
            if (active_count >= MAX_COMMANDS and method!='archive_terminal') or count>=MAX_RETAINED_COMMANDS:
                raise ValueError('ORDER_JOURNAL_CAP_REACHED')
            db.execute('INSERT INTO commands VALUES (?,?)', (count+1, payload))
            if self.checkpoints_enabled and (count+1)%order_checkpoints.INTERVAL==0:
                order_checkpoints.save(db,gateway,count+1)
        return result,count+(before!=after)

    def execute_quote_order(self,client_order_id,product,side,notional,*,tier,created_at,ttl_seconds,
                            quantity,bid,ask,available_quantity,quote_at,evaluated_at,fee_bps=10,slippage_bps=5,execution_clock=None):
        """Opt-in offline atomic order treatment; not used by the forward runner.

        Retains ordinary replayable commands. Any failure rolls back new changes
        and linked ledger writes; only the completed transaction is acknowledged.
        """
        results=self._call_many((
            ('submit',(client_order_id,product,side,notional),dict(tier=tier)),
            ('set_execution_terms',(client_order_id,created_at,ttl_seconds),{}),
            ('simulate_quote_fill',(client_order_id,'fill',1,quantity,bid,ask,available_quantity,quote_at,evaluated_at),dict(fee_bps=fee_bps,slippage_bps=slippage_bps)),
            ('cancel',(client_order_id,),{})),execution_clock=execution_clock)
        return dict(order=results[3],fill=results[2],atomic_transaction=True,live_orders_enabled=False)

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

    def simulate_quote_fill(self, *args, execution_clock=None, **kwargs):
        return self._call_many((('simulate_quote_fill',args,kwargs),),execution_clock=execution_clock)[0]

    def expire_due(self, evaluated_at):
        return self._call('expire_due', evaluated_at)

    def archive_terminal(self,batch_id):
        return self._call('archive_terminal',batch_id)

    def bind_replacement_epoch(self,generation,predecessor_archive_sha256,predecessor_epoch_sha256,
                               retired_order_id_fingerprints=None):
        args=(generation,predecessor_archive_sha256,predecessor_epoch_sha256)
        if retired_order_id_fingerprints is not None:args=(*args,retired_order_id_fingerprints)
        return self._call('bind_replacement_epoch',*args)

    def _retired_order_id_fingerprints(self):
        """Private rotation helper; public status exposes only count and set hash."""
        with self._read_connection() as db:
            gateway,_=self._read_restore(db)
        return tuple(gateway.retired_order_id_fingerprints)

    def connections(self):
        from nexus.alpaca_paper_contract import catalogue
        return {**OrderGateway().connections(),'alpaca_paper_contract':catalogue()}

    def session_orders(self,session_id):
        import copy,re
        if not isinstance(session_id,str) or not re.fullmatch(r'[a-f0-9]{16}',session_id):raise ValueError('INVALID_FORWARD_SESSION_ID')
        prefix='pf_'+session_id+'_'
        with self._read_connection() as db:
            gateway,_=self._read_restore(db,compare_portfolio=True)
        records={**gateway.archived_intents,**gateway.intents}
        matched={key:dict(order=copy.deepcopy(record),quote_events=[]) for key,record in records.items() if key.startswith(prefix)}
        for (oid,_),detail in gateway.quote_events.items():
            if oid in matched:matched[oid]['quote_events'].append(copy.deepcopy(detail))
        return list(matched.values())

    def order_report(self,client_order_id):
        import copy
        with self._read_connection() as db:
            gateway,_=self._read_restore(db)
            record=gateway.lookup(client_order_id)
            if record is None:raise ValueError('UNKNOWN_ORDER_ID')
            return dict(read_only=True,archived=client_order_id in gateway.archived_intents,
                        order=copy.deepcopy(record),execution_terms=copy.deepcopy(gateway.order_terms.get(client_order_id)),
                        executions=[dict(event_id=eid,**copy.deepcopy(detail)) for (oid,eid),detail in gateway.executions.items() if oid==client_order_id],
                        quote_events=[dict(event_id=eid,**copy.deepcopy(detail)) for (oid,eid),detail in gateway.quote_events.items() if oid==client_order_id],
                        live_orders_enabled=False)

    def recovery_report(self):
        with self._read_connection() as db:
            present=db is not None
            gateway,count=self._read_restore(db,compare_portfolio=True)
        return dict(mode='OFFLINE_ORDER_RECOVERY', status='REPLAY_VERIFIED' if present else 'NO_RETAINED_HISTORY',journal_present=present,
                    commands=count, halted=gateway.halted,
                    orders=list(gateway.intents.values()),execution_terms=gateway.order_terms,
                    quote_model_events=len(gateway.quote_events), live_orders_enabled=False,
                    portfolio_reconciled=gateway.portfolio_linked, execution_account=gateway.account_report(),
                    recovery_diagnostics=dict(self.restore_diagnostics),
                    retention=dict(command_history_deleted=False,checkpoint_limit=2,journal_command_limit=MAX_COMMANDS,retained_history_command_limit=MAX_RETAINED_COMMANDS),
                    capacity=capacity_report(count-gateway.archival_boundary,len(gateway.intents),gateway.halted,MAX_COMMANDS,
                        retained_commands=count,retained_limit=MAX_RETAINED_COMMANDS),
                    lineage=(copy.deepcopy(gateway.epoch_lineage) if gateway.epoch_lineage is not None else
                             dict(generation=0,epoch_sha256='0'*64,predecessor_archive_sha256=None,
                                  predecessor_epoch_sha256=None)),
                    retired_order_ids=dict(policy=RETIRED_ID_POLICY,
                        fingerprint_count=len(gateway.retired_order_id_fingerprints),
                        fingerprint_set_sha256=retired_id_set_sha256(gateway.retired_order_id_fingerprints),
                        fingerprint_limit=MAX_RETIRED_ORDER_ID_FINGERPRINTS,
                        cross_epoch_reuse_fenced=('retired_order_id_fingerprints_sha256' in (gateway.epoch_lineage or {}))),
                    archival=dict(active_epoch_commands=count-gateway.archival_boundary,
                                  retained_commands=count,retained_limit=MAX_RETAINED_COMMANDS,
                                  archived_order_count=len(gateway.archived_intents),archived_order_limit=MAX_ARCHIVED_INTENTS,
                                  archive_batch_limit=MAX_ARCHIVE_BATCHES,batches=list(gateway.archive_batches.values()),
                                  command_history_deleted=False,physical_compaction=False),
                    note='Missing order history is not evidence of a flat account or absence of a separate portfolio. Reference notionals cannot establish quantities, positions or ownership. Read-only snapshots grant no execution permission.')


gateway = DurableOrderGateway()
