"""Pure per-session composition of forward, event, and order evidence.

The inputs are already replayed local records.  This module grants no source,
broker, account, market, or release authority; it only refuses inconsistent
local projections with fixed public reason codes.
"""
from collections import Counter
import hashlib
import json
import math


REASONS=frozenset((
    'DURABLE_EVENT_CHAIN_DISAGREEMENT',
    'DURABLE_EVENT_START_DISAGREEMENT',
    'DURABLE_EVENT_TAIL_DISAGREEMENT',
    'DURABLE_EVENT_COUNTER_DISAGREEMENT',
    'DURABLE_EVENT_TIME_OR_CYCLE_DISAGREEMENT',
    'QUOTE_OUTCOME_BIJECTION_DISAGREEMENT',
    'DURABLE_ORDER_OUTCOME_DISAGREEMENT',
    'INDEPENDENT_CUT_REQUIRES_SESSION_REVIEW'))


def _sha(value):
    payload=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _clock(value):
    return type(value) in (int,float) and math.isfinite(value)


def reconcile(*,session,started_at,updated_at,state,counts,last,quotes,events,
              order_snapshot):
    """Return a bounded public summary and fixed local-consistency reasons."""
    reasons=[]
    try:
        event_points=events['points'];quote_points=quotes['points']
        if (type(event_points) is not list or type(quote_points) is not list or
                type(counts) is not dict or type(last) is not dict or
                type(order_snapshot) is not dict):
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

        independent_cut=(state=='RUNNING' and order_snapshot.get('halted') is True)
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
            independent_cut_observed=independent_cut)
        summary['reconciliation_sha256']=_sha(dict(session=session,state=state,
            quote_head_sha256=quotes.get('head_sha256'),
            event_head_sha256=events.get('head_sha256'),
            order_snapshot_sha256=order_snapshot.get('snapshot_sha256'),
            **{key:summary[key] for key in ('events','lifecycle_events','outcome_events',
                'scheduler_events','quote_outcomes','modeled_order_outcomes',
                'event_trace_complete','quote_outcome_bijection',
                'durable_order_outcomes_reconciled','independent_cut_observed')}))
        summary['integrity_ok']=not reasons
        return summary,sorted(set(reasons))
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        summary=dict(read_only=True,events=0,head_sha256=None,lifecycle_events=0,
            outcome_events=0,scheduler_events=0,quote_outcomes=0,
            modeled_order_outcomes=0,event_trace_complete=False,
            quote_outcome_bijection=False,durable_order_outcomes_reconciled=False,
            independent_cut_observed=False,reconciliation_sha256=_sha(dict(
                session=session,state=state,invalid=True)),integrity_ok=False)
        return summary,['DURABLE_EVENT_CHAIN_DISAGREEMENT']
