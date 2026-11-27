"""Bounded append-only quote evidence and local integrity diagnostics.

Only allowlisted public quote fields are retained. Chains are locally verifiable,
not externally anchored or independent attestations of market truth.
"""
import hashlib
import json
import math
from decimal import Decimal
from datetime import datetime

LIMIT=100000
ADMISSION_LIMIT=LIMIT
FIELDS=('provider','product','bid','ask','last','received_at','exchange_time','read_only','orders_allowed')
GENESIS='0'*64
ADMISSION_TABLES=frozenset(('forward_exchange_admissions',
    'forward_exchange_admission_heads'))


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(previous,payload):return hashlib.sha256((previous+'\n'+payload).encode()).hexdigest()


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS forward_quotes(session TEXT NOT NULL,cycle INTEGER NOT NULL,payload TEXT NOT NULL,previous TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(session,cycle))')
    db.execute('CREATE TABLE IF NOT EXISTS forward_quote_heads(session TEXT PRIMARY KEY,observations INTEGER NOT NULL,head TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS forward_exchange_admissions(session TEXT NOT NULL,cycle INTEGER NOT NULL,payload TEXT NOT NULL,previous TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(session,cycle))')
    db.execute('CREATE TABLE IF NOT EXISTS forward_exchange_admission_heads(session TEXT PRIMARY KEY,admissions INTEGER NOT NULL,head TEXT NOT NULL)')


