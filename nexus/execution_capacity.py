"""Read-only bounded-storage admission diagnostics; never archives or deletes."""
from decimal import Decimal, InvalidOperation
from nexus import order_gateway

CONTROL_RESERVE=1001
EXIT_INTENT_RESERVE=1


def resumable_exit_mandate(orders, positions, product='BTC-USD'):
    """Return the one exact full-position reducing mandate, if resumable."""
    if type(orders) is not list or type(positions) is not dict or type(product) is not str:
        raise ValueError('EXIT_MANDATE_STATE_INVALID')
    candidates=[order for order in orders if type(order) is dict and
        order.get('intent_scope')=='POSITION_REDUCING_EXIT' and
        order.get('status') in ('SIMULATED_ACK_ONLY','SIMULATED_REDUCTION_PENDING')]
    if not candidates:return None
    if len(candidates)!=1:raise ValueError('EXIT_MANDATE_STATE_INVALID')
    order=candidates[0];position=positions.get(product)
    if (type(position) is not dict or set(position)!={'quantity','entry'} or
            type(position.get('quantity')) is not str or
            order.get('product')!=product or order.get('side')!='SELL' or
            order.get('provider')!='nexus_local_paper' or
            order.get('tier') not in ('terrestrial','solar','universal') or
            type(order.get('id')) is not str or
            type(order.get('expected_position_quantity')) is not str or
            type(order.get('reduction_quantity')) is not str or
            type(order.get('notional')) is not str or
            ('reduction_filled_quantity' in order and
                type(order['reduction_filled_quantity']) is not str) or
            order.get('sent_to_broker') is not False or
            order.get('live_orders_enabled') is not False):
        raise ValueError('EXIT_MANDATE_STATE_INVALID')
    try:
        expected=Decimal(order['expected_position_quantity'])
        reduction=Decimal(order['reduction_quantity'])
        current=Decimal(position['quantity'])
        filled=Decimal(order.get('reduction_filled_quantity','0'))
        slice_limit=Decimal(order['notional'])
        slices=order.get('reduction_slice_count',0)
    except (KeyError,InvalidOperation,TypeError,ValueError):
        raise ValueError('EXIT_MANDATE_STATE_INVALID') from None
    if (not all(value.is_finite() for value in (expected,reduction,current,filled,slice_limit)) or
            expected<=0 or reduction!=expected or current<=0 or filled<0 or
            not Decimal(0)<slice_limit<=Decimal(200) or
            current+filled!=expected or type(slices) is not int or slices<0 or
            (order['status']=='SIMULATED_ACK_ONLY' and (filled or slices)) or
            (order['status']=='SIMULATED_REDUCTION_PENDING' and (not filled or slices<1))):
        raise ValueError('EXIT_MANDATE_STATE_INVALID')
    return order
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
