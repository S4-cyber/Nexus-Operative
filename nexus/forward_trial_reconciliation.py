"""Privacy-safe detached local reconciliation for retained forward trials.

This composes rewritable local evidence only.  It grants no broker, account,
market, sustained-trial, live-trading, funding, or release authority.
"""
import hashlib
import json
import math
import re

from nexus.forward_event_evidence import LIMIT as EVENT_LIMIT
from nexus.forward_evidence import LIMIT as QUOTE_LIMIT
from nexus.forward_reconciliation_review import (RECEIPT_PROFILE,
    reconciliation_sha256)
from nexus.paper_forward import SESSION_LIMIT


PROFILE='nexus.forward-trial-reconciliation-bundle.v1'
MAX_BUNDLE_BYTES=8*1024*1024
EVENT_FIELDS=('events','lifecycle_events','outcome_events','scheduler_events',
    'quote_outcomes','modeled_order_outcomes','modeled_no_fill_outcomes',
    'event_trace_complete','quote_outcome_bijection',
    'durable_order_outcomes_reconciled','durable_fill_attempts_bound',
    'modeled_execution_set_sha256','durable_no_fill_outcomes_reconciled',
    'durable_no_fill_attempts_bound','cut_evidence_stable',
    'cut_binding_verified','independent_cut_observed',
    'latest_modeled_outcome_sha256')
EVENT_EXPORT_FIELDS=('read_only','head_sha256',*EVENT_FIELDS,
    'reconciliation_sha256','integrity_ok')
SNAPSHOT_FIELDS=('snapshot_sha256','halted','journal_stable_during_review',
    'portfolio_stable_during_review','stable_during_review')
RECORD_FIELDS=('session','review_receipt','durable_event_evidence',
    'order_journal_snapshot','local_consistency_ok','reasons',
    'live_orders_enabled','release_approved')
RECEIPT_FIELDS=('profile','read_only','session','session_summary_sha256',
    'quote_head_sha256','quote_observations','event_head_sha256','event_records',
    'event_reconciliation_sha256','cut_state_before','cut_state_after',
    'cut_requested_before','cut_requested_after','cut_binding_source',
    'cut_evidence_stable','independent_cut_binding_verified','cut_record_sha256',
    'initial_order_snapshot_sha256','final_order_snapshot_sha256',
    'journal_stable_during_review','portfolio_stable_during_review',
    'order_snapshot_stable_during_review','halted_before','halted_after',
    'reason_codes_sha256','reason_count','local_consistency_ok',
    'live_orders_enabled','release_approved','receipt_sha256')
FACT_FIELDS=('session_summary','quote_head_sha256','quote_observations',
    'event_head_sha256','event_records','lifecycle_events','outcome_events',
    'scheduler_events','modeled_order_outcomes','modeled_no_fill_outcomes',
    'modeled_execution_set_sha256')
COUNT_FIELDS=('events','lifecycle_events','outcome_events','scheduler_events',
    'quote_outcomes','modeled_order_outcomes','modeled_no_fill_outcomes')
COUNT_LIMITS=dict(events=EVENT_LIMIT,lifecycle_events=EVENT_LIMIT,
    outcome_events=EVENT_LIMIT,scheduler_events=EVENT_LIMIT,
    quote_outcomes=QUOTE_LIMIT,modeled_order_outcomes=EVENT_LIMIT,
    modeled_no_fill_outcomes=EVENT_LIMIT)
EVENT_BOOLEAN_FIELDS=('read_only','event_trace_complete','quote_outcome_bijection',
    'durable_order_outcomes_reconciled','durable_fill_attempts_bound',
    'durable_no_fill_outcomes_reconciled','durable_no_fill_attempts_bound',
    'cut_evidence_stable','cut_binding_verified','independent_cut_observed',
    'integrity_ok')
CUT_STATES=('CLEAR','CANONICAL_REQUESTED','PRESENT_UNVERIFIED')
CUT_SOURCES=('NONE','PROCESS_SIGNAL','CANONICAL_LATCH')


