"""Retrospective, fee-reserved funding checks for supplied BTC/USD executions.

No broker IO, accounts, strategy signals or ledger writes. Known execution-linked
fees are reserved at execution time even when posting occurs later. This audits
supplied history; future fee knowledge must never enter strategy backtests.
"""
from collections import defaultdict
from decimal import Decimal,localcontext
from datetime import timezone
from nexus import broker_fee_projection as projection
from nexus.alpaca_paper_contract import TERMINAL,number,text,timestamp,sha

MAX_REVIEW_SAMPLES = 20


def _analyze_projection(aggregate,normalized):
    reasons=list(aggregate['reasons']);samples=[];sample_count=0
    opening=projection.balances(normalized['opening_balances']);current=dict(opening);minimum=dict(opening)
    groups=0;checked=0;covered=None;matches=None;terminal=None
    if aggregate['input_consistency_ok']:
        normalized_orders={row['broker_order_id']:row for row in normalized['orders']}
        normalized_executions={row['execution_id']:row for row in normalized['executions']}
        normalized_fees={row['activity_id']:row for row in normalized['fees']}
        terminal=all(row['status'] in TERMINAL for row in normalized_orders.values())
        if not terminal:reasons.append('NONTERMINAL_ORDER_RESERVATIONS_REQUIRE_REVIEW')
        fee_totals=defaultdict(Decimal);by_time=defaultdict(list)
        with localcontext() as context:
            context.prec=80
            for fee in normalized_fees.values():fee_totals[fee['execution_id']]+=number(fee['amount'],zero=True)
            for execution in normalized_executions.values():by_time[timestamp(execution['occurred_at']).astimezone(timezone.utc)].append(execution)
            covered=True
            for at,rows in sorted(by_time.items()):
                debit={'USD':Decimal(0),'BTC':Decimal(0)};credit=dict(debit)
                for execution in rows:
                    quantity=number(execution['quantity']);gross=quantity*number(execution['price'])
                    buy=execution['side']=='BUY';received='BTC' if buy else 'USD';spent='USD' if buy else 'BTC'
                    incoming=quantity if buy else gross;outgoing=gross if buy else quantity
                    reserved=fee_totals[execution['execution_id']]
                    if reserved>incoming:reasons.append('EXECUTION_FEES_EXCEED_RECEIVED_ASSET')
                    # All supplied linked fees are obligations of this execution;
                    # posting timestamps cannot make them available to later trades.
                    net=incoming-reserved
                    debit[spent]+=outgoing
                    if net<0:debit[received]-=net
                    else:credit[received]+=net
                for asset in ('USD','BTC'):
                    if current[asset]<debit[asset]:
                        covered=False;sample_count+=1
                        # Equal-time executions have no attested ordering. Incoming
                        # funds from the same group cannot fund another execution.
                        reason=('AMBIGUOUS_SAME_TIME_FUNDING' if len(rows)>1 and current[asset]-debit[asset]+credit[asset]>=0
                                else 'INSUFFICIENT_'+asset+'_BEFORE_EXECUTION')
                        reasons.append(reason)
                        if len(samples)<MAX_REVIEW_SAMPLES:
                            samples.append(dict(at=at.isoformat(),asset=asset,reason=reason,
                                                available=text(current[asset]),required=text(debit[asset]),
                                                shortfall=text(debit[asset]-current[asset])))
                    current[asset]+=credit[asset]-debit[asset]
                    minimum[asset]=min(minimum[asset],current[asset])
                groups+=1;checked+=len(rows)
            matches={k:text(v) for k,v in current.items()}==aggregate['projected_balances']
            if not matches:reasons.append('CASH_PATH_AGGREGATE_DISAGREEMENT')
    else:reasons.append('AGGREGATE_EVIDENCE_REQUIRES_REVIEW')
    return dict(mode='DISCONNECTED_FEE_RESERVED_CASH_PATH',evaluated=covered is not None,
                declared_path_consistency_ok=not reasons,declared_debits_covered=covered,
                reasons=sorted(set(reasons)),aggregate_input_sha256=aggregate['input_sha256'],
                input_sha256=sha(dict(policy='nexus.fee-reserved-cash-path.v1',aggregate_input_sha256=aggregate['input_sha256'])),
                execution_groups=groups,executions_checked=checked,all_supplied_orders_terminal=terminal,
                fee_reserved_ending_balances={k:text(v) for k,v in current.items()} if covered is not None else None,
                minimum_balance_scope='OPENING_AND_TIMESTAMP_GROUP_ENDS_ONLY',
                timestamp_scope='UTC_MICROSECOND_GROUPS; no submicrosecond execution ordering is inferred.',
                minimum_fee_reserved_balances={k:text(v) for k,v in minimum.items()} if covered is not None else None,
                ending_balances_match_aggregate=matches,review_samples=samples,review_events=sample_count,
                review_sample_limit=MAX_REVIEW_SAMPLES,review_samples_truncated=sample_count>len(samples),
                fee_timing='ALL_SUPPLIED_LINKED_FEES_RESERVED_AT_EXECUTION',
                simultaneous_execution_scope='Require pre-group funds for every debit; do not invent an order among equal timestamps.',
                broker_connected=False,broker_account_reconciled=False,spendable_funds_verified=False,
                ledger_applied=False,ready_for_routing=False,live_orders_enabled=False,
                limitations='Retrospective supplied-history audit only. Execution links, fee completeness and opening funds are unverified. Reserved known fees are not a causal forecast or a broker settled/available balance. Other orders, holds, transfers, deposits, corrections and activity coverage require authenticated reconciliation. Never use later-known fees to choose historical strategy trades.')


def analyze_and_project(orders,executions,fees,*,opening_balances,opening_at,observed_balances,observed_at,fee_coverage_complete=False):
    aggregate,normalized=projection._project(orders,executions,fees,opening_balances=opening_balances,
            opening_at=opening_at,observed_balances=observed_balances,observed_at=observed_at,
            fee_coverage_complete=fee_coverage_complete)
    return dict(**aggregate,cash_path=_analyze_projection(aggregate,normalized))


def analyze(orders,executions,fees,*,opening_balances,opening_at,observed_balances,observed_at,fee_coverage_complete=False):
    return analyze_and_project(orders,executions,fees,opening_balances=opening_balances,
            opening_at=opening_at,observed_balances=observed_balances,observed_at=observed_at,
            fee_coverage_complete=fee_coverage_complete)['cash_path']


def fixture_report():return analyze(**projection.fixture_inputs())


if __name__=='__main__':
    import json
    print(json.dumps(fixture_report(),sort_keys=True,indent=2))
