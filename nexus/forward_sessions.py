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
    return policy,counts,event(last_raw)