def _fail():raise ValueError('FORWARD_TRIAL_RECONCILIATION_FAILED') from None


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _sha(value):return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _digest(value,optional=False):
    return ((optional and value is None) or type(value) is str and
        re.fullmatch(r'[a-f0-9]{64}',value) is not None)


def _count(value,maximum):
    return type(value) is int and 0<=value<=maximum


def _exact(value,depth=0):
    if depth>12:_fail()
    if value is None or type(value) in (str,bool):return
    if type(value) is int:return
    if type(value) is float:
        if not math.isfinite(value):_fail()
        return
    if type(value) is list:
        if len(value)>SESSION_LIMIT*64:_fail()
        for item in value:_exact(item,depth+1)
        return
    if type(value) is dict:
        if len(value)>256:_fail()
        for key,item in value.items():
            if type(key) is not str:_fail()
            _exact(item,depth+1)
        return
    _fail()


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:_fail()
        result[key]=value
    return result


def loads(raw):
    """Load exact bounded canonical bytes without accepting JSON aliases."""
    if type(raw) is not bytes or not 1<=len(raw)<=MAX_BUNDLE_BYTES:_fail()
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,
            parse_constant=lambda _:_fail())
        _exact(value)
        if type(value) is not dict or _canonical(value).encode()!=raw:_fail()
        return value
    except (UnicodeError,ValueError,TypeError,OverflowError,RecursionError):_fail()


def build(reviews):
    """Detach the allowlisted public subset of clean stopped-session reviews."""
    try:
        if type(reviews) is not list or not 1<=len(reviews)<=SESSION_LIMIT:_fail()
        records=[];seen=set()
        for review in reviews:
            if type(review) is not dict:_fail()
            sid=review.get('session');events=review.get('durable_event_evidence')
            receipt=review.get('review_receipt');snapshot=review.get('order_journal_snapshot')
            if (type(sid) is not str or re.fullmatch(r'[a-f0-9]{16}',sid) is None or
                    sid in seen or review.get('read_only') is not True or
                    review.get('stored_state')!='STOPPED' or
                    review.get('provisional_active_session') is not False or
                    review.get('local_consistency_ok') is not True or
                    review.get('reasons')!=[] or
                    review.get('live_orders_enabled') is not False or
                    review.get('release_approved') is not False or
                    type(events) is not dict or type(receipt) is not dict or
                    type(snapshot) is not dict or
                    type(events.get('modeled_order_outcomes')) is not int or
                    events['modeled_order_outcomes']<1):_fail()
            seen.add(sid)
            event_export={key:events.get(key) for key in EVENT_EXPORT_FIELDS}
            snapshot_export={key:snapshot.get(key) for key in SNAPSHOT_FIELDS}
            record=dict(session=sid,review_receipt=dict(receipt),
                durable_event_evidence=event_export,
                order_journal_snapshot=snapshot_export,
                local_consistency_ok=True,reasons=[],live_orders_enabled=False,
                release_approved=False)
            _publication_record(record);records.append(record)
        records.sort(key=lambda item:item['session'])
        result=dict(profile=PROFILE,read_only=True,sessions=records,
            live_orders_enabled=False,release_approved=False)
        # Apply the same strict checks as a consumer before publication.
        _shape(result)
        return result
    except (KeyError,ValueError,TypeError,OverflowError,RecursionError):_fail()


