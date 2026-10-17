"""Bounded append-only quote evidence and local integrity diagnostics.

Only allowlisted public quote fields are retained. Chains are locally verifiable,
not externally anchored or independent attestations of market truth.
"""
import hashlib
import json
import math
from decimal import Decimal

LIMIT=100000
FIELDS=('provider','product','bid','ask','last','received_at','exchange_time','read_only','orders_allowed')
GENESIS='0'*64


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(previous,payload):return hashlib.sha256((previous+'\n'+payload).encode()).hexdigest()


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS forward_quotes(session TEXT NOT NULL,cycle INTEGER NOT NULL,payload TEXT NOT NULL,previous TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(session,cycle))')
    db.execute('CREATE TABLE IF NOT EXISTS forward_quote_heads(session TEXT PRIMARY KEY,observations INTEGER NOT NULL,head TEXT NOT NULL)')


def sanitize(quote):
    if not isinstance(quote,dict):return {'missing_or_invalid_quote':True}
    result={}
    for key in FIELDS:
        value=quote.get(key)
        if isinstance(value,str):value=value[:256]
        elif isinstance(value,float) and not math.isfinite(value):value='NONFINITE'
        elif type(value) not in (int,float,bool,type(None)):value='UNSUPPORTED_VALUE'
        result[key]=value
    return result


def append(db,session,cycle,now,quote,eligible,source,policy_hash,error=None):
    if db.execute('SELECT COUNT(*) FROM forward_quotes').fetchone()[0]>=LIMIT:
        raise ValueError('FORWARD_QUOTE_EVIDENCE_CAP')
    old=db.execute('SELECT observations,head FROM forward_quote_heads WHERE session=?',(session,)).fetchone()
    count,previous=old if old else (0,GENESIS)
    tail=db.execute('SELECT payload,previous,digest FROM forward_quotes WHERE session=? ORDER BY cycle DESC LIMIT 1',(session,)).fetchone()
    actual_count=db.execute('SELECT COUNT(*) FROM forward_quotes WHERE session=?',(session,)).fetchone()[0]
    if count!=actual_count or (tail is None and count) or (tail is not None and (tail[2]!=previous or digest(tail[1],tail[0])!=tail[2])):
        raise ValueError('FORWARD_QUOTE_HEAD_CORRUPT')
    payload=canonical(dict(session=session,cycle=cycle,evaluated_at=now,quote=sanitize(quote),
                           quote_eligible=eligible,source_mode=source,policy_sha256=policy_hash,error_type=error))
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
                points=points)


def crosscheck(quotes,orders,counts):
    indexed={p['cycle']:p for p in quotes['points']};reasons=[];fills={'BUY':0,'SELL':0}
    for item in orders:
        order=item['order']
        if Decimal(order['simulated_filled_notional'])==0:continue
        fills[order['side']]+=1
        try:
            cycle=int(order['id'].split('_')[2]);point=indexed[cycle]
            if not point['quote_eligible']:raise ValueError()
            for execution in item['quote_events']:
                assumptions=execution['assumptions'];quote=point['quote']
                for model,field in (('bid','bid'),('ask','ask'),('quote_at','received_at')):
                    if Decimal(assumptions[model])!=Decimal(str(quote[field])):raise ValueError()
            if not item['quote_events']:raise ValueError()
        except (ValueError,KeyError,TypeError,ArithmeticError):reasons.append('FILL_QUOTE_EVIDENCE_DISAGREEMENT')
    if any(counts.get(side,0)!=fills[side] for side in ('BUY','SELL')):reasons.append('SESSION_COUNTER_FILL_DISAGREEMENT')
    return dict(integrity_ok=not reasons,reasons=sorted(set(reasons)),modeled_buy_orders=fills['BUY'],modeled_sell_orders=fills['SELL'])
