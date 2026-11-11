"""Atomic SQLite bridge for explicitly linked offline execution accounting."""
import hashlib
import json
import os
import re
import time
import stat
from decimal import Decimal
from pathlib import Path
from nexus.paper_portfolio import create_schema, verify_ledger
from nexus.portfolio_governor import require_safe_ledger
from nexus.transaction_seals import append_seal

TABLES = r'\b(meta|positions|ledger|position_events|chain_seals|execution_fills)\b'
SNAPSHOT_TABLES = (
    ('meta', ('key','value'), ('key',)),
    ('positions', ('product','quantity','entry'), ('product',)),
    ('ledger', ('id','at','action','product','cash_delta'), ('id',)),
    ('position_events', ('id','ledger_id','action','product','quantity','entry'), ('id',)),
    ('chain_seals', ('ledger_id','previous','digest'), ('ledger_id',)),
    ('execution_fills', ('ledger_id','order_id','event_id','quantity','price','fee'), ('ledger_id',)),
)


class AttachedPortfolio:
    """Use the order transaction's attached portfolio database, without a second commit."""
    def __init__(self, db, path=None, observed=None):
        self.db = db
        self.path = path
        self.observed = observed

    def require_current(self):
        if self.path is None:return
        try:current=self.path.lstat()
        except OSError:raise ValueError('LINKED_PORTFOLIO_CHANGED') from None
        if (stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode) or
                not os.path.samestat(self.observed,current)):
            raise ValueError('LINKED_PORTFOLIO_CHANGED')

    def execute(self, sql, parameters=()):
        return self.db.execute(re.sub(TABLES, r'portfolio.\1', sql), parameters)

    def executemany(self, sql, parameters):
        return self.db.executemany(re.sub(TABLES, r'portfolio.\1', sql), parameters)


def _require_current(portfolio):
    """Recheck identity only for explicitly bound attached-portfolio readers."""
    checker=getattr(portfolio,'require_current',None)
    if checker is not None:checker()


def attach(db, path):
    db.execute('ATTACH DATABASE ? AS portfolio', (str(path),))
    # Atomic multi-file commit requires rollback journals on both databases.
    if db.execute('PRAGMA main.journal_mode').fetchone()[0] != 'delete' or db.execute('PRAGMA portfolio.journal_mode').fetchone()[0] != 'delete':
        raise ValueError('ATOMIC_PORTFOLIO_REQUIRES_DELETE_JOURNALS')
    db.execute('PRAGMA portfolio.synchronous=FULL')
    portfolio = AttachedPortfolio(db)
    create_schema(portfolio)
    return portfolio


def attach_read_only(db, path):
    """Compare an already linked ledger without creating/migrating its file."""
    path=Path(path)
    observed=path.lstat()  # Missing/inaccessible linked accounting must fail, not recreate.
    if stat.S_ISLNK(observed.st_mode):raise ValueError('LINKED_PORTFOLIO_SYMLINK')
    if not stat.S_ISREG(observed.st_mode):raise ValueError('LINKED_PORTFOLIO_REGULAR_FILE_REQUIRED')
    try:resolved=path.resolve(strict=True)
    except OSError:raise ValueError('LINKED_PORTFOLIO_CHANGED') from None
    try:after_resolve=path.lstat()
    except OSError:raise ValueError('LINKED_PORTFOLIO_CHANGED') from None
    if (stat.S_ISLNK(after_resolve.st_mode) or not stat.S_ISREG(after_resolve.st_mode) or
            not os.path.samestat(observed,after_resolve)):
        raise ValueError('LINKED_PORTFOLIO_CHANGED')
    db.execute('ATTACH DATABASE ? AS portfolio', (resolved.as_uri()+'?mode=ro',))
    if db.execute('PRAGMA main.journal_mode').fetchone()[0]!='delete' or db.execute('PRAGMA portfolio.journal_mode').fetchone()[0]!='delete':
        raise ValueError('ATOMIC_PORTFOLIO_REQUIRES_DELETE_JOURNALS')
    portfolio=AttachedPortfolio(db,path,observed)
    portfolio.require_current()
    try:
        attached=[Path(filename).resolve(strict=True) for _,name,filename in db.execute('PRAGMA database_list') if name=='portfolio']
    except OSError:raise ValueError('LINKED_PORTFOLIO_CONNECTION_MISMATCH') from None
    if len(attached)!=1 or attached[0]!=resolved:
        raise ValueError('LINKED_PORTFOLIO_CONNECTION_MISMATCH')
    portfolio.require_current()
    return portfolio


