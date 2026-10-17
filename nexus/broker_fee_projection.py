"""Pure, bounded projection of explicitly linked execution/fee fixtures.

No broker transport, stored account or synthetic-ledger writes. This is not a raw
Alpaca activity adapter: execution-to-fee links and coverage are caller supplied.
"""
from collections import defaultdict
from decimal import Decimal,localcontext
import re
from uuid import UUID
from nexus.alpaca_paper_contract import number,text,timestamp,sha,STATUSES

MAX_ORDERS=1000
MAX_EXECUTIONS=2000
MAX_FEES=4000


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,128}',value):raise ValueError('INVALID_BROKER_PROJECTION_ID')
    return value


def broker_id(value):
    try:return str(UUID(value))
    except (ValueError,TypeError,AttributeError):raise ValueError('INVALID_BROKER_PROJECTION_ORDER_ID')


def unique(rows,limit,key,normalize):
    if not isinstance(rows,list) or len(rows)>limit:raise ValueError('BROKER_PROJECTION_INPUT_CAP')
    result={};duplicates=0
    for raw in rows:
        if not isinstance(raw,dict):raise ValueError('INVALID_BROKER_PROJECTION_RECORD')
        try:row=normalize(raw);identity=row[key]
        except (KeyError,TypeError,AttributeError):raise ValueError('INVALID_BROKER_PROJECTION_RECORD')
        if identity in result:
            if result[identity]!=row:raise ValueError('BROKER_PROJECTION_DUPLICATE_CONFLICT')
            duplicates+=1
        else:result[identity]=row
    return result,duplicates


def order_record(raw):
    if raw.get('symbol')!='BTC/USD' or raw.get('side') not in ('buy','sell') or raw.get('status') not in STATUSES:
        raise ValueError('BROKER_PROJECTION_ORDER_SCOPE')
    qty=number(raw['filled_quantity'],zero=True)
    average=number(raw['average_fill_price']) if qty else None
    if not qty and raw.get('average_fill_price') is not None and number(raw['average_fill_price'],zero=True)!=0:raise ValueError('BROKER_AVERAGE_WITHOUT_FILL')
    if raw['status'] in ('accepted','pending_new','new','rejected') and qty:raise ValueError('BROKER_STATUS_QUANTITY_DISAGREEMENT')
    if raw['status'] in ('filled','partially_filled') and not qty:raise ValueError('BROKER_STATUS_QUANTITY_DISAGREEMENT')
    return dict(broker_order_id=broker_id(raw['broker_order_id']),client_order_id=identifier(raw['client_order_id']),
                side=raw['side'],status=raw['status'],filled_quantity=text(qty),average_fill_price=text(average) if average is not None else None,
                updated_at=timestamp(raw['updated_at']).isoformat())


def execution_record(raw):
    if raw.get('product')!='BTC-USD' or raw.get('side') not in ('BUY','SELL'):raise ValueError('BROKER_PROJECTION_EXECUTION_SCOPE')
    return dict(execution_id=identifier(raw['execution_id']),broker_order_id=broker_id(raw['broker_order_id']),
                client_order_id=identifier(raw['client_order_id']),side=raw['side'],quantity=text(number(raw['quantity'])),
                price=text(number(raw['price'])),occurred_at=timestamp(raw['occurred_at']).isoformat())


def fee_record(raw):
    if raw.get('asset') not in ('BTC','USD'):raise ValueError('BROKER_PROJECTION_FEE_ASSET_UNSUPPORTED')
    return dict(activity_id=identifier(raw['activity_id']),execution_id=identifier(raw['execution_id']),
                asset=raw['asset'],amount=text(number(raw['amount'],zero=True)),occurred_at=timestamp(raw['occurred_at']).isoformat())


def balances(raw):
    if not isinstance(raw,dict) or set(raw)!={'USD','BTC'}:raise ValueError('BROKER_PROJECTION_BALANCE_SCOPE')
    return {asset:number(raw[asset],zero=True) for asset in ('USD','BTC')}


