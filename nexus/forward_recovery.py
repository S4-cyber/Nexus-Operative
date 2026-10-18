"""Read-only operator recovery snapshot; no start, cancellation or repair authority."""
import copy


def build(status,recovery,history):
    pending=[{key:order[key] for key in ('id','product','side','status','notional','simulated_filled_notional','simulated_fee_total','tier')}
             for order in recovery['orders'] if order['status'] in ('SIMULATED_ACK_ONLY','SIMULATED_PARTIALLY_FILLED')]
    positions=copy.deepcopy((recovery['execution_account']['account'] or {}).get('positions',{}))
    reviews=[dict(session=s['session'],state=s['effective_state'],reasons=s['review_reasons'])
             for s in history['sessions'] if s['requires_review']]
    provisional=status['enabled'] or status['ownership_held']
    actions=[]
    if recovery.get('journal_present') is False:actions.append('ORDER_HISTORY_MISSING_VERIFY_SEPARATE_PORTFOLIO')
    if provisional:actions.append('STOP_AND_WAIT_FOR_ACTIVE_CYCLE_BEFORE_RECOVERY')
    if pending:actions.append('INSPECT_ORDERS_AND_EXPLICITLY_CANCEL_PENDING_REMAINDERS')
    if reviews:actions.append('RETAIN_AND_REVIEW_FLAGGED_SESSION_EVIDENCE')
    if positions and not status['enabled']:actions.append('REVIEW_HOLDINGS_AND_EXPLICITLY_START_EXIT_MONITORING_IF_APPROPRIATE')
    return dict(read_only=True,mode='FORWARD_OPERATOR_RECOVERY',provisional=provisional,
                order_history_present=recovery.get('journal_present'),
                runner_enabled=status['enabled'],ownership_held=status['ownership_held'],external_owner_checked=False,
                pending_orders=pending,positions=positions,portfolio_reconciled=recovery['portfolio_reconciled'],
                halted=recovery['halted'],session_reviews=reviews,sessions_examined=len(history['sessions']),
                session_review_scope='Most recent requested sessions; older retained sessions remain available separately.',
                suggested_actions=actions,automatic_start=False,automatic_cancel=False,automatic_repair=False,
                cancellation_scope='Cancels an unfilled remainder only; committed fills and holdings are retained.',
                live_orders_enabled=False,
                limitations='Read-only snapshots across separate databases, provisional while this runner owns execution. Other processes are not probed; inspect only one application instance. Review markers are not approval and this snapshot does not reserve admission or grant running permission.')
