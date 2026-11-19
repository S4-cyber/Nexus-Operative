"""Read-only bounded-storage admission diagnostics; never archives or deletes."""
from nexus import order_gateway

CONTROL_RESERVE=1001
EXIT_INTENT_RESERVE=1
PUBLIC_KEYS=frozenset(('read_only','command_count','command_limit','ordinary_command_limit',
    'ordinary_commands_remaining','total_commands_remaining','control_commands_reserved',
    'intent_count','intent_limit','intents_remaining','ordinary_intent_limit',
    'ordinary_intents_remaining','exit_intent_reserve_active','exit_intents_remaining','new_intents_allowed',
    'lifecycle_intake_allowed','reasons','automatic_archival_available',
    'explicit_logical_archival_available','history_deleted','recommended_action',
    'retained_commands','retained_limit','note'))


def report(command_count,intent_count,halted,command_limit,retained_commands=None,
           retained_limit=100000,exit_intent_reserve_active=False):
    values=(command_count,intent_count,command_limit,retained_limit)
    if (any(type(value) is not int or value<0 for value in values) or
            type(halted) is not bool or type(exit_intent_reserve_active) is not bool or
            command_limit<CONTROL_RESERVE or
            retained_limit<CONTROL_RESERVE):
        raise ValueError('EXECUTION_CAPACITY_INPUT_INVALID')
    ordinary_limit=max(0,command_limit-CONTROL_RESERVE)
    if retained_commands is None:retained_commands=command_count
    if (type(retained_commands) is not int or retained_commands<command_count or
            command_count>command_limit or retained_commands>retained_limit or
            intent_count>order_gateway.MAX_INTENTS):
        raise ValueError('EXECUTION_CAPACITY_INPUT_INVALID')
    remaining=min(max(0,ordinary_limit-command_count),max(0,retained_limit-CONTROL_RESERVE-retained_commands))
    total_remaining=min(max(0,command_limit-command_count),max(0,retained_limit-retained_commands))
    intent_remaining=max(0,order_gateway.MAX_INTENTS-intent_count)
    ordinary_intent_limit=max(0,order_gateway.MAX_INTENTS-
        (EXIT_INTENT_RESERVE if exit_intent_reserve_active else 0))
    ordinary_intent_remaining=max(0,ordinary_intent_limit-intent_count)
    exit_intent_remaining=max(0,intent_remaining-ordinary_intent_remaining)
    reasons=[]
    if halted:reasons.append('EMERGENCY_HALT_ACTIVE')
    if remaining==0:reasons.append('JOURNAL_NORMAL_INTAKE_EXHAUSTED')
    if intent_remaining==0:reasons.append('ACTIVE_INTENT_CAP_REACHED')
    elif exit_intent_reserve_active and ordinary_intent_remaining==0:
        reasons.append('EXIT_INTENT_RESERVE_PROTECTED')
    if retained_commands>=retained_limit-CONTROL_RESERVE:reasons.append('RETAINED_HISTORY_INTAKE_EXHAUSTED')
    return dict(read_only=True,command_count=command_count,command_limit=command_limit,
                ordinary_command_limit=ordinary_limit,ordinary_commands_remaining=remaining,
                total_commands_remaining=total_remaining,
                control_commands_reserved=min(CONTROL_RESERVE,total_remaining),
                intent_count=intent_count,intent_limit=order_gateway.MAX_INTENTS,intents_remaining=intent_remaining,
                ordinary_intent_limit=ordinary_intent_limit,
                ordinary_intents_remaining=ordinary_intent_remaining,
                exit_intent_reserve_active=exit_intent_reserve_active,
                exit_intents_remaining=exit_intent_remaining,
                new_intents_allowed=not halted and remaining>0 and ordinary_intent_remaining>0,
                lifecycle_intake_allowed=not halted and remaining>0,
                reasons=reasons,automatic_archival_available=False,explicit_logical_archival_available=True,history_deleted=False,
                recommended_action='STOP_NEW_INTAKE_AND_REVIEW_ARCHIVAL' if remaining==0 or ordinary_intent_remaining==0 else 'MONITOR_CAPACITY',
                retained_commands=retained_commands,retained_limit=retained_limit,
                note='Diagnostic snapshot, not a reservation. Admission is rechecked transactionally. A linked paper account protects one existing intent slot for a matching position-reducing exit; it does not increase the total limit. Duplicate replays do not consume commands; cancellation, expiry and halt can use remaining control slots.')