def _project(orders,executions,fees,*,opening_balances,opening_at,observed_balances,observed_at,fee_coverage_complete=False):
    if type(fee_coverage_complete) is not bool:raise ValueError('INVALID_BROKER_FEE_COVERAGE_DECLARATION')
    opening=balances(opening_balances);observed=balances(observed_balances)
    start=timestamp(opening_at);end=timestamp(observed_at)
    if end<start:raise ValueError('BROKER_PROJECTION_INTERVAL_REGRESSION')
    orders,order_duplicates=unique(orders,MAX_ORDERS,'broker_order_id',order_record)
    executions,execution_duplicates=unique(executions,MAX_EXECUTIONS,'execution_id',execution_record)
    fees,fee_duplicates=unique(fees,MAX_FEES,'activity_id',fee_record)
    reasons=[];totals=defaultdict(Decimal);quantities=defaultdict(Decimal);notionals=defaultdict(Decimal);fee_links=set()
    projected=dict(opening);admitted_executions=set()
    if len({o['client_order_id'] for o in orders.values()})!=len(orders):reasons.append('CLIENT_ORDER_ID_MULTIPLE_BROKER_IDS')
    if not fee_coverage_complete:reasons.append('FEE_ACTIVITY_COVERAGE_UNCONFIRMED')
    with localcontext() as ctx:
        ctx.prec=80
        for execution in executions.values():
            oid=execution['broker_order_id'];order=orders.get(oid);at=timestamp(execution['occurred_at'])
            if order is None or order['client_order_id']!=execution['client_order_id'] or order['side'].upper()!=execution['side']:
                reasons.append('EXECUTION_ORDER_LINK_DISAGREEMENT');continue
            if not start<at<=end or timestamp(order['updated_at'])<at:
                reasons.append('EXECUTION_TIME_OUTSIDE_RECONCILIATION_WINDOW');continue
            qty=number(execution['quantity']);gross=qty*number(execution['price']);sign=1 if execution['side']=='BUY' else -1
            admitted_executions.add(execution['execution_id'])
            quantities[oid]+=qty;notionals[oid]+=gross
            projected['BTC']+=sign*qty;projected['USD']-=sign*gross
            totals['gross_'+execution['side'].lower()+'_notional']+=gross
        for oid,order in orders.items():
            if not start<=timestamp(order['updated_at'])<=end:reasons.append('ORDER_SNAPSHOT_OUTSIDE_RECONCILIATION_WINDOW')
            qty=number(order['filled_quantity'],zero=True)
            if quantities[oid]!=qty:reasons.append('ORDER_EXECUTION_QUANTITY_DISAGREEMENT')
            if qty and notionals[oid]!=qty*number(order['average_fill_price']):reasons.append('ORDER_EXECUTION_AVERAGE_DISAGREEMENT')
        for fee in fees.values():
            execution=executions.get(fee['execution_id']);at=timestamp(fee['occurred_at'])
            if execution is None:
                reasons.append('FEE_EXECUTION_LINK_UNKNOWN');continue
            if fee['execution_id'] not in admitted_executions:
                reasons.append('FEE_EXECUTION_NOT_ADMITTED');continue
            if fee['asset']!=('BTC' if execution['side']=='BUY' else 'USD'):
                reasons.append('FEE_RECEIVED_ASSET_DISAGREEMENT');continue
            if not timestamp(execution['occurred_at'])<=at<=end:
                reasons.append('FEE_TIME_OUTSIDE_RECONCILIATION_WINDOW');continue
            fee_links.add(fee['execution_id'])
            amount=number(fee['amount'],zero=True);projected[fee['asset']]-=amount;totals['fee_'+fee['asset']]+=amount
        if set(executions)-fee_links:reasons.append('EXECUTION_FEE_ACTIVITY_MISSING')
        if any(v<0 for v in projected.values()):reasons.append('NEGATIVE_PROJECTED_BALANCE')
        matches=projected==observed
        if not matches:reasons.append('DECLARED_BALANCE_DISAGREEMENT')
    normalized=dict(orders=sorted(orders.values(),key=lambda r:r['broker_order_id']),
                    executions=sorted(executions.values(),key=lambda r:r['execution_id']),
                    fees=sorted(fees.values(),key=lambda r:r['activity_id']),
                    opening_balances={k:text(v) for k,v in opening.items()},observed_balances={k:text(v) for k,v in observed.items()},
                    opening_at=start.isoformat(),observed_at=end.isoformat(),fee_coverage_complete=fee_coverage_complete)
    report=dict(mode='DISCONNECTED_BROKER_FEE_PROJECTION',input_consistency_ok=not reasons,reasons=sorted(set(reasons)),
                projected_balances={k:text(v) for k,v in projected.items()},declared_balances={k:text(v) for k,v in observed.items()},
                projection_matches_declared_balances=matches,input_sha256=sha(normalized),
                orders=len(orders),executions=len(executions),fee_activities=len(fees),
                duplicate_records_ignored=dict(orders=order_duplicates,executions=execution_duplicates,fees=fee_duplicates),
                gross_buy_notional=text(totals['gross_buy_notional']),gross_sell_notional=text(totals['gross_sell_notional']),
                fees_by_asset={asset:text(totals['fee_'+asset]) for asset in ('BTC','USD')},
                projection_scope='AGGREGATE_INTERVAL_BALANCE_CONSERVATION_ONLY',
                coverage_scope='CALLER_DECLARED_AND_EXECUTION_LINKED_NOT_INDEPENDENTLY_ATTESTED',
                broker_account_reconciled=False,broker_connected=False,ledger_applied=False,ready_for_routing=False,live_orders_enabled=False,
                limitations='Pure supplied-data consistency only. Explicit execution prices are required; cumulative averages never create executions. Fee links, opening balances and completeness are unverified inputs. Intraperiod spending capacity is not proved. Missing corrections, transfers, deposits or fees can invalidate the projection. No existing paper account is modified.')

    return report,normalized


