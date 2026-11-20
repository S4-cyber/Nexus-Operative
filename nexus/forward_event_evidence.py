"""Durable, bounded forward-runner event evidence.

The short ``cycles`` table is a UI projection.  This chain is the retained
decision/lifecycle record used by trial review.  It is locally verifiable but
is not an external timestamp, source attestation, or trading permission.
"""
import hashlib
import json
import math


LIMIT=203000
GENESIS='0'*64
KINDS=frozenset(('LIFECYCLE','OUTCOME','SCHEDULER'))
TABLES=frozenset(('forward_events','forward_event_heads'))
BASE_FIELDS=frozenset(('at','clock_sample_valid','session','cycle',
    'quote_evidence_sha256','decision','reason','product','tier',
    'simulation_only','policy_id','policy_sha256'))
EXTRA_FIELDS=frozenset(('order_id','liquidity','strategy_validated',
    'consecutive_failures','error_type','cleanup_acknowledged',
    'order_ledger_authoritative','scheduler_clock_valid',
    'scheduler_interval_seconds','scheduler_cycle_duration_seconds',
    'scheduler_overrun_seconds','burst_catchup_allowed'))


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(previous,payload):
    return hashlib.sha256((previous+'\n'+payload).encode()).hexdigest()


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS forward_events(session TEXT NOT NULL,sequence INTEGER NOT NULL,cycle INTEGER NOT NULL,ordinal INTEGER NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,previous TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(session,sequence),UNIQUE(session,cycle,ordinal))')
    db.execute('CREATE TABLE IF NOT EXISTS forward_event_heads(session TEXT PRIMARY KEY,events INTEGER NOT NULL,head TEXT NOT NULL)')


