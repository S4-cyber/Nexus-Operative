"""Explicit read-only coverage review of retained experimental paper observations.

Declared source labels and local hashes are not market/source attestation. This
checks operational evidence only, not returns, balances or broker acceptance.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import math
import re
from pathlib import Path
import sqlite3
import stat
import time

from nexus import forward_evidence, forward_sessions
from nexus.paper_forward import SESSION_LIMIT

MINIMUM_SECONDS=72*3600
MAX_WINDOW_SECONDS=7*86400
MAX_GAP_SECONDS=10
MAX_PAYLOAD_BYTES=64*1024*1024
PUBLIC='PUBLIC_READER_SINGLE_SOURCE_UNATTESTED'
INJECTED='INJECTED_TRANSPORT_UNATTESTED'
FLAGS=('source_attested','market_realism_authenticated','account_reconciled','broker_paper_accepted',
       'strategy_validated','sustained_forward_accepted','release_approved','live_orders_enabled',
       'money_movement_enabled','automatic_start','external_owner_checked')


def _fail():raise ValueError('FORWARD_TRIAL_REVIEW_FAILED') from None


def _window(token):
    if type(token) is not str or len(token)!=20:_fail()
    value=datetime.strptime(token,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    if value.strftime('%Y-%m-%dT%H:%M:%SZ')!=token:_fail()
    return Decimal(int((value-datetime(1970,1,1,tzinfo=timezone.utc)).total_seconds()))


def _number(value):
    if type(value) not in (int,float) or not math.isfinite(value):_fail()
    return Decimal(str(value))


def _text(value):return format(value.normalize(),'f') if value else '0'


def _merge(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
        else:merged.append([start,end])
    return merged


def review(path, *, after, until):
    with localcontext() as context:
        context.prec=50
        return _review(path,after=after,until=until)


def _review(path, *, after, until):
    """Bounded one-transaction local review; no app environment or default path."""
    start,end=_window(after),_window(until)
    if not 0<end-start<=MAX_WINDOW_SECONDS:_fail()
    path=Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):_fail()
    deadline=time.monotonic()+20
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        db.execute('BEGIN')
        count,size,largest=db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(payload AS BLOB))),0),COALESCE(MAX(length(CAST(payload AS BLOB))),0) FROM forward_quotes').fetchone()
        if count>forward_evidence.LIMIT or size>MAX_PAYLOAD_BYTES or largest>8192:_fail()
        n,metadata_bytes,largest_metadata=db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(policy_json AS BLOB))+length(CAST(counts_json AS BLOB))+length(CAST(last_json AS BLOB))),0),COALESCE(MAX(MAX(length(CAST(policy_json AS BLOB)),length(CAST(counts_json AS BLOB)),length(CAST(last_json AS BLOB)))),0) FROM sessions').fetchone()
        if n>SESSION_LIMIT or metadata_bytes>8*1024*1024 or largest_metadata>65536:_fail()
        if db.execute('SELECT COALESCE(MAX(length(id)),0) FROM sessions').fetchone()[0]>16:_fail()
        if db.execute('SELECT COALESCE(MAX(MAX(length(session),length(head))),0) FROM forward_quote_heads').fetchone()[0]>64:_fail()
        rows=db.execute('SELECT id,started_at,updated_at,state,policy_json,counts_json,last_json FROM sessions ORDER BY id').fetchmany(SESSION_LIMIT+1)
        if len(rows)>SESSION_LIMIT:_fail()
        # Check metadata budgets before parsing; missing/foreign history is not repaired.
        if any(any(type(v) is not str or len(v.encode())>65536 for v in row[4:]) for row in rows):_fail()
        known={r[0] for r in rows}
        if any(type(sid) is not str or re.fullmatch('[a-f0-9]{16}',sid) is None for sid in known):_fail()
        heads=db.execute('SELECT session,observations,head FROM forward_quote_heads ORDER BY session').fetchmany(SESSION_LIMIT+1)
        if len(heads)>SESSION_LIMIT or any(r[0] not in known for r in heads):_fail()
        if any(r[0] not in known for r in db.execute('SELECT DISTINCT session FROM forward_quotes')):_fail()
        # append() stores canonical JSON. Reject duplicate/nonfinite/ambiguous JSON
        # before the existing chain reviewer, rather than accept parser last-wins.
        for payload, in db.execute('SELECT payload FROM forward_quotes'):
            if type(payload) is not str or forward_evidence.canonical(json.loads(payload))!=payload:_fail()
        intervals=[];seen=selected=eligible=public=injected=unknown=flagged=0;session_count=0
        reasons={'SOURCE_LABELS_AND_HASHES_UNATTESTED','ORDER_ACCOUNT_AND_STRATEGY_ACCEPTANCE_NOT_REVIEWED','EXECUTION_OWNERSHIP_NOT_CHECKED'}
        for sid,started,updated,state,policy_raw,counts_raw,last_raw in rows:
            policy,counts,last=forward_sessions.parse(started,updated,state,policy_raw,counts_raw,last_raw)
            expected=hashlib.sha256(forward_evidence.canonical({k:v for k,v in policy.items() if k!='policy_sha256'}).encode()).hexdigest()
            if expected!=policy['policy_sha256']:_fail()
            evidence=forward_evidence.review(db,sid)
            if not evidence['integrity_ok']:_fail()
            points=evidence['points'];seen+=len(points);previous=None;in_window=False
            for point in points:
                if point['policy_sha256']!=expected or type(point['quote_eligible']) is not bool:_fail()
                at=_number(point['evaluated_at']);source=point['source_mode']
                good=point['quote_eligible'] is True and source==PUBLIC
                if start<=at<=end:
                    in_window=True;selected+=1;eligible+=int(point['quote_eligible'])
                    public+=int(source==PUBLIC);injected+=int(source==INJECTED);unknown+=int(source not in (PUBLIC,INJECTED))
                if previous is not None:
                    old,was_good=previous;gap=at-old
                    if gap<0:_fail()
                    if good and was_good and 0<gap<=MAX_GAP_SECONDS:
                        left,right=max(start,old),min(end,at)
                        if left<right:intervals.append((left,right))
                previous=at,good
            if in_window:
                session_count+=1
                if state!='STOPPED' or last['reason']=='ORDER_CLEANUP_FAILED_REVIEW_REQUIRED':flagged+=1
        if seen!=count:_fail()
        binding=hashlib.sha256(forward_evidence.canonical(dict(sessions=rows,quote_heads=heads,quote_rows=count)).encode()).hexdigest()
    with localcontext() as context:
        context.prec=50
        merged=_merge(intervals);covered=sum((b-a for a,b in merged),Decimal(0));duration=end-start
        cursor=start;gaps=[]
        for left,right in merged:
            if left>cursor:gaps.append(left-cursor)
            cursor=max(cursor,right)
        if cursor<end:gaps.append(end-cursor)
        fraction=covered/duration
        target=duration>=MINIMUM_SECONDS and fraction>=Decimal('.95') and not flagged and not injected and not unknown and selected>0
        if duration<MINIMUM_SECONDS:reasons.add('REQUESTED_WINDOW_SHORTER_THAN_72_HOURS')
        if fraction<Decimal('.95'):reasons.add('DECLARED_PUBLIC_COVERAGE_BELOW_95_PERCENT')
        if injected:reasons.add('INJECTED_OBSERVATIONS_IN_WINDOW')
        if unknown:reasons.add('UNKNOWN_SOURCE_LABELS_IN_WINDOW')
        if flagged:reasons.add('NONSTOPPED_OR_CLEANUP_REVIEW_SESSIONS')
        if not selected:reasons.add('NO_OBSERVATIONS_IN_WINDOW')
        return dict(mode='RETAINED_FORWARD_OPERATIONAL_COVERAGE_REVIEW',status='REVIEW_REQUIRED',read_only=True,
            after=after,until=until,window_seconds=_text(duration),minimum_trial_seconds=MINIMUM_SECONDS,
            maximum_covered_gap_seconds=MAX_GAP_SECONDS,required_coverage_fraction='0.95',
            retained_observations=count,observations_in_window=selected,eligible_observations_in_window=eligible,rejected_observations_in_window=selected-eligible,
            declared_public_observations=public,injected_observations=injected,unknown_source_observations=unknown,
            sessions_in_window=session_count,nonstopped_or_cleanup_review_sessions=flagged,
            declared_public_covered_seconds=_text(covered),coverage_fraction=format(fraction.quantize(Decimal('.000001')),'f'),
            uncovered_seconds=_text(duration-covered),uncovered_intervals=len(gaps),longest_uncovered_seconds=_text(max(gaps,default=Decimal(0))),
            declared_coverage_target_met=target,evidence_snapshot_sha256=binding,
            **{flag:False for flag in FLAGS},review_reasons=sorted(reasons),
            limitations='Private local operational evidence only. Coverage counts adjacent declared-public eligible observations within one session, clips to the requested window and unions overlaps once. It does not bridge gaps over10 seconds or session boundaries. Labels/rewritable local hashes cannot attest genuine market data, uptime, broker execution, profitability, account reconciliation or a passed sustained trial. Snapshot is not an execution lock or protection from later writes. No provider/credential access, account mutation, automatic start or release permission.')


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'FORWARD_TRIAL_REVIEW_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__);parser.add_argument('--history',required=True)
    parser.add_argument('--after',required=True);parser.add_argument('--until',required=True);args=parser.parse_args()
    try:result=review(args.history,after=args.after,until=args.until)
    except (OSError,ValueError,TypeError,KeyError,ArithmeticError,sqlite3.Error,MemoryError,RecursionError):
        print('FORWARD_TRIAL_REVIEW_FAILED');return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
