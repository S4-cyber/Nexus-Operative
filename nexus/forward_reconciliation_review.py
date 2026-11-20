"""Pure per-session composition of forward, event, and order evidence.

The inputs are already replayed local records.  This module grants no source,
broker, account, market, or release authority; it only refuses inconsistent
local projections with fixed public reason codes.
"""
from collections import Counter
import hashlib
import json
import math
import re


REASONS=frozenset((
    'DURABLE_EVENT_CHAIN_DISAGREEMENT',
    'DURABLE_EVENT_START_DISAGREEMENT',
    'DURABLE_EVENT_TAIL_DISAGREEMENT',
    'DURABLE_EVENT_COUNTER_DISAGREEMENT',
    'DURABLE_EVENT_TIME_OR_CYCLE_DISAGREEMENT',
    'QUOTE_OUTCOME_BIJECTION_DISAGREEMENT',
    'DURABLE_ORDER_OUTCOME_DISAGREEMENT',
    'DURABLE_HALT_WITHOUT_INDEPENDENT_CUT_EVIDENCE',
    'INDEPENDENT_CUT_WITHOUT_DURABLE_HALT',
    'INDEPENDENT_CUT_EVIDENCE_UNSTABLE',
    'INDEPENDENT_CUT_REQUIRES_SESSION_REVIEW'))
RECEIPT_PROFILE='nexus.forward-session-review-receipt.v1'


def _sha(value):
    payload=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _clock(value):
    return type(value) in (int,float) and math.isfinite(value)


def review_receipt(*,session,started_at,updated_at,state,policy,counts,last,
                   quotes,events,initial_order_snapshot,final_order_snapshot,
                   cut_evidence,journal_stable,portfolio_stable,
                   order_snapshot_stable,reasons):
    """Seal the complete cross-database review result without private rows."""
    if (type(session) is not str or re.fullmatch(r'[a-f0-9]{16}',session) is None or
            not _clock(started_at) or not _clock(updated_at) or updated_at<started_at or
            state not in ('RUNNING','PAUSED','STOPPED') or type(policy) is not dict or
            type(counts) is not dict or type(last) is not dict or type(quotes) is not dict or
            type(events) is not dict or type(initial_order_snapshot) is not dict or
            type(final_order_snapshot) is not dict or type(cut_evidence) is not dict or
            any(type(value) is not bool for value in
                (journal_stable,portfolio_stable,order_snapshot_stable)) or
            type(reasons) is not list or reasons!=sorted(set(reasons)) or len(reasons)>64 or
            any(type(reason) is not str or re.fullmatch(r'[A-Z0-9_]{1,128}',reason) is None
                for reason in reasons)):
        raise ValueError('FORWARD_REVIEW_RECEIPT_INPUT_INVALID')
    def digest(value,optional=False):
        if optional and value is None:return None
        if type(value) is not str or re.fullmatch(r'[a-f0-9]{64}',value) is None:
            raise ValueError('FORWARD_REVIEW_RECEIPT_INPUT_INVALID')
        return value
    initial=digest(initial_order_snapshot.get('snapshot_sha256'))
    final=digest(final_order_snapshot.get('snapshot_sha256'))
    reason_codes_sha256=_sha(reasons)
    session_summary_sha256=_sha(dict(session=session,started_at=started_at,
        updated_at=updated_at,state=state,policy=policy,counts=counts,last=last))
    receipt=dict(profile=RECEIPT_PROFILE,read_only=True,session=session,
        session_summary_sha256=session_summary_sha256,
        quote_head_sha256=digest(quotes.get('head_sha256'),optional=True),
        quote_observations=quotes.get('observations'),
        event_head_sha256=digest(events.get('head_sha256'),optional=True),
        event_records=events.get('events'),
        event_reconciliation_sha256=digest(events.get('reconciliation_sha256')),
        cut_state_before=cut_evidence.get('before_status'),
        cut_state_after=cut_evidence.get('after_status'),
        cut_requested_before=cut_evidence.get('requested_before'),
        cut_requested_after=cut_evidence.get('requested_after'),
        cut_binding_source=cut_evidence.get('binding_source'),
        cut_evidence_stable=cut_evidence.get('stable'),
        independent_cut_binding_verified=cut_evidence.get('binding_verified'),
        cut_record_sha256=digest(cut_evidence.get('record_sha256'),optional=True),
        initial_order_snapshot_sha256=initial,final_order_snapshot_sha256=final,
        journal_stable_during_review=journal_stable,
        portfolio_stable_during_review=portfolio_stable,
        order_snapshot_stable_during_review=order_snapshot_stable,
        halted_before=initial_order_snapshot.get('halted'),
        halted_after=final_order_snapshot.get('halted'),
        reason_codes_sha256=reason_codes_sha256,reason_count=len(reasons),
        local_consistency_ok=not reasons,live_orders_enabled=False,release_approved=False)
    if (type(receipt['quote_observations']) is not int or receipt['quote_observations']<0 or
            type(receipt['event_records']) is not int or receipt['event_records']<0 or
            receipt['cut_state_before'] not in ('CLEAR','CANONICAL_REQUESTED','PRESENT_UNVERIFIED') or
            receipt['cut_state_after'] not in ('CLEAR','CANONICAL_REQUESTED','PRESENT_UNVERIFIED') or
            type(receipt['cut_requested_before']) is not bool or
            type(receipt['cut_requested_after']) is not bool or
            receipt['cut_binding_source'] not in ('NONE','PROCESS_SIGNAL','CANONICAL_LATCH') or
            type(receipt['cut_evidence_stable']) is not bool or
            type(receipt['independent_cut_binding_verified']) is not bool or
            type(receipt['halted_before']) is not bool or
            type(receipt['halted_after']) is not bool):
        raise ValueError('FORWARD_REVIEW_RECEIPT_INPUT_INVALID')
    if ((receipt['cut_binding_source']=='NONE') !=
            (receipt['independent_cut_binding_verified'] is False) or
            receipt['independent_cut_binding_verified'] and not
                receipt['cut_evidence_stable'] or
            receipt['cut_binding_source']!='NONE' and not
                (receipt['cut_requested_before'] and receipt['cut_requested_after']) or
            receipt['cut_binding_source']=='CANONICAL_LATCH' and (
                receipt['cut_state_before']!='CANONICAL_REQUESTED' or
                receipt['cut_state_after']!='CANONICAL_REQUESTED' or
                receipt['cut_record_sha256'] is None) or
            receipt['cut_binding_source']!='CANONICAL_LATCH' and
                receipt['cut_record_sha256'] is not None):
        raise ValueError('FORWARD_REVIEW_RECEIPT_INPUT_INVALID')
    receipt['receipt_sha256']=_sha(receipt)
    return receipt


