"""Replay-validated JSON checkpoints; immutable command history is retained.

Fresh processes verify a checkpoint by full prefix replay. A bounded process cache
then permits state restoration when both snapshot and command-prefix hashes match.
Checksums detect isolated changes, not a coordinated local database rewrite.
"""
import hashlib
import json
import threading
from collections import OrderedDict
from nexus.order_accounting import AccountingOrderGateway

INTERVAL = 64
CACHE_LIMIT = 4
_CACHE = OrderedDict()
_LOCK = threading.RLock()


def encode(gateway):
    return json.dumps(dict(halted=gateway.halted,intents=gateway.intents,account=gateway.account,
        portfolio_linked=gateway.portfolio_linked,order_terms=gateway.order_terms,
        archived_intents=gateway.archived_intents,archive_batches=gateway.archive_batches,archival_boundary=gateway.archival_boundary,
        executions=[[list(k),v] for k,v in sorted(gateway.executions.items())],
        quote_events=[[list(k),v] for k,v in sorted(gateway.quote_events.items())]),
        sort_keys=True,separators=(',',':'),allow_nan=False)


def decode(payload):
    data=json.loads(payload)
    required={'halted','intents','account','portfolio_linked','order_terms','executions','quote_events'}
    optional={'archived_intents','archive_batches','archival_boundary'}
    if not isinstance(data,dict) or not required<=set(data) or set(data)-required-optional:
        raise ValueError('ORDER_CHECKPOINT_SCHEMA_MISMATCH')
    gateway=AccountingOrderGateway(clock=lambda:0)
    for key in ('halted','intents','account','portfolio_linked','order_terms'):
        setattr(gateway,key,data[key])
    gateway.archived_intents=data.get('archived_intents',{})
    gateway.archive_batches=data.get('archive_batches',{})
    gateway.archival_boundary=data.get('archival_boundary',0)
    gateway.executions={tuple(k):v for k,v in data['executions']}
    gateway.quote_events={tuple(k):v for k,v in data['quote_events']}
    return gateway


def prefix_digest(rows):
    digest=hashlib.sha256()
    for sequence,payload in rows:
        digest.update(json.dumps([sequence,payload],ensure_ascii=True,separators=(',',':')).encode())
        digest.update(b'\n')
    return digest.hexdigest()


def key(sequence,prefix,payload):
    return hashlib.sha256(json.dumps([sequence,prefix,payload],ensure_ascii=True,separators=(',',':')).encode()).hexdigest()


def remember(cache_key,payload):
    with _LOCK:
        _CACHE[cache_key]=payload
        _CACHE.move_to_end(cache_key)
        while len(_CACHE)>CACHE_LIMIT:_CACHE.popitem(last=False)


def clear_cache():
    with _LOCK:_CACHE.clear()


def restore(db,rows,replay):
    checkpoints=db.execute('SELECT sequence,prefix_sha256,state_json FROM order_checkpoints ORDER BY sequence DESC').fetchmany(3)
    if len(checkpoints)>2:raise ValueError('ORDER_CHECKPOINT_RETENTION_FAILED')
    if not checkpoints:
        return replay(rows),dict(checkpoint_sequence=0,prefix_replayed=len(rows),tail_replayed=0,cache_hit=False)
    sequence,prefix,payload=checkpoints[0]
    if type(sequence) is not int or not isinstance(prefix,str) or not isinstance(payload,str) or not 1<=sequence<=len(rows) or prefix_digest(rows[:sequence])!=prefix:
        raise ValueError('ORDER_CHECKPOINT_PREFIX_MISMATCH')
    cache_key=key(sequence,prefix,payload)
    with _LOCK:verified=_CACHE.get(cache_key)
    if verified is not None:
        gateway=decode(verified)
        prefix_replayed=0
    else:
        gateway=replay(rows[:sequence])
        try:normalized=encode(decode(payload))
        except (ValueError,TypeError,KeyError,AttributeError):raise ValueError('ORDER_CHECKPOINT_STATE_MISMATCH')
        if encode(gateway)!=normalized:raise ValueError('ORDER_CHECKPOINT_STATE_MISMATCH')
        remember(cache_key,payload)
        prefix_replayed=sequence
    gateway=replay(rows[sequence:],gateway)
    return gateway,dict(checkpoint_sequence=sequence,prefix_replayed=prefix_replayed,
                        tail_replayed=len(rows)-sequence,cache_hit=verified is not None)


def save(db,gateway,sequence):
    payload=encode(gateway)
    rows=db.execute('SELECT sequence,payload FROM commands WHERE sequence<=? ORDER BY sequence',(sequence,)).fetchall()
    prefix=prefix_digest(rows)
    db.execute('INSERT OR REPLACE INTO order_checkpoints VALUES (?,?,?)',(sequence,prefix,payload))
    db.execute('DELETE FROM order_checkpoints WHERE sequence NOT IN (SELECT sequence FROM order_checkpoints ORDER BY sequence DESC LIMIT 2)')
    remember(key(sequence,prefix,payload),payload)