def _publication_record(record):
    _exact(record)
    if type(record) is not dict or set(record)!=set(RECORD_FIELDS):_fail()
    sid=record['session'];receipt=record['review_receipt']
    events=record['durable_event_evidence'];snapshot=record['order_journal_snapshot']
    if (type(sid) is not str or re.fullmatch(r'[a-f0-9]{16}',sid) is None or
            type(receipt) is not dict or set(receipt)!=set(RECEIPT_FIELDS) or
            type(events) is not dict or set(events)!=set(EVENT_EXPORT_FIELDS) or
            type(snapshot) is not dict or set(snapshot)!=set(SNAPSHOT_FIELDS) or
            any(type(events[field]) is not bool for field in EVENT_BOOLEAN_FIELDS) or
            any(not _count(events[field],COUNT_LIMITS[field])
                for field in COUNT_FIELDS) or
            events['events']!=events['lifecycle_events']+events['outcome_events']+
                events['scheduler_events'] or
            events['quote_outcomes']!=events['outcome_events'] or
            events['modeled_order_outcomes']+events['modeled_no_fill_outcomes']>
                events['outcome_events'] or
            events['modeled_order_outcomes']<1 or
            not _digest(events['head_sha256']) or
            not _digest(events['modeled_execution_set_sha256']) or
            not _digest(events['latest_modeled_outcome_sha256']) or
            not _digest(events['reconciliation_sha256']) or
            receipt['profile']!=RECEIPT_PROFILE or receipt['read_only'] is not True or
            receipt['session']!=sid or receipt['local_consistency_ok'] is not True or
            not _count(receipt['quote_observations'],QUOTE_LIMIT) or
            not _count(receipt['event_records'],EVENT_LIMIT) or
            receipt['quote_observations']!=events['quote_outcomes'] or
            receipt['event_records']!=events['events'] or
            receipt['reason_count']!=0 or type(receipt['reason_count']) is not int or
            receipt['reason_codes_sha256']!=_sha([]) or
            not _digest(receipt['session_summary_sha256']) or
            not _digest(receipt['quote_head_sha256']) or
            not _digest(receipt['event_head_sha256']) or
            not _digest(receipt['event_reconciliation_sha256']) or
            not _digest(receipt['initial_order_snapshot_sha256']) or
            not _digest(receipt['final_order_snapshot_sha256']) or
            not _digest(receipt['reason_codes_sha256']) or
            not _digest(receipt['receipt_sha256']) or
            receipt['live_orders_enabled'] is not False or
            receipt['release_approved'] is not False or
            receipt['receipt_sha256']!=_sha({key:value for key,value in
                receipt.items() if key!='receipt_sha256'}) or
            receipt['journal_stable_during_review'] is not True or
            receipt['portfolio_stable_during_review'] is not True or
            receipt['order_snapshot_stable_during_review'] is not True or
            receipt['initial_order_snapshot_sha256']!=snapshot.get('snapshot_sha256') or
            receipt['final_order_snapshot_sha256']!=snapshot.get('snapshot_sha256') or
            receipt['event_head_sha256']!=events.get('head_sha256') or
            receipt['event_records']!=events.get('events') or
            receipt['event_reconciliation_sha256']!=events.get('reconciliation_sha256') or
            events.get('read_only') is not True or events.get('integrity_ok') is not True or
            events.get('event_trace_complete') is not True or
            events.get('quote_outcome_bijection') is not True or
            events.get('durable_order_outcomes_reconciled') is not True or
            events.get('durable_fill_attempts_bound') is not True or
            events.get('durable_no_fill_outcomes_reconciled') is not True or
            events.get('durable_no_fill_attempts_bound') is not True or
            snapshot.get('journal_stable_during_review') is not True or
            snapshot.get('portfolio_stable_during_review') is not True or
            snapshot.get('stable_during_review') is not True or
            type(snapshot.get('halted')) is not bool or
            not _digest(snapshot.get('snapshot_sha256')) or
            receipt['halted_before'] is not snapshot['halted'] or
            receipt['halted_after'] is not snapshot['halted'] or
            record['local_consistency_ok'] is not True or record['reasons']!=[] or
            record['live_orders_enabled'] is not False or
            record['release_approved'] is not False):_fail()
    before=receipt['cut_state_before'];after=receipt['cut_state_after']
    requested_before=receipt['cut_requested_before']
    requested_after=receipt['cut_requested_after'];source=receipt['cut_binding_source']
    stable=receipt['cut_evidence_stable']
    verified=receipt['independent_cut_binding_verified'];cut_digest=receipt['cut_record_sha256']
    if (before not in CUT_STATES or after not in CUT_STATES or
            type(requested_before) is not bool or type(requested_after) is not bool or
            source not in CUT_SOURCES or type(stable) is not bool or
            type(verified) is not bool or not _digest(cut_digest,optional=True) or
            stable is not True or events['cut_evidence_stable'] is not stable or
            events['cut_binding_verified'] is not verified or
            events['independent_cut_observed'] is not False or
            ((source=='NONE') is not (verified is False)) or
            source=='NONE' and (requested_before or requested_after or
                before!='CLEAR' or after!='CLEAR') or
            source!='NONE' and not (requested_before and requested_after) or
            verified and not snapshot['halted'] or
            source=='PROCESS_SIGNAL' and ('PRESENT_UNVERIFIED' in
                (before,after) or cut_digest is not None) or
            source=='CANONICAL_LATCH' and (before!='CANONICAL_REQUESTED' or
                after!='CANONICAL_REQUESTED' or cut_digest is None) or
            source!='CANONICAL_LATCH' and cut_digest is not None):_fail()
    cut=dict(before_status=before,after_status=after,
        requested_before=requested_before,requested_after=requested_after,
        binding_source=source,stable=stable,binding_verified=verified,
        record_sha256=cut_digest)
    expected=reconciliation_sha256(session=sid,state='STOPPED',
        quote_head_sha256=receipt['quote_head_sha256'],
        event_head_sha256=receipt['event_head_sha256'],
        order_snapshot_sha256=snapshot['snapshot_sha256'],
        final_order_snapshot_sha256=snapshot['snapshot_sha256'],
        cut_evidence=cut,reasons=[],summary=events)
    if (events['reconciliation_sha256']!=expected or
            receipt['event_reconciliation_sha256']!=expected):_fail()


