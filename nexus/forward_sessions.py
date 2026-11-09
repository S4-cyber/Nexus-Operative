"""Validation for diagnostic session records; never repairs retained evidence."""
import json
import math


def event(raw):
    value=json.loads(raw)
    if not isinstance(value,dict) or not isinstance(value.get('decision'),str) or not isinstance(value.get('reason'),str):
        raise ValueError('INVALID_FORWARD_SESSION_METADATA')
    stamp=value.get('at')
    if type(stamp) not in (int,float) or not math.isfinite(stamp):
        raise ValueError('INVALID_FORWARD_SESSION_METADATA')
    return value


def parse(started,updated,state,policy_raw,counts_raw,last_raw):
    policy=json.loads(policy_raw);counts=json.loads(counts_raw)
    times=(started,updated)
    valid_times=all(type(v) in (int,float) and math.isfinite(v) for v in times)
    if not valid_times or updated<started or state not in ('RUNNING','PAUSED','STOPPED'):
        raise ValueError('INVALID_FORWARD_SESSION_METADATA')
    if not isinstance(counts,dict) or any(not isinstance(k,str) or type(v) is not int or v<0 for k,v in counts.items()):
        raise ValueError('INVALID_FORWARD_SESSION_METADATA')
    if not isinstance(policy,dict) or not isinstance(policy.get('policy_sha256'),str):
        raise ValueError('INVALID_FORWARD_SESSION_METADATA')
    last=event(last_raw)
    decision=last['decision']
    if (last['at']!=updated or counts.get(decision,0)<1 or
            (state=='STOPPED' and decision!='STOP') or
            (state=='PAUSED' and decision!='PAUSED') or
            (state=='RUNNING' and decision in ('STOP','PAUSED'))):
        raise ValueError('INVALID_FORWARD_SESSION_METADATA')
    return policy,counts,last


def binding_reasons(last,state,session,policy,quote_head,observations,last_quote_cycle,parent_quote_head=None):
    """Return fixed local-consistency reasons; never repairs retained metadata."""
    reasons=[]
    if last.get('session')!=session:
        reasons.append('TERMINAL_EVENT_SESSION_DISAGREEMENT')
    if (last.get('policy_id')!=policy.get('id') or
            last.get('policy_sha256')!=policy.get('policy_sha256') or
            last.get('product')!=policy.get('product')):
        reasons.append('TERMINAL_EVENT_POLICY_DISAGREEMENT')
    if last.get('simulation_only') is not True:
        reasons.append('TERMINAL_EVENT_SCOPE_DISAGREEMENT')
    if last.get('clock_sample_valid') is not True:
        reasons.append('TERMINAL_EVENT_CLOCK_UNTRUSTED')
    cycle=last.get('cycle')
    if type(cycle) is not int or cycle<0:
        reasons.append('TERMINAL_EVENT_CYCLE_DISAGREEMENT')
    elif observations:
        if (state=='STOPPED' and cycle!=last_quote_cycle) or (state=='RUNNING' and cycle not in (last_quote_cycle,last_quote_cycle-1)) or (state=='PAUSED' and cycle<last_quote_cycle):
            reasons.append('TERMINAL_EVENT_CYCLE_DISAGREEMENT')
    elif state!='PAUSED' and cycle!=0:
        reasons.append('TERMINAL_EVENT_CYCLE_DISAGREEMENT')
    observed=last.get('quote_evidence_sha256')
    if observations:
        if state=='PAUSED':valid=observed in (None,quote_head)
        elif state=='RUNNING':
            expected=quote_head if cycle==last_quote_cycle else parent_quote_head if cycle==last_quote_cycle-1 else None
            valid=observed==expected
        else:valid=observed==quote_head
        if not valid:
            reasons.append('TERMINAL_EVENT_QUOTE_HEAD_DISAGREEMENT')
    elif observed is not None:
        reasons.append('TERMINAL_EVENT_QUOTE_HEAD_DISAGREEMENT')
    return sorted(set(reasons))
