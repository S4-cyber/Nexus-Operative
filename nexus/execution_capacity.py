"""Read-only bounded-storage admission diagnostics; never archives or deletes."""
from nexus import order_gateway

CONTROL_RESERVE=1001


def report(command_count,intent_count,halted,command_limit):
    ordinary_limit=max(0,command_limit-CONTROL_RESERVE)
    remaining=max(0,ordinary_limit-command_count)
    intent_remaining=max(0,order_gateway.MAX_INTENTS-intent_count)
    reasons=[]
    if halted:reasons.append('EMERGENCY_HALT_ACTIVE')
    if remaining==0:reasons.append('JOURNAL_NORMAL_INTAKE_EXHAUSTED')
    if intent_remaining==0:reasons.append('INTENT_HISTORY_CAP_REACHED')
    return dict(read_only=True,command_count=command_count,command_limit=command_limit,
                ordinary_command_limit=ordinary_limit,ordinary_commands_remaining=remaining,
                total_commands_remaining=max(0,command_limit-command_count),
                control_commands_reserved=min(CONTROL_RESERVE,max(0,command_limit-command_count)),
                intent_count=intent_count,intent_limit=order_gateway.MAX_INTENTS,intents_remaining=intent_remaining,
                new_intents_allowed=not halted and remaining>0 and intent_remaining>0,
                lifecycle_intake_allowed=not halted and remaining>0,
                reasons=reasons,automatic_archival_available=False,history_deleted=False,
                recommended_action='STOP_NEW_INTAKE_AND_REVIEW_ARCHIVAL' if remaining==0 or intent_remaining==0 else 'MONITOR_CAPACITY',
                note='Diagnostic snapshot, not a reservation. Admission is rechecked transactionally. Duplicate replays do not consume commands; cancellation, expiry and halt can use remaining control slots.')