def admission_table_names(db):
    return frozenset(row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('forward_exchange_admissions','forward_exchange_admission_heads')"))


def admission_schema_valid(db):
    if admission_table_names(db)!=ADMISSION_TABLES:return False
    expected={
        'forward_exchange_admissions':(
            ('session','TEXT',1,1),('cycle','INTEGER',1,2),
            ('payload','TEXT',1,0),('previous','TEXT',1,0),
            ('digest','TEXT',1,0)),
        'forward_exchange_admission_heads':(
            ('session','TEXT',0,1),('admissions','INTEGER',1,0),
            ('head','TEXT',1,0)),
    }
    for table,columns in expected.items():
        actual=tuple((row[1],row[2].upper(),row[3],row[5]) for row in
            db.execute(f'PRAGMA table_info({table})'))
        if actual!=columns:return False
    return True


def sanitize(quote):
    if (type(quote) is not dict or
            any(type(key) is not str for key in quote)):
        return {'missing_or_invalid_quote':True}
    result={}
    for key in FIELDS:
        value=quote.get(key)
        if type(value) is str:value=value[:256]
        elif type(value) is float and not math.isfinite(value):value='NONFINITE'
        elif type(value) not in (int,float,bool,type(None)):value='UNSUPPORTED_VALUE'
        result[key]=value
    return result


def quote_sha256(quote):
    return hashlib.sha256(canonical(sanitize(quote)).encode()).hexdigest()


def initialize_exchange_admissions(db,session):
    """Mark one new-format session; legacy sessions intentionally lack a head."""
    if not admission_schema_valid(db):raise ValueError('FORWARD_EXCHANGE_ADMISSION_SCHEMA')
    if (type(session) is not str or not session or
            db.execute('SELECT 1 FROM forward_exchange_admission_heads WHERE session=?',
                (session,)).fetchone() is not None):
        raise ValueError('FORWARD_EXCHANGE_ADMISSION_SESSION')
    db.execute('INSERT INTO forward_exchange_admission_heads VALUES (?,?,?)',
        (session,0,GENESIS))


def append_exchange_admission(db,session,cycle,now,quote,source,policy_hash,
        exchange_at):
    """Commit the consumed exchange timestamp before price-guard evaluation."""
    if not admission_schema_valid(db):raise ValueError('FORWARD_EXCHANGE_ADMISSION_SCHEMA')
    if db.execute('SELECT COUNT(*) FROM forward_exchange_admissions').fetchone()[0]>=ADMISSION_LIMIT:
        raise ValueError('FORWARD_EXCHANGE_ADMISSION_CAP')
    head=db.execute('SELECT admissions,head FROM forward_exchange_admission_heads WHERE session=?',
        (session,)).fetchone()
    if head is None:raise ValueError('FORWARD_EXCHANGE_ADMISSION_SESSION')
    count,previous=head
    tail=db.execute('SELECT cycle,payload,previous,digest FROM forward_exchange_admissions WHERE session=? ORDER BY cycle DESC LIMIT 1',
        (session,)).fetchone()
    actual_count=db.execute('SELECT COUNT(*) FROM forward_exchange_admissions WHERE session=?',
        (session,)).fetchone()[0]
    if (count!=actual_count or (tail is None and count) or
            (tail is not None and (tail[3]!=previous or
                digest(tail[2],tail[1])!=tail[3] or cycle<=tail[0]))):
        raise ValueError('FORWARD_EXCHANGE_ADMISSION_HEAD_CORRUPT')
    if (type(cycle) is not int or cycle<1 or
            type(now) not in (int,float) or not math.isfinite(now) or
            type(exchange_at) not in (int,float) or not math.isfinite(exchange_at) or
            source not in ('PUBLIC_READER_SINGLE_SOURCE_UNATTESTED',
                'INJECTED_TRANSPORT_UNATTESTED') or
            type(policy_hash) is not str or len(policy_hash)!=64 or
            any(character not in '0123456789abcdef' for character in policy_hash)):
        raise ValueError('FORWARD_EXCHANGE_ADMISSION_SCHEMA')
    sanitized=sanitize(quote)
    try:
        exchange=datetime.fromisoformat(
            sanitized['exchange_time'].replace('Z','+00:00'))
        if exchange.tzinfo is None or exchange.timestamp()!=exchange_at:
            raise ValueError()
    except (KeyError,TypeError,ValueError,OverflowError,OSError):
        raise ValueError('FORWARD_EXCHANGE_ADMISSION_SCHEMA') from None
    payload=canonical(dict(session=session,cycle=cycle,evaluated_at=now,
        exchange_time=sanitized.get('exchange_time'),exchange_at=exchange_at,
        quote_sha256=quote_sha256(quote),source_mode=source,
        policy_sha256=policy_hash))
    if len(payload.encode())>2048:raise ValueError('FORWARD_EXCHANGE_ADMISSION_PAYLOAD_TOO_LARGE')
    sha=digest(previous,payload)
    db.execute('INSERT INTO forward_exchange_admissions VALUES (?,?,?,?,?)',
        (session,cycle,payload,previous,sha))
    db.execute('UPDATE forward_exchange_admission_heads SET admissions=?,head=? WHERE session=?',
        (count+1,sha,session))
    return sha


def review_exchange_admissions(db,session):
    if not admission_schema_valid(db):raise ValueError('FORWARD_EXCHANGE_ADMISSION_SCHEMA')
    rows=db.execute('SELECT cycle,payload,previous,digest FROM forward_exchange_admissions WHERE session=? ORDER BY cycle',
        (session,)).fetchmany(ADMISSION_LIMIT+1)
    head=db.execute('SELECT admissions,head FROM forward_exchange_admission_heads WHERE session=?',
        (session,)).fetchone()
    if len(rows)>ADMISSION_LIMIT:raise ValueError('FORWARD_EXCHANGE_ADMISSION_CAP_EXCEEDED')
    if head is None and not rows:return None
    previous=GENESIS
    reasons=[] if head is not None else ['EXCHANGE_ADMISSION_HEAD_MISSING']
    points=[];seen=0
    for cycle,payload,prev,sha in rows:
        if (cycle<=seen or prev!=previous or digest(previous,payload)!=sha):
            reasons.append('EXCHANGE_ADMISSION_CHAIN_DISAGREEMENT')
        try:
            data=json.loads(payload)
            if type(data) is not dict or canonical(data)!=payload:raise ValueError()
        except (ValueError,TypeError,json.JSONDecodeError):
            reasons.append('EXCHANGE_ADMISSION_PAYLOAD_INVALID');data={}
        if data.get('cycle')!=cycle or data.get('session')!=session:
            reasons.append('EXCHANGE_ADMISSION_IDENTITY_DISAGREEMENT')
        data['_digest']=sha;points.append(data);seen=cycle;previous=sha
    if head is not None and head!=(len(rows),previous):
        reasons.append('EXCHANGE_ADMISSION_HEAD_DISAGREEMENT')
    return dict(integrity_ok=not reasons,reasons=sorted(set(reasons)),
        admissions=len(rows),head_sha256=previous,
        parent_head_sha256=rows[-1][2] if rows else None,points=points)


def crosscheck_exchange_admissions(admissions,quotes,state,policy_hash):
    """Bind new-format quotes to pre-evaluation timestamp admissions."""
    if admissions is None:
        return dict(integrity_ok=False,reasons=['LEGACY_EXCHANGE_ADMISSIONS_UNBOUND'],
            matched_quotes=0,unmatched_admissions=0,admissions=0,
            head_sha256=None)
    reasons=list(admissions['reasons']);markers={
        point.get('cycle'):point for point in admissions['points']}
    quote_points={point.get('cycle'):point for point in quotes['points']}
    for marker in admissions['points']:
        try:
            exchange=datetime.fromisoformat(
                marker['exchange_time'].replace('Z','+00:00'))
            values=(marker['evaluated_at'],marker['exchange_at'])
            if (exchange.tzinfo is None or
                    any(type(value) not in (int,float) or not math.isfinite(value)
                        for value in values) or
                    exchange.timestamp()!=marker['exchange_at'] or
                    marker['policy_sha256']!=policy_hash or
                    marker['source_mode'] not in (
                        'PUBLIC_READER_SINGLE_SOURCE_UNATTESTED',
                        'INJECTED_TRANSPORT_UNATTESTED') or
                    type(marker['quote_sha256']) is not str or
                    len(marker['quote_sha256'])!=64 or
                    any(character not in '0123456789abcdef'
                        for character in marker['quote_sha256'])):
                raise ValueError()
        except (KeyError,TypeError,ValueError,OverflowError,OSError):
            reasons.append('EXCHANGE_ADMISSION_CONTENT_INVALID')
    matched=0
    for cycle,point in quote_points.items():
        marker=markers.get(cycle);binding=point.get(
            'exchange_admission_sha256')
        if binding is None:
            if point.get('quote_eligible') is True:
                reasons.append('ELIGIBLE_QUOTE_EXCHANGE_ADMISSION_MISSING')
            if marker is not None:
                reasons.append('QUOTE_EXCHANGE_ADMISSION_BINDING_MISSING')
            continue
        if marker is None or binding!=marker.get('_digest'):
            reasons.append('QUOTE_EXCHANGE_ADMISSION_DIGEST_DISAGREEMENT')
            continue
        try:
            quote=point['quote'];exchange_time=quote['exchange_time']
            exchange=datetime.fromisoformat(exchange_time.replace('Z','+00:00'))
            if (exchange.tzinfo is None or
                    marker.get('session')!=point.get('session') or
                    marker.get('cycle')!=cycle or
                    marker.get('evaluated_at')!=point.get('evaluated_at') or
                    marker.get('exchange_time')!=exchange_time or
                    marker.get('exchange_at')!=exchange.timestamp() or
                    marker.get('quote_sha256')!=quote_sha256(quote) or
                    marker.get('source_mode')!=point.get('source_mode') or
                    marker.get('policy_sha256')!=point.get('policy_sha256') or
                    marker.get('policy_sha256')!=policy_hash):
                raise ValueError()
        except (KeyError,TypeError,ValueError,OverflowError,OSError):
            reasons.append('QUOTE_EXCHANGE_ADMISSION_CONTENT_DISAGREEMENT')
            continue
        matched+=1
    missing=[cycle for cycle in markers if cycle not in quote_points]
    if missing:
        reasons.append('EXCHANGE_ADMISSION_OUTCOME_MISSING')
        if (state!='RUNNING' or len(missing)!=1 or
                missing[0]!=max(markers)):
            reasons.append('EXCHANGE_ADMISSION_NONTERMINAL_GAP')
    return dict(integrity_ok=not reasons,reasons=sorted(set(reasons)),
        matched_quotes=matched,unmatched_admissions=len(missing),
        admissions=admissions['admissions'],
        head_sha256=admissions['head_sha256'])


def append(db,session,cycle,now,quote,eligible,source,policy_hash,error=None,
        exchange_admission_sha256=None):
    if db.execute('SELECT COUNT(*) FROM forward_quotes').fetchone()[0]>=LIMIT:
        raise ValueError('FORWARD_QUOTE_EVIDENCE_CAP')
    old=db.execute('SELECT observations,head FROM forward_quote_heads WHERE session=?',(session,)).fetchone()
    count,previous=old if old else (0,GENESIS)
    tail=db.execute('SELECT payload,previous,digest FROM forward_quotes WHERE session=? ORDER BY cycle DESC LIMIT 1',(session,)).fetchone()
    actual_count=db.execute('SELECT COUNT(*) FROM forward_quotes WHERE session=?',(session,)).fetchone()[0]
    if count!=actual_count or (tail is None and count) or (tail is not None and (tail[2]!=previous or digest(tail[1],tail[0])!=tail[2])):
        raise ValueError('FORWARD_QUOTE_HEAD_CORRUPT')
    data=dict(session=session,cycle=cycle,evaluated_at=now,quote=sanitize(quote),
        quote_eligible=eligible,source_mode=source,policy_sha256=policy_hash,
        error_type=error)
    if exchange_admission_sha256 is not None:
        if (type(exchange_admission_sha256) is not str or
                len(exchange_admission_sha256)!=64):
            raise ValueError('FORWARD_EXCHANGE_ADMISSION_BINDING')
        data['exchange_admission_sha256']=exchange_admission_sha256
    payload=canonical(data)
    sha=digest(previous,payload)
    db.execute('INSERT INTO forward_quotes VALUES (?,?,?,?,?)',(session,cycle,payload,previous,sha))
    db.execute('INSERT OR REPLACE INTO forward_quote_heads VALUES (?,?,?)',(session,count+1,sha))
    return sha


def review(db,session):
    rows=db.execute('SELECT cycle,payload,previous,digest FROM forward_quotes WHERE session=? ORDER BY cycle',(session,)).fetchmany(LIMIT+1)
    head=db.execute('SELECT observations,head FROM forward_quote_heads WHERE session=?',(session,)).fetchone()
    if len(rows)>LIMIT:raise ValueError('FORWARD_EVIDENCE_CAP_EXCEEDED')
    previous=GENESIS;reasons=[];points=[];seen=0
    for cycle,payload,prev,sha in rows:
        if cycle<=seen or prev!=previous or digest(previous,payload)!=sha:reasons.append('QUOTE_CHAIN_DISAGREEMENT')
        data=json.loads(payload)
        if data.get('cycle')!=cycle or data.get('session')!=session:reasons.append('QUOTE_IDENTITY_DISAGREEMENT')
        # The retained row digest is needed by the private three-way
        # quote/event/order reconciler.  Public evidence responses remove the
        # complete points collection, so this never expands their payload.
        data['_digest']=sha
        points.append(data);seen=cycle;previous=sha
    if head is None:
        if rows:reasons.append('QUOTE_HEAD_MISSING')
    elif head!=(len(rows),previous):reasons.append('QUOTE_HEAD_DISAGREEMENT')
    eligible=[p for p in points if p['quote_eligible']]
    times=[p['evaluated_at'] for p in points]
    gaps=[b-a for a,b in zip(times,times[1:])]
    if any(g<0 for g in gaps):reasons.append('OBSERVATION_TIME_REGRESSION')
    return dict(integrity_ok=not reasons,reasons=sorted(set(reasons)),observations=len(rows),
                storage_total=db.execute('SELECT COUNT(*) FROM forward_quotes').fetchone()[0],storage_limit=LIMIT,
                eligible_observations=len(eligible),rejected_observations=len(rows)-len(eligible),
                distinct_eligible_exchange_timestamps=len(set(p['quote'].get('exchange_time') for p in eligible)),
                repeated_eligible_exchange_timestamps=len(eligible)-len(set(p['quote'].get('exchange_time') for p in eligible)),
                observed_span_seconds=round(max(times)-min(times),6) if times else 0,
                maximum_gap_seconds=round(max(gaps),6) if gaps else None,
                gaps_over_ten_seconds=sum(g>10 for g in gaps),
                source_modes=sorted(set(p['source_mode'] for p in points)),head_sha256=previous,
                parent_head_sha256=rows[-1][2] if rows else None,
                points=points)


def crosscheck(quotes,orders,counts,policy):
    indexed={p['cycle']:p for p in quotes['points']};reasons=[];fills={'BUY':0,'SELL':0}
    totals={'BUY':Decimal(0),'SELL':Decimal(0)};fees=Decimal(0)
    for item in orders:
        order=item['order']
        if Decimal(order['simulated_filled_notional'])==0:continue
        fills[order['side']]+=(len(item['quote_events']) if
            order.get('intent_scope')=='POSITION_REDUCING_EXIT' else 1)
        try:
            cycle=int(order['id'].split('_')[2])
            for execution in item['quote_events']:
                point=indexed[execution.get('evidence_cycle',cycle)]
                if not point['quote_eligible']:raise ValueError()
                assumptions=execution['assumptions'];quote=point['quote']
                for model,field in (('bid','bid'),('ask','ask'),('quote_at','received_at')):
                    if Decimal(assumptions[model])!=Decimal(str(quote[field])):raise ValueError()
            if not item['quote_events']:raise ValueError()
        except (ValueError,KeyError,TypeError,ArithmeticError):reasons.append('FILL_QUOTE_EVIDENCE_DISAGREEMENT')
        try:
            cycle=int(order['id'].split('_')[2])
            for execution in item['quote_events']:
                quote=indexed[execution.get('evidence_cycle',cycle)]['quote']
                stamp=quote['exchange_time']
                if not isinstance(stamp,str):raise ValueError()
                exchange=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if exchange.tzinfo is None:raise ValueError()
                exchange_at=Decimal(str(exchange.timestamp()))
                evaluated=Decimal(execution['assumptions']['evaluated_at'])
                age=evaluated-exchange_at
                if not age.is_finite() or not 0<=age<=Decimal(str(policy['exchange_age_seconds'])):raise ValueError()
        except (ValueError,KeyError,TypeError,ArithmeticError,OverflowError,IndexError):
            reasons.append('FILL_EXCHANGE_TIME_DISAGREEMENT')
        try:
            notional=Decimal(0);cost=Decimal(0);sequences=set()
            for execution in item['quote_events']:
                a=execution['assumptions']
                qty=Decimal(execution['quantity']);price=Decimal(execution['price']);fee=Decimal(execution['fee'])
                bid=Decimal(a['bid']);ask=Decimal(a['ask']);capacity=Decimal(a['available_quantity'])
                fee_bps=Decimal(a['fee_bps']);slip=Decimal(a['slippage_bps'])
                age=Decimal(a['evaluated_at'])-Decimal(a['quote_at'])
                values=(qty,price,fee,bid,ask,capacity,fee_bps,slip,age)
                if not all(v.is_finite() for v in values):raise ValueError()
                if qty<=0 or price<=0 or bid<=0 or ask<bid or fee<0 or capacity<qty:raise ValueError()
                if not 0<=age<Decimal(str(policy['order_lifetime_seconds'])):raise ValueError()
                if fee_bps!=Decimal(str(policy['fee_bps'])) or slip!=Decimal(str(policy['slippage_bps'])):raise ValueError()
                expected=ask*(1+slip/10000) if order['side']=='BUY' else bid*(1-slip/10000)
                if price!=expected or fee!=qty*price*fee_bps/10000:raise ValueError()
                sequence=execution['sequence']
                if type(sequence) is not int or sequence<1 or sequence in sequences:raise ValueError()
                sequences.add(sequence);notional+=qty*price;cost+=fee
            if not item['quote_events'] or notional!=Decimal(order['simulated_filled_notional']) or cost!=Decimal(order['simulated_fee_total']):raise ValueError()
            totals[order['side']]+=notional;fees+=cost
        except (ValueError,KeyError,TypeError,ArithmeticError):reasons.append('FILL_MODEL_OR_POLICY_DISAGREEMENT')
    if any(counts.get(side,0)!=fills[side] for side in ('BUY','SELL')):reasons.append('SESSION_COUNTER_FILL_DISAGREEMENT')
    return dict(integrity_ok=not reasons,reasons=sorted(set(reasons)),modeled_buy_orders=fills['BUY'],modeled_sell_orders=fills['SELL'],
                modeled_buy_notional=str(totals['BUY']),modeled_sell_notional=str(totals['SELL']),modeled_fees=str(fees),
                totals_scope='Consistent modeled execution events only; not a profit or available-liquidity assessment.')
