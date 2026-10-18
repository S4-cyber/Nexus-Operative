"""Explicit local coverage/quote-capacity review, never session admission."""
from datetime import datetime,timezone
import math
import time
from pathlib import Path
from nexus import forward_trial_review as trial,forward_evidence
from nexus.paper_policy import POLICY

HOURS=(1,24,72,168)

def build(path,*,hours=72,now=None):
    if type(hours) is not int or hours not in HOURS:raise ValueError('INVALID_TRIAL_WINDOW')
    now=time.time() if now is None else now
    if type(now) not in (int,float) or not math.isfinite(now) or not 7*86400<now<253402300799:
        raise ValueError('INVALID_TRIAL_CLOCK')
    end=int(now);seconds=hours*3600
    utc=lambda value:datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    after,until=utc(end-seconds),utc(end)
    present=trial.existing_history(Path(path))
    review=trial.review(path,after=after,until=until) if present else None
    retained=review['retained_observations'] if review else 0
    remaining=max(0,forward_evidence.LIMIT-retained)
    needed=math.ceil(seconds/POLICY['interval_seconds'])+1
    return dict(mode='READ_ONLY_PAPER_TRIAL_READINESS',read_only=True,
        status='REVIEW_REQUIRED' if present else 'NO_RETAINED_HISTORY',hours=hours,after=after,until=until,
        minimum_trial_hours=72,history_present=present,review=review,
        quote_capacity=dict(retained=retained,limit=forward_evidence.LIMIT,remaining=remaining,
            estimated_additional_observations=needed,policy_interval_seconds=POLICY['interval_seconds'],
            room_for_additional_requested_window=remaining>=needed,
            scope='Quote records only, estimated at policy cadence; no reservation, uptime, order/ID/session/disk capacity or exit guarantee. No pruning or automatic repair.'),
        **{flag:False for flag in trial.FLAGS},
        limitations='Local sampled operational review only. A clear capacity estimate or declared coverage target never accepts a sustained trial, strategy, source, account, broker or release. Stop for stable final evidence. No engine/session starts, vault/provider access, repairs or account writes.')
