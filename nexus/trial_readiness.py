"""Explicit local coverage/quote-capacity review, never session admission."""
from datetime import datetime,timezone
import math
import time
from pathlib import Path
from nexus import forward_trial_review as trial,forward_evidence
from nexus.durable_orders import DurableOrderGateway
from nexus.paper_forward import SESSION_LIMIT
from nexus.paper_policy import POLICY

HOURS=(1,24,72,168)

def build(path,*,hours=72,now=None,order_gateway=None):
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
    sessions=review['retained_sessions'] if review else 0
    remaining=max(0,forward_evidence.LIMIT-retained)
    needed=math.ceil(seconds/POLICY['interval_seconds'])+1
    gateway=order_gateway or DurableOrderGateway(Path(path).parent/'order_lifecycle.sqlite3')
    orders=gateway.recovery_report();capacity=orders['capacity']
    account=orders['execution_account']['account'] or {}
    retained_position=bool(account.get('positions'))
    # Mirror the runner's per-cycle admission reserve. This deliberately assumes
    # every cycle could need the larger entry/exit budget; it is a bound, not a
    # strategy forecast or a reason to delete retained history.
    writes_per_cycle=4 if retained_position else 12
    intents_per_cycle=1 if retained_position else 2
    forecast_commands=needed*writes_per_cycle
    forecast_intents=needed*intents_per_cycle
    return dict(mode='READ_ONLY_PAPER_TRIAL_READINESS',read_only=True,
        status='REVIEW_REQUIRED' if present else 'NO_RETAINED_HISTORY',hours=hours,after=after,until=until,
        minimum_trial_hours=72,history_present=present,review=review,
        quote_capacity=dict(retained=retained,limit=forward_evidence.LIMIT,remaining=remaining,
            estimated_additional_observations=needed,policy_interval_seconds=POLICY['interval_seconds'],
            room_for_additional_requested_window=remaining>=needed,
            scope='Quote records only, estimated at policy cadence; no reservation, uptime, order/ID/session/disk capacity or exit guarantee. No pruning or automatic repair.'),
        session_capacity=dict(retained=sessions,limit=SESSION_LIMIT,remaining=max(0,SESSION_LIMIT-sessions),
            additional_sessions_required=1,room_for_additional_session=sessions<SESSION_LIMIT,
            scope='One additional local session record only; no ownership, uptime, cleanup or automatic-start guarantee.'),
        order_capacity=dict(journal_present=orders['journal_present'],retained_position=retained_position,
            ordinary_commands_remaining=capacity['ordinary_commands_remaining'],intents_remaining=capacity['intents_remaining'],
            commands_per_cycle_upper_bound=writes_per_cycle,intents_per_cycle_upper_bound=intents_per_cycle,
            estimated_cycle_observations=needed,estimated_upper_bound_commands=forecast_commands,
            estimated_upper_bound_intents=forecast_intents,
            next_cycle_reserve_clear=(capacity['ordinary_commands_remaining']>=writes_per_cycle and
                capacity['intents_remaining']>=intents_per_cycle and capacity['lifecycle_intake_allowed']),
            room_for_conservative_requested_window=(capacity['ordinary_commands_remaining']>=forecast_commands and
                capacity['intents_remaining']>=forecast_intents and capacity['lifecycle_intake_allowed']),
            scope='Conservative local upper bound using the runner admission reserve on every policy-cadence cycle. It is not an expected trade count, a storage reservation, strategy evidence or permission. Logical archival preserves retained commands and cannot create indefinite capacity.'),
        **{flag:False for flag in trial.FLAGS},
        limitations='Local sampled operational review only. Clear quote, session or order capacity estimates and a declared coverage target never accept a sustained trial, strategy, source, account, broker or release. Stop for stable final evidence. No engine/session starts, vault/provider access, repairs or account writes.')
