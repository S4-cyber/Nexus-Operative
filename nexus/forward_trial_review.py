"""Explicit read-only coverage review of retained experimental paper observations.

Declared source labels and local hashes are not market/source attestation. This
checks operational evidence only, not returns, balances or broker acceptance.
"""
import argparse
from contextlib import closing,contextmanager
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import math
import re
from pathlib import Path
import sqlite3
import os
import time

from nexus import forward_evidence, forward_event_evidence, forward_sessions
from nexus.paper_forward import SESSION_LIMIT
from nexus import feed_guard as feed_policy
from nexus.feed_guard import FeedGuard
from nexus.paper_policy import admit
from nexus.sqlite_journal_guard import (capture_file_fingerprints,
    capture_file_identities, guard_file_set_finalization,
    require_file_fingerprints, require_file_identities)

MINIMUM_SECONDS=72*3600
MAX_WINDOW_SECONDS=7*86400
MAX_GAP_SECONDS=10
MAX_PAYLOAD_BYTES=64*1024*1024
MAX_DATABASE_BYTES=1024*1024*1024
DB_BUSY_TIMEOUT_MS=10_000
PUBLIC='PUBLIC_READER_SINGLE_SOURCE_UNATTESTED'
INJECTED='INJECTED_TRANSPORT_UNATTESTED'
FLAGS=('source_attested','market_realism_authenticated','account_reconciled','broker_paper_accepted',
       'strategy_validated','sustained_forward_accepted','release_approved','live_orders_enabled',
       'money_movement_enabled','automatic_start','external_owner_checked')
LIMITATIONS=('Private local operational evidence only. Coverage requires both a recorded eligible flag and replay of retained '
    'single-source feed, product, permission, numeric/spread/jump, receive/exchange-age and exchange-regression checks under '
    'the exact admitted policy at the recorded evaluation time. Rejected flags are never upgraded. Adjacent declared-public '
    'valid points are clipped to the window and overlaps unioned once; every in-window quote must retain a matching durable '
    'pre-evaluation exchange-admission chain, and exchange time must also advance across retained '
    'session boundaries, and a repeated or regressed point removes both adjacent candidate intervals; every point and terminal event must remain bound '
    'to its recorded session lifecycle; gaps over10 seconds, invalid points and session '
    'boundaries are not bridged, and over-limit uncovered gaps remain operational incidents even when aggregate coverage '
    'exceeds 95%. This rechecks supplied content, not independent feeds, clock authenticity, genuine market '
    'data, uptime, broker execution, profitability, account reconciliation or a passed sustained trial. Labels/hashes are '
    'rewritable; sampled file identity/fingerprint binding and the read snapshot are not an execution lock. No provider/credential access, account mutation, automatic start or '
    'release permission.')
PUBLIC_KEYS=frozenset(('mode','status','read_only','after','until','window_seconds','minimum_trial_seconds',
    'maximum_covered_gap_seconds','required_coverage_fraction','retained_observations','retained_sessions',
    'observations_in_window','eligible_observations_in_window','rejected_observations_in_window','eligibility_count_scope',
    'retained_quote_recheck_applied','revalidated_eligible_observations_in_window',
    'revalidated_rejected_observations_in_window','recorded_eligibility_disagreements_in_window',
    'declared_public_observations','injected_observations','unknown_source_observations','sessions_in_window',
    'nonstopped_or_cleanup_review_sessions','declared_public_covered_seconds','coverage_fraction','uncovered_seconds',
    'uncovered_intervals','longest_uncovered_seconds','declared_coverage_target_met',
    'clean_manual_stop_sessions','other_stopped_sessions','paused_sessions','running_sessions',
    'sessions_with_quote_errors','quote_error_observations','over_limit_coverage_gaps',
    'over_limit_coverage_gap_seconds','operational_incidents_present',
    'retained_event_records','events_in_window','legacy_unbound_observations_in_window',
    'quote_outcome_disagreements_in_window','event_incident_records_in_window',
    'retained_exchange_admissions','exchange_admissions_in_window',
    'exchange_admission_incidents_in_window',
    'event_trace_complete','incident_free_coverage_target_met','evidence_snapshot_sha256',
    *FLAGS,'review_reasons','limitations'))


