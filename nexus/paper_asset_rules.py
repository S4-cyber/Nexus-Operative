"""Three explicit fixed-host paper GETs for reported BTC asset fields only."""
import time
from nexus import alpaca_paper_inspection as transport
from nexus.alpaca_paper_contract import number,text,sha

FLAGS=('source_attested','asset_entitlement_verified','constraint_semantics_verified',
       'metadata_freshness_verified','broker_account_reconciled','spendable_funds_verified',
       'ready_for_routing','broker_connected','ledger_applied','live_orders_enabled',
       'money_movement_enabled','automatic_retry','release_approved')


def _asset(raw):
    if type(raw) is not dict or len(raw)>64:transport._fail('PAPER_ASSET_RULES_INVALID')
    if raw.get('symbol') not in ('BTC/USD','BTCUSD') or raw.get('class')!='crypto':transport._fail('PAPER_ASSET_RULES_SCOPE')
    if raw.get('status') not in ('active','inactive') or any(type(raw.get(k)) is not bool for k in ('tradable','fractionable')):transport._fail('PAPER_ASSET_RULES_INVALID')
    result=dict(id=transport._uuid(raw.get('id')),symbol='BTC/USD',asset_class='crypto',status=raw['status'],tradable=raw['tradable'],fractionable=raw['fractionable'])
    for key in ('min_order_size','min_trade_increment','price_increment'):
        result[key]=text(number(raw.get(key)))
    return result


def inspect_rules(api_key,api_secret,*,expected_account_id,confirm_read_only_paper=False,
                  opener=None,wall_clock=time.time,elapsed_clock=time.monotonic,
                  permission_check=lambda:True,pacing=None,expected_pacing_head=None):
    session=transport._ReadSession(api_key,api_secret,confirm_read_only_paper=confirm_read_only_paper,
        opener=opener,wall_clock=wall_clock,elapsed_clock=elapsed_clock,permission_check=permission_check,
        pacing=pacing,expected_account_id=expected_account_id,expected_pacing_head=expected_pacing_head,operation='BTC_ASSET_RULES')
    account=transport._uuid(expected_account_id)
    with session:
        started=session.evaluated_at();before=transport._account(session.get(transport.URLS[0]))
        if before['id']!=account:transport._fail('PAPER_ASSET_RULES_ACCOUNT_DISAGREEMENT')
        asset=_asset(session.get(transport.ASSET_URL));received=session.evaluated_at()
        after=transport._account(session.get(transport.URLS[0]))
        if before!=after:transport._fail('PAPER_ASSET_RULES_ACCOUNT_CHANGED')
        completed=session.evaluated_at();reasons=[]
        if any(before[k]!=v for k,v in (('currency','USD'),('status','ACTIVE'),('crypto_status','ACTIVE'))):reasons.append('ACCOUNT_STATUS_OR_CURRENCY_REVIEW')
        if any(before[k] is not False for k in ('account_blocked','trading_blocked','trade_suspended_by_user')):reasons.append('ACCOUNT_PERMISSION_FLAGS_REVIEW')
        if asset['status']!='active' or asset['tradable'] is not True:reasons.append('ASSET_NOT_REPORTED_ACTIVE_AND_TRADABLE')
        if asset['fractionable'] is not True:reasons.append('FRACTIONAL_SUPPORT_REVIEW')
        scope=dict(account_id=account,source='INJECTED_UNATTESTED' if session.injected else 'PAPER_HTTPS_READS',
            asset=asset,account_fields_sha256=sha(before),read_timing=dict(started_at=started,asset_received_at=received,completed_at=completed))
        result=dict(mode='READ_ONLY_PAPER_ASSET_RULES',status='REVIEW_REQUIRED',reported_fields_observed=True,
            **scope,input_sha256=sha(scope),review_reasons=reasons,requests_completed=3,
            account_fields_double_checked=True,**{flag:False for flag in FLAGS},
            limitations='Reported paper BTC asset fields from three sequential GETs. Stable account fields are not atomic account/asset state, provider permission scope, fresh metadata or accepted order-size/increment semantics. No fees, funds, routing, order or funding authority; no automatic retry.')
        session.bind_result_projection(result)
    return result