def reconcile(orders,executions,fees,*,opening_balances,opening_at,observed_balances,observed_at,fee_coverage_complete=False):
    return _project(orders,executions,fees,opening_balances=opening_balances,opening_at=opening_at,
                    observed_balances=observed_balances,observed_at=observed_at,
                    fee_coverage_complete=fee_coverage_complete)[0]


def fixture_inputs():
    from nexus.alpaca_paper_contract import normalize
    orders=[];executions=[];fees=[]
    for index,side,quantity,price in ((1,'BUY','1','100'),(2,'SELL','0.999','110')):
        oid=f'01234567-89ab-cdef-0123-456789abcde{index}';cid=f'fixture_{index}';stamp=f'2026-10-17T18:00:0{index}+00:00'
        payload=dict(client_order_id=cid,symbol='BTC/USD',side=side.lower(),type='limit',time_in_force='ioc',qty=quantity,limit_price=price)
        raw=dict(id=oid,**payload,asset_class='crypto',status='filled',filled_qty=quantity,filled_avg_price=price,updated_at=stamp)
        orders.append(normalize(raw,payload))
        executions.append(dict(execution_id=f'execution_{index}',broker_order_id=oid,client_order_id=cid,
                               product='BTC-USD',side=side,quantity=quantity,price=price,occurred_at=stamp))
        fees.append(dict(activity_id=f'fee_{index}',execution_id=f'execution_{index}',asset='BTC' if side=='BUY' else 'USD',
                         amount='0.001' if side=='BUY' else '0.10989',occurred_at=stamp))
    return dict(orders=orders,executions=executions,fees=fees,opening_balances={'USD':'1000','BTC':'0'},opening_at='2026-10-17T18:00:00+00:00',
                     observed_balances={'USD':'1009.78011','BTC':'0'},observed_at='2026-10-17T18:00:03+00:00',fee_coverage_complete=True)


def fixture_report():
    from nexus.broker_cash_path import analyze_and_project
    return analyze_and_project(**fixture_inputs())


if __name__=='__main__':
    import json
    print(json.dumps(fixture_report(),sort_keys=True,indent=2))