def existing_history(path):
    from nexus.sqlite_journal_guard import check_existing
    path=Path(path)
    if os.path.lexists(str(path)+'-journal'):_fail()
    return check_existing(path,MAX_DATABASE_BYTES,'TRIAL_')


def _fail():raise ValueError('FORWARD_TRIAL_REVIEW_FAILED') from None


def _database_busy(error):
    code=getattr(error,'sqlite_errorcode',None)
    return (code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or
        any(word in str(error).lower() for word in ('busy','locked')))


@contextmanager
def _snapshot(path, stable_source):
    """Open one bounded read snapshot and redact exhausted lock contention."""
    db=None
    try:
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,
            timeout=DB_BUSY_TIMEOUT_MS/1000)
        db.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
        db.execute('BEGIN')
        stable_source()
        try:
            opened=[Path(filename).resolve(strict=True) for _,name,filename in
                db.execute('PRAGMA database_list') if name=='main']
        except OSError:_fail()
        if len(opened)!=1 or opened[0]!=path:_fail()
        yield db
    except sqlite3.OperationalError as error:
        if _database_busy(error):_fail()
        raise
    finally:
        if db is not None:db.close()


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


def _quote_recheck(point, policy, guard, previous_exchange):
    """Replay retained-field heuristics, never upgrade a rejected observation.

    This is another check on the same supplied source, not independent market
    verification. Use the recorded evaluation time, not the reviewer's clock.
    """
    try:
        quote=point['quote'];now=point['evaluated_at']
        if not isinstance(quote,dict):return False,previous_exchange,None
        if quote.get('product')!=policy['product']:
            return False,previous_exchange,None
        received=_number(quote['received_at']);at=_number(now)
        stamp=quote['exchange_time']
        if not isinstance(stamp,str):return False,previous_exchange,None
        exchange=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if exchange.tzinfo is None:return False,previous_exchange,None
        exchanged=_number(exchange.timestamp())
        if not (0<=at-received<=policy['receive_age_seconds'] and
            0<=at-exchanged<=policy['exchange_age_seconds']):
            return False,previous_exchange,None
        # Every structurally valid fresh tick consumes the marker. Rejected
        # prices never become a guard baseline, but their exchange timestamp
        # cannot be replayed later with altered prices.
        if previous_exchange is not None and exchanged<=previous_exchange:
            return False,previous_exchange,exchanged
        previous_exchange=exchanged
        if (point['quote_eligible'] is not True or
                point.get('error_type') is not None):
            return False,previous_exchange,exchanged
        feed=guard.evaluate(quote,now)
        if feed['decision']=='VETO':return False,previous_exchange,exchanged
        return True,previous_exchange,exchanged
    except (ValueError,TypeError,KeyError,ArithmeticError,OverflowError,OSError):
        return False,previous_exchange,None


def review(path, *, after, until):
    with localcontext() as context:
        context.prec=50
        return _review(path,after=after,until=until)


def _review(path, *, after, until):
    """Bind a result to one exact retained file through finalization."""
    start,end=_window(after),_window(until)
    if not 0<end-start<=MAX_WINDOW_SECONDS:_fail()
    path=Path(path)
    if not existing_history(path):raise FileNotFoundError('FORWARD_TRIAL_REVIEW_FAILED')
    try:
        identities=capture_file_identities((path,),'FORWARD_TRIAL_REVIEW_')
        pinned=Path(identities[0][0])
        paths=(pinned,);limits=(MAX_DATABASE_BYTES,)
        fingerprints=capture_file_fingerprints(paths,limits,
            'FORWARD_TRIAL_REVIEW_')
        def stable_source():
            try:
                require_file_identities(paths,identities,
                    'FORWARD_TRIAL_REVIEW_')
                require_file_fingerprints(paths,limits,fingerprints,
                    'FORWARD_TRIAL_REVIEW_')
            except ValueError:_fail()
        with guard_file_set_finalization(paths,limits,identities,fingerprints,
                'FORWARD_TRIAL_REVIEW_'):
            result=_review_bound(pinned,after=after,until=until,start=start,end=end,
                stable_source=stable_source)
        return result
    except ValueError:_fail()