def reconcile(*,session,started_at,updated_at,state,counts,last,quotes,events,
              order_snapshot,final_order_snapshot,cut_evidence):
    """Return a bounded public summary and fixed local-consistency reasons."""
    reasons=[]
    try:
        event_points=events['points'];quote_points=quotes['points']
        if (type(event_points) is not list or type(quote_points) is not list or
                type(counts) is not dict or type(last) is not dict or
                type(order_snapshot) is not dict or type(final_order_snapshot) is not dict or
                type(cut_evidence) is not dict):
            raise ValueError()
        if events.get('integrity_ok') is not True:
            reasons.append('DURABLE_EVENT_CHAIN_DISAGREEMENT')
        event_counts=Counter();kind_counts=Counter();outcomes={}
        previous_at=None;previous_cycle=-1
        for point in event_points:
            if type(point) is not dict or type(point.get('record')) is not dict:
                raise ValueError()
            record=point['record'];kind=point.get('kind')
            at=record.get('at');cycle=record.get('cycle')
            if (not _clock(at) or type(cycle) is not int or cycle<0 or
                    not started_at<=at<=updated_at or
                    previous_at is not None and at<previous_at or
                    cycle<previous_cycle):
                reasons.append('DURABLE_EVENT_TIME_OR_CYCLE_DISAGREEMENT')
            decision=record.get('decision')
            if type(decision) is not str or kind not in ('LIFECYCLE','OUTCOME','SCHEDULER'):
                raise ValueError()
            event_counts[decision]+=1;kind_counts[kind]+=1
            if kind=='OUTCOME':outcomes.setdefault(cycle,[]).append(record)
            previous_at=at;previous_cycle=cycle
        if (not event_points or event_points[0]['record'].get('decision')!='START' or
                event_points[0]['record'].get('cycle')!=0):
            reasons.append('DURABLE_EVENT_START_DISAGREEMENT')
        if not event_points or event_points[-1]['record']!=last:
            reasons.append('DURABLE_EVENT_TAIL_DISAGREEMENT')
        if dict(event_counts)!=counts:
            reasons.append('DURABLE_EVENT_COUNTER_DISAGREEMENT')

        quote_outcomes=0
        for point in quote_points:
            cycle=point.get('cycle') if type(point) is dict else None
            digest=point.get('_digest') if type(point) is dict else None
            # Normal quote review exposes no digest on the parsed point.  The
            # caller supplies the retained row digest in this private field on
            # a detached review copy; it is removed from the public response.
            matched=outcomes.pop(cycle,[]) if type(cycle) is int else []
            if (len(matched)!=1 or type(digest) is not str or
                    matched[0].get('quote_evidence_sha256')!=digest):
                reasons.append('QUOTE_OUTCOME_BIJECTION_DISAGREEMENT')
            else:quote_outcomes+=1
        if any(outcomes.values()):
            reasons.append('QUOTE_OUTCOME_BIJECTION_DISAGREEMENT')

        durable=[]
        for item in order_snapshot.get('orders',[]):
            if type(item) is not dict or type(item.get('order')) is not dict or type(item.get('quote_events')) is not list:
                raise ValueError()
            order=item['order'];oid=order.get('id');side=order.get('side')
            if type(oid) is not str or side not in ('BUY','SELL'):raise ValueError()
            for execution in item['quote_events']:
                if type(execution) is not dict:raise ValueError()
                cycle=execution.get('evidence_cycle')
                if type(cycle) is not int or cycle<1:raise ValueError()
                durable.append((cycle,side,oid))
        recorded=[]
        for point in event_points:
            record=point['record']
            if point.get('kind')=='OUTCOME' and record.get('decision') in ('BUY','SELL'):
                recorded.append((record.get('cycle'),record.get('decision'),record.get('order_id')))
        order_outcomes_ok=sorted(recorded)==sorted(durable)
        if not order_outcomes_ok:reasons.append('DURABLE_ORDER_OUTCOME_DISAGREEMENT')

        before=cut_evidence.get('before_status');after=cut_evidence.get('after_status')
        cut_stable=cut_evidence.get('stable');cut_binding=cut_evidence.get('binding_verified')
        requested_before=cut_evidence.get('requested_before')
        requested_after=cut_evidence.get('requested_after')
        binding_source=cut_evidence.get('binding_source')
        if (before not in ('CLEAR','CANONICAL_REQUESTED','PRESENT_UNVERIFIED') or
                after not in ('CLEAR','CANONICAL_REQUESTED','PRESENT_UNVERIFIED') or
                type(cut_stable) is not bool or type(cut_binding) is not bool or
                type(requested_before) is not bool or type(requested_after) is not bool or
                binding_source not in ('NONE','PROCESS_SIGNAL','CANONICAL_LATCH')):
            raise ValueError()
        halted=order_snapshot.get('halted') is True
        final_halted=final_order_snapshot.get('halted') is True
        cut_present=requested_before or requested_after
        if cut_present and not cut_stable:
            reasons.append('INDEPENDENT_CUT_EVIDENCE_UNSTABLE')
        if cut_binding and not (halted and final_halted):
            reasons.append('INDEPENDENT_CUT_WITHOUT_DURABLE_HALT')
        if state=='RUNNING' and (halted or final_halted) and not cut_binding:
            reasons.append('DURABLE_HALT_WITHOUT_INDEPENDENT_CUT_EVIDENCE')
        independent_cut=(state=='RUNNING' and halted and final_halted and cut_binding)
        if independent_cut:reasons.append('INDEPENDENT_CUT_REQUIRES_SESSION_REVIEW')
        trace_ok=not any(reason in reasons for reason in (
            'DURABLE_EVENT_CHAIN_DISAGREEMENT','DURABLE_EVENT_START_DISAGREEMENT',
            'DURABLE_EVENT_TAIL_DISAGREEMENT','DURABLE_EVENT_COUNTER_DISAGREEMENT',
            'DURABLE_EVENT_TIME_OR_CYCLE_DISAGREEMENT',
            'QUOTE_OUTCOME_BIJECTION_DISAGREEMENT'))
        summary=dict(read_only=True,events=len(event_points),
            head_sha256=events.get('head_sha256'),
            lifecycle_events=kind_counts['LIFECYCLE'],
            outcome_events=kind_counts['OUTCOME'],
            scheduler_events=kind_counts['SCHEDULER'],
            quote_outcomes=quote_outcomes,
            modeled_order_outcomes=len(recorded),
            event_trace_complete=trace_ok,
            quote_outcome_bijection='QUOTE_OUTCOME_BIJECTION_DISAGREEMENT' not in reasons,
            durable_order_outcomes_reconciled=order_outcomes_ok,
            cut_state_before=before,cut_state_after=after,
            cut_requested_before=requested_before,cut_requested_after=requested_after,
            cut_binding_source=binding_source,
            cut_evidence_stable=cut_stable,cut_binding_verified=cut_binding,
            independent_cut_observed=independent_cut)
        summary['reconciliation_sha256']=_sha(dict(session=session,state=state,
            quote_head_sha256=quotes.get('head_sha256'),
            event_head_sha256=events.get('head_sha256'),
            order_snapshot_sha256=order_snapshot.get('snapshot_sha256'),
            final_order_snapshot_sha256=final_order_snapshot.get('snapshot_sha256'),
            cut_evidence=cut_evidence,reasons=sorted(set(reasons)),
            **{key:summary[key] for key in ('events','lifecycle_events','outcome_events',
                'scheduler_events','quote_outcomes','modeled_order_outcomes',
                'event_trace_complete','quote_outcome_bijection',
                'durable_order_outcomes_reconciled','cut_evidence_stable',
                'cut_binding_verified','independent_cut_observed')}))
        summary['integrity_ok']=not reasons
        return summary,sorted(set(reasons))
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        summary=dict(read_only=True,events=0,head_sha256=None,lifecycle_events=0,
            outcome_events=0,scheduler_events=0,quote_outcomes=0,
            modeled_order_outcomes=0,event_trace_complete=False,
            quote_outcome_bijection=False,durable_order_outcomes_reconciled=False,
            cut_state_before='PRESENT_UNVERIFIED',cut_state_after='PRESENT_UNVERIFIED',
            cut_requested_before=True,cut_requested_after=True,cut_binding_source='NONE',
            cut_evidence_stable=False,cut_binding_verified=False,
            independent_cut_observed=False,reconciliation_sha256=_sha(dict(
                session=session,state=state,invalid=True)),integrity_ok=False)
        return summary,['DURABLE_EVENT_CHAIN_DISAGREEMENT']