def table_names(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('forward_events','forward_event_heads')")}


def tables_present(db):return table_names(db)==TABLES


def schema_valid(db):
    if not tables_present(db):return False
    expected_events=(('session','TEXT',1,1),('sequence','INTEGER',1,2),
        ('cycle','INTEGER',1,0),('ordinal','INTEGER',1,0),('kind','TEXT',1,0),
        ('payload','TEXT',1,0),('previous','TEXT',1,0),('digest','TEXT',1,0))
    expected_heads=(('session','TEXT',0,1),('events','INTEGER',1,0),('head','TEXT',1,0))
    observed=lambda table:tuple((row[1],row[2].upper(),row[3],row[5])
        for row in db.execute('PRAGMA table_info('+table+')'))
    return observed('forward_events')==expected_events and observed('forward_event_heads')==expected_heads


def classify(record):
    if record['decision'] in ('START','STOP'):
        return 'LIFECYCLE'
    if (record['reason'].startswith('SCHEDULER_') or
            record['reason'] in ('INVALID_SCHEDULER_CLOCK','WORKER_CYCLE_CONCURRENCY')):
        return 'SCHEDULER'
    if record['quote_evidence_sha256'] is None:return 'LIFECYCLE'
    return 'OUTCOME'


def _valid_scalar(value):
    return (value is None or type(value) in (str,int,bool) or
        type(value) is float and math.isfinite(value))


def validate_record(record):
    if type(record) is not dict or not BASE_FIELDS<=record.keys() or not record.keys()<=BASE_FIELDS|EXTRA_FIELDS:
        raise ValueError('FORWARD_EVENT_SCHEMA')
    if any(type(key) is not str or not _valid_scalar(value) for key,value in record.items()):
        raise ValueError('FORWARD_EVENT_SCHEMA')
    if (type(record['session']) is not str or len(record['session'])!=16 or
            any(char not in '0123456789abcdef' for char in record['session']) or
            type(record['cycle']) is not int or record['cycle']<0 or
            type(record['at']) not in (int,float) or not math.isfinite(record['at']) or
            not 0<=record['at']<253402300799 or
            type(record['decision']) is not str or not record['decision'] or len(record['decision'])>32 or
            type(record['reason']) is not str or not record['reason'] or len(record['reason'])>128 or
            type(record['policy_sha256']) is not str or len(record['policy_sha256'])!=64 or
            any(char not in '0123456789abcdef' for char in record['policy_sha256']) or
            record['simulation_only'] is not True or
            type(record['clock_sample_valid']) is not bool):
        raise ValueError('FORWARD_EVENT_SCHEMA')
    quote=record['quote_evidence_sha256']
    if quote is not None and (type(quote) is not str or len(quote)!=64 or
            any(char not in '0123456789abcdef' for char in quote)):
        raise ValueError('FORWARD_EVENT_SCHEMA')
    return record


def append(db,record,kind=None):
    validate_record(record);kind=classify(record) if kind is None else kind
    if kind not in KINDS:raise ValueError('FORWARD_EVENT_KIND')
    if db.execute('SELECT COALESCE(SUM(events),0) FROM forward_event_heads').fetchone()[0]>=LIMIT:
        raise ValueError('FORWARD_EVENT_EVIDENCE_CAP')
    session=record['session'];cycle=record['cycle']
    old=db.execute('SELECT events,head FROM forward_event_heads WHERE session=?',(session,)).fetchone()
    count,previous=old if old else (0,GENESIS)
    tail=db.execute('SELECT sequence,payload,previous,digest FROM forward_events WHERE session=? ORDER BY sequence DESC LIMIT 1',(session,)).fetchone()
    if (tail is None and count) or (tail is not None and
            (tail[0]!=count or tail[3]!=previous or digest(tail[2],tail[1])!=tail[3])):
        raise ValueError('FORWARD_EVENT_HEAD_CORRUPT')
    ordinal=db.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM forward_events WHERE session=? AND cycle=?',(session,cycle)).fetchone()[0]
    payload=canonical(dict(kind=kind,record=record))
    if len(payload.encode())>2048:raise ValueError('FORWARD_EVENT_PAYLOAD_TOO_LARGE')
    sha=digest(previous,payload);sequence=count+1
    db.execute('INSERT INTO forward_events VALUES (?,?,?,?,?,?,?,?)',
        (session,sequence,cycle,ordinal,kind,payload,previous,sha))
    db.execute('INSERT OR REPLACE INTO forward_event_heads VALUES (?,?,?)',
        (session,sequence,sha))
    return sha


def review(db,session):
    rows=db.execute('SELECT sequence,cycle,ordinal,kind,payload,previous,digest FROM forward_events WHERE session=? ORDER BY sequence',(session,)).fetchmany(LIMIT+1)
    head=db.execute('SELECT events,head FROM forward_event_heads WHERE session=?',(session,)).fetchone()
    reasons=[];points=[];previous=GENESIS;last_cycle=-1;last_ordinal=0
    if len(rows)>LIMIT:reasons.append('EVENT_CAP_EXCEEDED')
    for sequence,cycle,ordinal,kind,payload,parent,sha in rows[:LIMIT]:
        try:
            data=json.loads(payload)
            if canonical(data)!=payload:raise ValueError()
            record=validate_record(data['record'])
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            reasons.append('EVENT_PAYLOAD_INVALID');continue
        if (sequence!=len(points)+1 or parent!=previous or digest(previous,payload)!=sha):
            reasons.append('EVENT_CHAIN_DISAGREEMENT')
        if (kind not in KINDS or data.get('kind')!=kind or record['session']!=session or
                record['cycle']!=cycle or kind!=classify(record)):
            reasons.append('EVENT_IDENTITY_DISAGREEMENT')
        expected_ordinal=last_ordinal+1 if cycle==last_cycle else 1
        if cycle<last_cycle or ordinal!=expected_ordinal:
            reasons.append('EVENT_ORDER_DISAGREEMENT')
        points.append(dict(sequence=sequence,cycle=cycle,ordinal=ordinal,
            kind=kind,record=record,digest=sha))
        previous=sha;last_cycle=cycle;last_ordinal=ordinal
    if head is None:
        if rows:reasons.append('EVENT_HEAD_MISSING')
    elif head!=(len(rows),previous):reasons.append('EVENT_HEAD_DISAGREEMENT')
    return dict(integrity_ok=not reasons,reasons=sorted(set(reasons)),events=len(rows),
        head_sha256=previous,points=points)
