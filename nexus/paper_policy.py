"""Fixed, versioned experiment admission; research results confer no authority."""
import hashlib
import json
from types import MappingProxyType

POLICY=MappingProxyType(dict(id='btc_momentum_experiment_v1',version=1,product='BTC-USD',
    interval_seconds=5,ranking_seconds=15,warmup_observations=6,warmup_seconds=25,
    entry_threshold_bps=30,exit_threshold_bps=50,capital_loss_bps=500,
    maximum_entry_usd=50,initial_entry_fraction_bps=100,fee_bps=10,slippage_bps=5,
    receive_age_seconds=2,exchange_age_seconds=10,order_lifetime_seconds=2,consecutive_failure_limit=3,
    strategy_validated=False,live_authority=False,paper_only=True))


def admit(policy_id):
    if policy_id!=POLICY['id']:raise ValueError('POLICY_NOT_ADMITTED_FOR_PAPER_RUNNER')
    result=dict(POLICY)
    result['policy_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result


def catalogue():
    return dict(read_only=True,admitted_experiments=[admit(POLICY['id'])],
                research_promotes_automatically=False,live_orders_enabled=False,
                note='Only this fixed experimental policy is admitted to the opt-in paper runner. Historical rankings, AI commentary and research branch IDs do not grant execution authority.')