def _shape(bundle):
    _exact(bundle)
    if (type(bundle) is not dict or set(bundle)!=
            {'profile','read_only','sessions','live_orders_enabled','release_approved'} or
            bundle.get('profile')!=PROFILE or bundle.get('read_only') is not True or
            bundle.get('live_orders_enabled') is not False or
            bundle.get('release_approved') is not False or
            type(bundle.get('sessions')) is not list or
            not 1<=len(bundle['sessions'])<=SESSION_LIMIT):_fail()


def validate(bundle,sessions):
    """Return exact modeled counts after complete local receipt validation."""
    try:
        _shape(bundle)
        if (type(sessions) is not dict or not 1<=len(sessions)<=SESSION_LIMIT or
                any(type(key) is not str for key in sessions)):_fail()
        records=bundle['sessions'];ids=[record.get('session')
            if type(record) is dict else None for record in records]
        if ids!=sorted(ids) or len(ids)!=len(set(ids)) or set(ids)!=set(sessions):_fail()
        result={}
        for record in records:
            _publication_record(record)
            sid=record['session'];receipt=record['review_receipt']
            events=record['durable_event_evidence'];snapshot=record['order_journal_snapshot']
            facts=sessions[sid]
            if (record['local_consistency_ok'] is not True or record['reasons']!=[] or
                    record['live_orders_enabled'] is not False or
                    record['release_approved'] is not False or
                    type(receipt) is not dict or set(receipt)!=set(RECEIPT_FIELDS) or
                    type(events) is not dict or set(events)!=set(EVENT_EXPORT_FIELDS) or
                    type(snapshot) is not dict or set(snapshot)!=set(SNAPSHOT_FIELDS) or
                    type(facts) is not dict or set(facts)!=set(FACT_FIELDS)):_fail()
            _exact(facts)
            if (not _digest(facts['quote_head_sha256']) or
                    not _digest(facts['event_head_sha256']) or
                    not _digest(facts['modeled_execution_set_sha256']) or
                    not _count(facts['quote_observations'],QUOTE_LIMIT) or
                    not _count(facts['event_records'],EVENT_LIMIT) or
                    not _count(facts['lifecycle_events'],EVENT_LIMIT) or
                    not _count(facts['outcome_events'],EVENT_LIMIT) or
                    not _count(facts['scheduler_events'],EVENT_LIMIT) or
                    not _count(facts['modeled_order_outcomes'],EVENT_LIMIT) or
                    not _count(facts['modeled_no_fill_outcomes'],EVENT_LIMIT) or
                    facts['modeled_order_outcomes']<1):_fail()
            if (receipt['profile']!=RECEIPT_PROFILE or receipt['read_only'] is not True or
                    receipt['session']!=sid or receipt['local_consistency_ok'] is not True or
                    receipt['reason_count']!=0 or
                    receipt['reason_codes_sha256']!=_sha([]) or
                    receipt['live_orders_enabled'] is not False or
                    receipt['release_approved'] is not False or
                    receipt['receipt_sha256']!=_sha({key:value for key,value in
                        receipt.items() if key!='receipt_sha256'}) or
                    receipt['journal_stable_during_review'] is not True or
                    receipt['portfolio_stable_during_review'] is not True or
                    receipt['order_snapshot_stable_during_review'] is not True or
                    receipt['initial_order_snapshot_sha256']!=_snapshot(snapshot) or
                    receipt['final_order_snapshot_sha256']!=_snapshot(snapshot) or
                    receipt['halted_before'] is not snapshot['halted'] or
                    receipt['halted_after'] is not snapshot['halted'] or
                    snapshot['journal_stable_during_review'] is not True or
                    snapshot['portfolio_stable_during_review'] is not True or
                    snapshot['stable_during_review'] is not True):_fail()
            summary=facts['session_summary']
            if (type(summary) is not dict or summary.get('session')!=sid or
                    summary.get('state')!='STOPPED' or
                    receipt['session_summary_sha256']!=_sha(summary) or
                    receipt['quote_head_sha256']!=facts['quote_head_sha256'] or
                    receipt['quote_observations']!=facts['quote_observations'] or
                    receipt['event_head_sha256']!=facts['event_head_sha256'] or
                    receipt['event_records']!=facts['event_records'] or
                    events['read_only'] is not True or events['integrity_ok'] is not True or
                    events['head_sha256']!=facts['event_head_sha256'] or
                    events['events']!=facts['event_records'] or
                    events['lifecycle_events']!=facts['lifecycle_events'] or
                    events['outcome_events']!=facts['outcome_events'] or
                    events['scheduler_events']!=facts['scheduler_events'] or
                    events['modeled_order_outcomes']!=facts['modeled_order_outcomes'] or
                    events['modeled_no_fill_outcomes']!=
                        facts['modeled_no_fill_outcomes'] or
                    events['modeled_execution_set_sha256']!=
                        facts['modeled_execution_set_sha256'] or
                    events['event_trace_complete'] is not True or
                    events['quote_outcome_bijection'] is not True or
                    events['durable_order_outcomes_reconciled'] is not True or
                    events['durable_fill_attempts_bound'] is not True or
                    not _digest(events['modeled_execution_set_sha256']) or
                    not _digest(events['reconciliation_sha256'])):_fail()
            cut=dict(before_status=receipt['cut_state_before'],
                after_status=receipt['cut_state_after'],
                requested_before=receipt['cut_requested_before'],
                requested_after=receipt['cut_requested_after'],
                binding_source=receipt['cut_binding_source'],
                stable=receipt['cut_evidence_stable'],
                binding_verified=receipt['independent_cut_binding_verified'],
                record_sha256=receipt['cut_record_sha256'])
            expected=reconciliation_sha256(session=sid,state=summary['state'],
                quote_head_sha256=facts['quote_head_sha256'],
                event_head_sha256=facts['event_head_sha256'],
                order_snapshot_sha256=snapshot['snapshot_sha256'],
                final_order_snapshot_sha256=snapshot['snapshot_sha256'],
                cut_evidence=cut,reasons=[],summary=events)
            if (expected!=events['reconciliation_sha256'] or
                    receipt['event_reconciliation_sha256']!=expected):_fail()
            result[sid]=facts['modeled_order_outcomes']
        return result
    except (KeyError,ValueError,TypeError,OverflowError,RecursionError):_fail()


def _snapshot(snapshot):
    value=snapshot.get('snapshot_sha256') if type(snapshot) is dict else None
    if not _digest(value):_fail()
    return value