def snapshot(portfolio, row_limit):
    """Stream one exact logical linked-ledger fingerprint from an open snapshot."""
    if type(row_limit) is not int or row_limit<1:raise ValueError('INVALID_PORTFOLIO_SNAPSHOT_LIMIT')
    _require_current(portfolio)
    try:
        digest=hashlib.sha256(b'nexus.execution-portfolio-snapshot.v1\n');rows=0
        for table,columns,ordering in SNAPSHOT_TABLES:
            selected=','.join(part for column in columns for part in (f'typeof({column})',f'hex({column})'))
            sql=f"SELECT {selected} FROM {table} ORDER BY {','.join(ordering)}"
            table_rows=0
            for row in portfolio.execute(sql):
                rows+=1;table_rows+=1
                if rows>row_limit:raise ValueError('PORTFOLIO_SNAPSHOT_CAP_EXCEEDED')
                digest.update(json.dumps((table,*row),ensure_ascii=True,separators=(',',':')).encode()+b'\n')
            digest.update(json.dumps(('table_rows',table,table_rows),separators=(',',':')).encode()+b'\n')
    except Exception:
        _require_current(portfolio)
        raise
    _require_current(portfolio)
    return dict(rows=rows,snapshot_sha256=digest.hexdigest())


def link(portfolio, account):
    meta=dict(portfolio.execute('SELECT key,value FROM meta'))
    if 'initial' not in meta:
        if portfolio.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] or portfolio.execute('SELECT COUNT(*) FROM positions').fetchone()[0]:
            raise ValueError('NONEMPTY_UNINITIALIZED_PORTFOLIO')
        portfolio.executemany('INSERT INTO meta VALUES (?,?)', [('initial',account['initial']),('cash',account['initial']),('position_journal_version','2'),('hash_chain_version','1')])
        cur=portfolio.execute('INSERT INTO ledger(at,action,product,cash_delta) VALUES (?,?,?,?)',(int(time.time()),'INIT','',account['initial']))
        append_seal(portfolio,cur.lastrowid)
    else:
        require_safe_ledger(portfolio)
        if portfolio.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]!=1:
            raise ValueError('LINK_REQUIRES_UNUSED_PAPER_PORTFOLIO')
    compare(portfolio,account)


def compare(portfolio, account):
    _require_current(portfolio)
    try:
        require_safe_ledger(portfolio)
        meta=dict(portfolio.execute('SELECT key,value FROM meta'))
        actual={p:(Decimal(q),Decimal(e)) for p,q,e in portfolio.execute('SELECT product,quantity,entry FROM positions')}
        expected={p:(Decimal(v['quantity']),Decimal(v['entry'])) for p,v in account['positions'].items()}
        if Decimal(meta['initial'])!=Decimal(account['initial']) or Decimal(meta['cash'])!=Decimal(account['cash']) or actual!=expected:
            raise ValueError('EXECUTION_PORTFOLIO_DISAGREEMENT')
    except Exception:
        _require_current(portfolio)
        raise
    _require_current(portfolio)


def apply_fill(portfolio, order, event_id, detail, account):
    qty=Decimal(detail['quantity']);price=Decimal(detail['price']);fee=Decimal(detail['fee'])
    cash=verify_ledger(portfolio)
    old=portfolio.execute('SELECT quantity,entry FROM positions WHERE product=?',(order['product'],)).fetchone()
    entry=price if order['side']=='BUY' else Decimal(old[1])
    delta=-(qty*price+fee) if order['side']=='BUY' else qty*price-fee
    action='PAPER_FILL_'+order['side']
    if order['side']=='BUY':
        capital=Decimal(dict(portfolio.execute('SELECT key,value FROM meta'))['initial'])
        new_position=account['positions'][order['product']]
        exposure=sum((Decimal(v['quantity'])*Decimal(v['entry']) for v in account['positions'].values()),Decimal(0))
        if Decimal(new_position['quantity'])*Decimal(new_position['entry'])>capital*Decimal('.02') or exposure>capital*Decimal('.10'):
            raise ValueError('PAPER_EXPOSURE_LIMIT')
    portfolio.execute('UPDATE meta SET value=? WHERE key=?',(str(cash+delta),'cash'))
    portfolio.execute('DELETE FROM positions WHERE product=?',(order['product'],))
    position=account['positions'].get(order['product'])
    if position:
        portfolio.execute('INSERT INTO positions VALUES (?,?,?)',(order['product'],position['quantity'],position['entry']))
    cur=portfolio.execute('INSERT INTO ledger(at,action,product,cash_delta) VALUES (?,?,?,?)',(int(time.time()),action,order['product'],str(delta)))
    portfolio.execute('INSERT INTO position_events(ledger_id,action,product,quantity,entry) VALUES (?,?,?,?,?)',(cur.lastrowid,action,order['product'],str(qty),str(entry)))
    portfolio.execute('INSERT INTO execution_fills VALUES (?,?,?,?,?,?)',(cur.lastrowid,order['id'],event_id,str(qty),str(price),str(fee)))
    append_seal(portfolio,cur.lastrowid)
    compare(portfolio,account)