def _review_bound(path, *, after, until, start, end, stable_source):
    """Bounded one-transaction local review; no app environment or default path."""
    deadline=time.monotonic()+20
    with _snapshot(path,stable_source) as db:
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
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
        event_names={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('forward_events','forward_event_heads')")}
        if event_names not in (set(),{'forward_events','forward_event_heads'}):_fail()
        event_tables=bool(event_names)
        if event_tables and not forward_event_evidence.schema_valid(db):_fail()
        event_count=0;event_heads=[]
        if event_tables:
            event_count,event_bytes,event_largest=db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(payload AS BLOB))),0),COALESCE(MAX(length(CAST(payload AS BLOB))),0) FROM forward_events').fetchone()
            if event_count>forward_event_evidence.LIMIT or event_bytes>512*1024*1024 or event_largest>2048:_fail()
            event_heads=db.execute('SELECT session,events,head FROM forward_event_heads ORDER BY session').fetchmany(SESSION_LIMIT+1)
            if len(event_heads)>SESSION_LIMIT or any(r[0] not in known for r in event_heads):_fail()
            if any(r[0] not in known for r in db.execute('SELECT DISTINCT session FROM forward_events')):_fail()
        admission_names=forward_evidence.admission_table_names(db)
        if (admission_names and
                (admission_names!=forward_evidence.ADMISSION_TABLES or
                    not forward_evidence.admission_schema_valid(db))):_fail()
        admission_count=0;admission_heads=[]
        if admission_names:
            admission_count,admission_bytes,admission_largest=db.execute(
                'SELECT COUNT(*),COALESCE(SUM(length(CAST(payload AS BLOB))),0),COALESCE(MAX(length(CAST(payload AS BLOB))),0) FROM forward_exchange_admissions').fetchone()
            if (admission_count>forward_evidence.ADMISSION_LIMIT or
                    admission_bytes>256*1024*1024 or admission_largest>2048):_fail()
            admission_heads=db.execute(
                'SELECT session,admissions,head FROM forward_exchange_admission_heads ORDER BY session').fetchmany(SESSION_LIMIT+1)
            if (len(admission_heads)>SESSION_LIMIT or
                    any(row[0] not in known for row in admission_heads)):_fail()
            if any(row[0] not in known for row in db.execute(
                    'SELECT DISTINCT session FROM forward_exchange_admissions')):_fail()
        # append() stores canonical JSON. Reject duplicate/nonfinite/ambiguous JSON
        # before the existing chain reviewer, rather than accept parser last-wins.
        for payload, in db.execute('SELECT payload FROM forward_quotes'):
            if type(payload) is not str or forward_evidence.canonical(json.loads(payload))!=payload:_fail()
        interval_candidates=[];cross_session_points=[];seen=selected=eligible=revalidated=disagreements=public=injected=unknown=flagged=0;session_count=0
        events_in_window=legacy_unbound=outcome_disagreements=event_incidents=0
        admissions_in_window=admission_incidents=0;cross_session_admissions=[]
        legacy_admission_incidents=unmatched_admission_incidents=0
        event_trace_complete=True
        clean_stopped=other_stopped=paused=running=quote_error_sessions=quote_errors=0
        reasons={'SOURCE_LABELS_AND_HASHES_UNATTESTED','ORDER_ACCOUNT_AND_STRATEGY_ACCEPTANCE_NOT_REVIEWED','EXECUTION_OWNERSHIP_NOT_CHECKED'}
        for sid,started,updated,state,policy_raw,counts_raw,last_raw in rows:
            policy,counts,last=forward_sessions.parse(started,updated,state,policy_raw,counts_raw,last_raw)
            session_start,session_end=_number(started),_number(updated)
            expected=hashlib.sha256(forward_evidence.canonical({k:v for k,v in policy.items() if k!='policy_sha256'}).encode()).hexdigest()
            if expected!=policy['policy_sha256']:_fail()
            if policy!=admit(policy.get('id')):_fail()
            evidence=forward_evidence.review(db,sid)
            if not evidence['integrity_ok']:_fail()
            points=evidence['points'];seen+=len(points);previous=None
            admission=(forward_evidence.review_exchange_admissions(db,sid)
                if admission_names else None)
            session_admission_incidents=0
            if admission is None:
                session_admission_incidents=sum(
                    start<=_number(point['evaluated_at'])<=end for point in points)
                legacy_admission_incidents+=session_admission_incidents
                admission_incidents+=session_admission_incidents
            else:
                admission_check=forward_evidence.crosscheck_exchange_admissions(
                    admission,evidence,state,policy['policy_sha256'])
                severe=[reason for reason in admission_check['reasons'] if
                    reason!='EXCHANGE_ADMISSION_OUTCOME_MISSING']
                if severe:_fail()
                for marker in admission['points']:
                    marker_at=_number(marker['evaluated_at'])
                    marker_exchange=_number(marker['exchange_at'])
                    cross_session_admissions.append((marker_at,sid,
                        marker['cycle'],marker_exchange))
                    if start<=marker_at<=end:
                        admissions_in_window+=1
                if admission_check['unmatched_admissions']:
                    session_admission_incidents=sum(start<=_number(marker['evaluated_at'])<=end
                        for marker in admission['points']
                        if marker['cycle'] not in {point['cycle'] for point in points})
                    unmatched_admission_incidents+=session_admission_incidents
                    admission_incidents+=session_admission_incidents
            # RUNNING has no terminal lifecycle end. Its last retained update
            # cannot make an unresolved session disappear from a later review.
            lifecycle_in_window=session_start<=end and (state=='RUNNING' or session_end>=start)
            terminal_reasons=forward_sessions.binding_reasons(last,state,sid,policy,
                evidence['head_sha256'],len(points),points[-1]['cycle'] if points else 0,
                evidence['parent_head_sha256'])
            if terminal_reasons:_fail()
            session_event_complete=event_tables
            if event_tables:
                events=forward_event_evidence.review(db,sid)
                event_points=events['points']
                if not events['integrity_ok']:session_event_complete=False
                if event_points:
                    first_event=event_points[0]['record'];last_event=event_points[-1]['record']
                    if first_event['decision']!='START' or first_event['cycle']!=0 or last_event!=last:
                        session_event_complete=False
                    event_counts={}
                    previous_event_at=None;previous_event_cycle=-1
                    for event in event_points:
                        record=event['record'];event_at=_number(record['at'])
                        event_counts[record['decision']]=event_counts.get(record['decision'],0)+1
                        if (not session_start<=event_at<=session_end or
                                previous_event_at is not None and event_at<previous_event_at or
                                record['cycle']<previous_event_cycle):
                            session_event_complete=False
                        if start<=event_at<=end:
                            events_in_window+=1
                            if event['kind']=='SCHEDULER' or record['decision'] in ('PAUSED','VETO'):
                                event_incidents+=1
                        previous_event_at=event_at;previous_event_cycle=record['cycle']
                    if event_counts!=counts:session_event_complete=False
                elif lifecycle_in_window:session_event_complete=False
                quote_digests=dict(db.execute('SELECT cycle,digest FROM forward_quotes WHERE session=?',(sid,)))
                quote_times={point['cycle']:point['evaluated_at'] for point in points}
                outcomes={}
                for event in event_points:
                    if event['kind']=='OUTCOME':outcomes.setdefault(event['cycle'],[]).append(event['record'])
                for cycle,quote_digest in quote_digests.items():
                    matched=outcomes.pop(cycle,[])
                    if len(matched)!=1 or matched[0]['quote_evidence_sha256']!=quote_digest:
                        session_event_complete=False
                        if start<=_number(quote_times[cycle])<=end:outcome_disagreements+=1
                if any(outcomes.values()):
                    session_event_complete=False
                    for records in outcomes.values():
                        outcome_disagreements+=sum(start<=_number(record['at'])<=end for record in records)
            else:
                event_points=[];session_event_complete=False
            session_quote_errors=0
            guard=FeedGuard();previous_exchange=None
            for point in points:
                if point['policy_sha256']!=expected or type(point['quote_eligible']) is not bool:_fail()
                at=_number(point['evaluated_at']);source=point['source_mode']
                if not session_start<=at<=session_end:_fail()
                checked,previous_exchange,exchange_at=_quote_recheck(
                    point,policy,guard,previous_exchange)
                checked_eligible=point['quote_eligible'] is True and checked
                good=checked_eligible and source==PUBLIC
                # Cross-session boundary validation needs retained context just
                # outside the requested window. Public counts and disagreement
                # totals below remain scoped to the window itself.
                if exchange_at is not None and source==PUBLIC:
                    cross_session_points.append((at,sid,point['cycle'],
                        point['quote'],checked_eligible,exchange_at))
                if start<=at<=end:
                    selected+=1;eligible+=int(point['quote_eligible'])
                    if not event_points:legacy_unbound+=1
                    revalidated+=int(checked_eligible)
                    disagreements+=int(point['quote_eligible'] is True and not checked)
                    public+=int(source==PUBLIC);injected+=int(source==INJECTED);unknown+=int(source not in (PUBLIC,INJECTED))
                    if point.get('error_type') is not None:
                        if (type(point['error_type']) is not str or
                                re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',point['error_type']) is None):_fail()
                        session_quote_errors+=1;quote_errors+=1
                if previous is not None:
                    old,was_good,old_cycle=previous;gap=at-old
                    if gap<0:_fail()
                    if good and was_good and 0<gap<=MAX_GAP_SECONDS:
                        left,right=max(start,old),min(end,at)
                        if left<right:
                            interval_candidates.append((left,right,
                                (sid,old_cycle),(sid,point['cycle'])))
                previous=at,good,point['cycle']
            if lifecycle_in_window:
                if session_admission_incidents:session_event_complete=False
                if not session_event_complete:event_trace_complete=False
                session_count+=1
                quote_error_sessions+=int(session_quote_errors>0)
                if state=='RUNNING':running+=1
                elif state=='PAUSED':paused+=1
                elif last['reason']=='MANUAL_STOP':clean_stopped+=1
                else:other_stopped+=1
                if state!='STOPPED' or last['reason']=='ORDER_CLEANUP_FAILED_REVIEW_REQUIRED':flagged+=1
        # Session-local coverage remains unchanged, but retained public quotes
        # also need one chronological cross-session consistency pass. Otherwise
        # rapidly rotating sessions can reset the jump heuristic indefinitely.
        cross_rejected=set();equal_time={}
        session_started={sid:_number(started) for sid,started,*_ in rows}
        ordered=sorted(cross_session_points,
            key=lambda item:(item[0],session_started[item[1]],item[1],item[2]))
        for at,sid,cycle,quote,checked_eligible,exchange_at in ordered:
            if not checked_eligible:continue
            product=quote['product']
            # Compare price meaning, not JSON spelling (for example 100 vs
            # "100.0"); provenance and permission fields already passed the
            # exact local quote recheck above.
            signature=tuple(FeedGuard.number(quote[key])
                for key in ('bid','ask','last'))
            key=(at,product)
            equal_time.setdefault(key,[]).append((sid,cycle,signature))
        for entries in equal_time.values():
            if (len({sid for sid,_,_ in entries})>1 and
                    len({signature for _,_,signature in entries})>1):
                # No supplied observation is authoritative enough to win an
                # equal-time cross-session price conflict by lexical order.
                cross_rejected.update((sid,cycle) for sid,cycle,_ in entries)
        previous_global={};highest_exchange_seen={}
        # The durable marker timeline is authoritative for new-format sessions.
        # A crash tail contributes no coverage, but it still consumes its
        # exchange timestamp before any later session can claim it.
        highest_admitted=None
        for _,sid,cycle,exchange_at in sorted(cross_session_admissions,
                key=lambda item:(item[0],session_started[item[1]],item[1],item[2])):
            if highest_admitted is not None and exchange_at<=highest_admitted:
                cross_rejected.add((sid,cycle))
            else:highest_admitted=exchange_at
        for at,sid,cycle,quote,checked_eligible,exchange_at in ordered:
            product=quote['product']
            # A session boundary cannot reset the freshness marker.  Track all
            # structurally valid fresh supplied ticks, including locally
            # rejected observations, so the same exchange tick cannot be
            # recycled later with different prices.
            prior_exchange=highest_exchange_seen.get(product)
            if prior_exchange is not None and exchange_at<=prior_exchange:
                if checked_eligible:cross_rejected.add((sid,cycle))
            else:highest_exchange_seen[product]=exchange_at
            if not checked_eligible or (sid,cycle) in cross_rejected:continue
            prices=tuple(FeedGuard.number(quote[key])
                for key in ('bid','ask','last'))
            previous=previous_global.get(product)
            if previous is not None:
                old_at,old_sid,old_prices=previous;elapsed=at-old_at
                if (old_sid!=sid and 0<=elapsed<=feed_policy.MAX_GAP_SECONDS and
                        any(abs(current-old)/old*100>feed_policy.MAX_JUMP_PCT
                            for current,old in zip(prices,old_prices))):
                    cross_rejected.add((sid,cycle))
                    continue
            previous_global[product]=(at,sid,prices)
        cross_rejected_in_window={
            (sid,cycle) for at,sid,cycle,_,checked_eligible,_ in
            cross_session_points if checked_eligible and start<=at<=end and
            (sid,cycle) in cross_rejected}
        disagreements+=len(cross_rejected_in_window)
        revalidated-=len(cross_rejected_in_window)
        # Session-local candidates were collected before the retained quotes
        # could receive their cross-session consistency pass. A point rejected
        # at that pass must break both adjacent intervals; otherwise rapidly
        # rotating sessions can retain nominal 100% coverage even though their
        # boundary observations are no longer eligible.
        intervals=[(left,right) for left,right,left_point,right_point in
            interval_candidates if left_point not in cross_rejected and
            right_point not in cross_rejected]
        if seen!=count:_fail()
        binding=hashlib.sha256(forward_evidence.canonical(dict(sessions=rows,
            quote_heads=heads,quote_rows=count,event_heads=event_heads,
            event_rows=event_count,exchange_admission_heads=admission_heads,
            exchange_admission_rows=admission_count)).encode()).hexdigest()
    with localcontext() as context:
        context.prec=50
        merged=_merge(intervals);covered=sum((b-a for a,b in merged),Decimal(0));duration=end-start
        cursor=start;gaps=[]
        for left,right in merged:
            if left>cursor:gaps.append(left-cursor)
            cursor=max(cursor,right)
        if cursor<end:gaps.append(end-cursor)
        fraction=covered/duration
        over_limit_gaps=[gap for gap in gaps if gap>MAX_GAP_SECONDS]
        over_limit_gap_seconds=sum(over_limit_gaps,Decimal(0))
        target=duration>=MINIMUM_SECONDS and fraction>=Decimal('.95') and not flagged and not injected and not unknown and not disagreements and not admission_incidents and selected>0
        rejected=selected-eligible
        trace_incomplete=not event_trace_complete or legacy_unbound or outcome_disagreements
        incidents=bool(rejected or quote_errors or other_stopped or paused or running or disagreements or injected or unknown or over_limit_gaps or trace_incomplete or event_incidents or admission_incidents)
        incident_free_target=target and not incidents and event_trace_complete
        if duration<MINIMUM_SECONDS:reasons.add('REQUESTED_WINDOW_SHORTER_THAN_72_HOURS')
        if fraction<Decimal('.95'):reasons.add('DECLARED_PUBLIC_COVERAGE_BELOW_95_PERCENT')
        if injected:reasons.add('INJECTED_OBSERVATIONS_IN_WINDOW')
        if unknown:reasons.add('UNKNOWN_SOURCE_LABELS_IN_WINDOW')
        if flagged:reasons.add('NONSTOPPED_OR_CLEANUP_REVIEW_SESSIONS')
        if disagreements:reasons.add('RECORDED_ELIGIBILITY_FAILS_RETAINED_QUOTE_RECHECK')
        if rejected:reasons.add('REJECTED_OBSERVATIONS_RETAINED_IN_WINDOW')
        if quote_errors:reasons.add('QUOTE_ERRORS_RETAINED_IN_WINDOW')
        if other_stopped:reasons.add('SESSION_TERMINATION_REQUIRES_REVIEW')
        if over_limit_gaps:reasons.add('COVERAGE_GAP_EXCEEDS_MAXIMUM')
        if not selected:reasons.add('NO_OBSERVATIONS_IN_WINDOW')
        if trace_incomplete:reasons.add('DURABLE_EVENT_TRACE_INCOMPLETE')
        if event_incidents:reasons.add('EVENT_INCIDENTS_RETAINED_IN_WINDOW')
        if legacy_admission_incidents:reasons.add('LEGACY_EXCHANGE_ADMISSIONS_UNBOUND')
        if unmatched_admission_incidents:reasons.add('EXCHANGE_ADMISSION_OUTCOME_MISSING')
        return dict(mode='RETAINED_FORWARD_OPERATIONAL_COVERAGE_REVIEW',status='REVIEW_REQUIRED',read_only=True,
            after=after,until=until,window_seconds=_text(duration),minimum_trial_seconds=MINIMUM_SECONDS,
            maximum_covered_gap_seconds=MAX_GAP_SECONDS,required_coverage_fraction='0.95',
            retained_observations=count,retained_sessions=n,observations_in_window=selected,eligible_observations_in_window=eligible,rejected_observations_in_window=rejected,
            eligibility_count_scope='RECORDED_FLAGS_ONLY_SEE_REVALIDATED_COUNTS',
            retained_quote_recheck_applied=True,revalidated_eligible_observations_in_window=revalidated,
            revalidated_rejected_observations_in_window=selected-revalidated,
            recorded_eligibility_disagreements_in_window=disagreements,
            declared_public_observations=public,injected_observations=injected,unknown_source_observations=unknown,
            sessions_in_window=session_count,nonstopped_or_cleanup_review_sessions=flagged,
            declared_public_covered_seconds=_text(covered),coverage_fraction=format(fraction.quantize(Decimal('.000001')),'f'),
            uncovered_seconds=_text(duration-covered),uncovered_intervals=len(gaps),longest_uncovered_seconds=_text(max(gaps,default=Decimal(0))),
            declared_coverage_target_met=target,
            clean_manual_stop_sessions=clean_stopped,other_stopped_sessions=other_stopped,
            paused_sessions=paused,running_sessions=running,
            sessions_with_quote_errors=quote_error_sessions,quote_error_observations=quote_errors,
            over_limit_coverage_gaps=len(over_limit_gaps),
            over_limit_coverage_gap_seconds=_text(over_limit_gap_seconds),
            operational_incidents_present=incidents,
            retained_event_records=event_count,events_in_window=events_in_window,
            legacy_unbound_observations_in_window=legacy_unbound,
            quote_outcome_disagreements_in_window=outcome_disagreements,
            event_incident_records_in_window=event_incidents,
            retained_exchange_admissions=admission_count,
            exchange_admissions_in_window=admissions_in_window,
            exchange_admission_incidents_in_window=admission_incidents,
            event_trace_complete=event_trace_complete,
            incident_free_coverage_target_met=incident_free_target,
            evidence_snapshot_sha256=binding,
            **{flag:False for flag in FLAGS},review_reasons=sorted(reasons),
            limitations=LIMITATIONS)


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
