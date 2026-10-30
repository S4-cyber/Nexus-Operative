"""Explicit single-attempt Alpaca paper order transport.

This internal boundary is deliberately not wired to the Portal, saved read-only
credentials, strategies, startup, retries, cancellation or a ledger.  It checks
one prepared BTC/USD IOC-limit request against fixed paper-account endpoints,
commits the durable unknown-outcome marker before POST, and then sends at most
once.  Every exception after that commit leaves original-client-ID recovery
mandatory.
"""
from decimal import Decimal, localcontext
import hashlib
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

from nexus import alpaca_paper_contract as contract
from nexus import alpaca_paper_inspection as inspection
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_provider_post_budget import PaperProviderPostBudget
from nexus.sqlite_journal_guard import check_path_set

ACCOUNT_URL = inspection.ROOT + '/v2/account'
POSITIONS_URL = inspection.ROOT + '/v2/positions'
ASSET_URL = inspection.ASSET_URL
ORDER_URL = contract.PAPER_ORDERS_URL
REQUEST_TIMEOUT = 4
TOTAL_SECONDS = 16
MAX_REQUESTS = 6
MAX_BODY = inspection.MAX_BODY


class PaperSubmissionError(ValueError):
    """Fixed diagnostic only; never include credentials or provider bodies."""


def _fail(code):
    raise PaperSubmissionError('PAPER_SUBMISSION_' + code)


def _hash(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        _fail('REFERENCE_INVALID')
    return value


def _credential(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', value):
        _fail('CREDENTIAL_FORMAT_INVALID')


def _asset(raw, payload):
    if not isinstance(raw, dict):
        _fail('ASSET_INVALID')
    expected = {
        'symbol': 'BTC/USD', 'class': 'crypto', 'status': 'active',
        'tradable': True, 'fractionable': True,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        _fail('ASSET_NOT_ELIGIBLE')
    try:
        minimum = contract.number(raw.get('min_order_size'))
        step = contract.number(raw.get('min_trade_increment'))
        tick = contract.number(raw.get('price_increment'))
        quantity = contract.number(payload['qty'])
        price = contract.number(payload['limit_price'])
    except (ValueError, KeyError):
        _fail('ASSET_INVALID')
    if quantity < minimum or quantity % step or price % tick:
        _fail('ASSET_TERMS_NOT_ELIGIBLE')


def _account_and_funds(account_raw, positions_raw, expected_account_id, payload):
    try:
        account = inspection._account(account_raw)
    except (inspection.PaperInspectionError, TypeError, ValueError):
        _fail('ACCOUNT_INVALID')
    if account['id'] != expected_account_id:
        _fail('ACCOUNT_ID_DISAGREEMENT')
    if any(account[field] != value for field, value in
           (('currency', 'USD'), ('status', 'ACTIVE'), ('crypto_status', 'ACTIVE'))):
        _fail('ACCOUNT_STATUS_REVIEW_REQUIRED')
    if any(account[field] is not False for field in
           ('account_blocked', 'trading_blocked', 'trade_suspended_by_user')):
        _fail('ACCOUNT_PERMISSION_REVIEW_REQUIRED')
    if any(account[field] is None for field in
           ('cash', 'non_marginable_buying_power', 'pending_transfer_in',
            'pending_transfer_out', 'accrued_fees')):
        _fail('ACCOUNT_FUNDS_INCOMPLETE')
    if any(contract.number(account[field], zero=True) != 0 for field in
           ('pending_transfer_in', 'pending_transfer_out', 'accrued_fees')):
        _fail('ACCOUNT_PENDING_FUNDS_OR_FEES_REVIEW_REQUIRED')
    try:
        inspection._collection_rows(positions_raw, expected_account_id)
    except (inspection.PaperInspectionError, TypeError, ValueError):
        _fail('POSITIONS_INVALID')
    quantity = contract.number(payload['qty'])
    price = contract.number(payload['limit_price'])
    if payload['side'] == 'buy':
        with localcontext() as context:
            context.prec = 80
            required = quantity * price
        observed = min(contract.number(account['cash'], zero=True),
                       contract.number(account['non_marginable_buying_power'], zero=True))
        if observed < required:
            _fail('OBSERVED_BUY_FUNDS_INSUFFICIENT')
    else:
        available = Decimal(0); seen = False
        for row in positions_raw:
            if row.get('symbol') not in ('BTC/USD', 'BTCUSD'):
                continue
            if seen or row.get('asset_class') != 'crypto' or row.get('side') != 'long':
                _fail('BTC_POSITION_REVIEW_REQUIRED')
            if row.get('qty_available') is None:
                _fail('BTC_AVAILABILITY_MISSING')
            total = contract.number(row.get('qty'), zero=True)
            available = contract.number(row['qty_available'], zero=True)
            if available > total:
                _fail('BTC_AVAILABILITY_INVALID')
            seen = True
        if available < quantity:
            _fail('OBSERVED_BTC_AVAILABLE_INSUFFICIENT')


def _risk_snapshot(account_raw, positions_raw, expected_account_id):
    """Normalize only fields that can change paper-order admission.

    Exact equality across the two samples is deliberately conservative.  A
    concurrent balance, permission, transfer/fee, or BTC availability change
    means the sequential preflight is no longer a stable basis for the one
    allowed POST.
    """
    try:
        account = inspection._account(account_raw)
        inspection._collection_rows(positions_raw, expected_account_id)
        btc = []
        for row in positions_raw:
            if row.get('symbol') not in ('BTC/USD', 'BTCUSD'):
                continue
            if (row.get('asset_class') != 'crypto' or row.get('side') != 'long' or
                    row.get('qty_available') is None):
                _fail('BTC_POSITION_REVIEW_REQUIRED')
            quantity = contract.number(row.get('qty'), zero=True)
            available = contract.number(row['qty_available'], zero=True)
            if available > quantity:
                _fail('BTC_AVAILABILITY_INVALID')
            btc.append(dict(quantity=contract.text(quantity),
                available=contract.text(available)))
        if len(btc) > 1:
            _fail('BTC_POSITION_REVIEW_REQUIRED')
        return dict(account=account, btc_position=btc[0] if btc else None)
    except PaperSubmissionError:
        raise
    except (inspection.PaperInspectionError, TypeError, ValueError, KeyError):
        _fail('PREFLIGHT_STATE_INVALID')


class _Session:
    def __init__(self, key, secret, *, opener, wall_clock, elapsed_clock,
                 permission_check, emergency_stop_check):
        self.key = key; self.secret = secret
        self.reader = opener or build_opener(ProxyHandler({}), inspection._NoRedirect())
        self.injected = opener is not None
        self.wall_clock = wall_clock; self.elapsed_clock = elapsed_clock
        self.permission_check = permission_check
        self.emergency_stop_check = emergency_stop_check
        self.start = self.previous = None
        self.requests = 0
        self.post_attempted = False

    def checkpoint(self):
        try:
            if self.permission_check() is not True:
                _fail('PERMISSION_REVOKED')
            if self.emergency_stop_check() is not False:
                _fail('EMERGENCY_STOP_ACTIVE')
            current = (self.wall_clock(), self.elapsed_clock())
            if any(type(value) not in (int, float) or not 0 <= value <= 1e12 or
                   not math.isfinite(value) for value in current):
                _fail('CLOCK_INVALID')
            if self.start is None:
                self.start = current
            if self.previous is not None and any(a < b for a, b in zip(current, self.previous)):
                _fail('CLOCK_REGRESSION')
            if abs((current[0]-self.start[0])-(current[1]-self.start[1])) > 2:
                _fail('CLOCK_DISCONTINUITY')
            if current[1]-self.start[1] >= TOTAL_SECONDS:
                _fail('TIME_BUDGET')
            self.previous = current
            return max(.001, min(REQUEST_TIMEOUT, TOTAL_SECONDS-(current[1]-self.start[1])))
        except PaperSubmissionError:
            raise
        except Exception:
            _fail('ADMISSION_CHECK_FAILED')

    def evaluated_at(self):
        self.checkpoint()
        return self.previous[0]

    def request(self, url, *, payload=None):
        if url not in (ACCOUNT_URL, POSITIONS_URL, ASSET_URL, ORDER_URL):
            _fail('PATH_DENIED')
        if self.requests >= MAX_REQUESTS:
            _fail('REQUEST_CAP')
        method = 'POST' if payload is not None else 'GET'
        if method == 'POST' and url != ORDER_URL or method == 'GET' and url == ORDER_URL:
            _fail('METHOD_DENIED')
        data = None if payload is None else contract.canonical(payload).encode()
        headers = {'APCA-API-KEY-ID': self.key, 'APCA-API-SECRET-KEY': self.secret,
                   'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(url, data=data, headers=headers, method=method)
        timeout = self.checkpoint(); self.requests += 1
        if method == 'POST':
            self.post_attempted = True
        try:
            with self.reader.open(request, timeout=timeout) as response:
                self.checkpoint()
                if response.geturl() != url:
                    _fail('RESPONSE_URL_CHANGED')
                allowed = (200,) if method == 'GET' else (200, 201)
                if response.status not in allowed:
                    _fail('HTTP_OUTCOME_UNKNOWN_LOOKUP_REQUIRED')
                if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                    _fail('CONTENT_TYPE_INVALID')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    _fail('ENCODING_DENIED')
                length = response.headers.get('Content-Length')
                if length is not None and (not re.fullmatch(r'[0-9]{1,8}', length) or
                                           int(length) > MAX_BODY):
                    _fail('BODY_CAP')
                body = bytearray()
                while True:
                    self.checkpoint()
                    chunk = response.read(min(65536, MAX_BODY+1-len(body)))
                    if not isinstance(chunk, bytes):
                        _fail('BODY_INVALID')
                    if len(body)+len(chunk) > MAX_BODY:
                        _fail('BODY_CAP')
                    body.extend(chunk)
                    if not chunk:
                        break
                if length is not None and len(body) != int(length):
                    _fail('BODY_LENGTH_CHANGED')
                try:
                    return json.loads(body.decode(), object_pairs_hook=inspection._pairs,
                                      parse_constant=inspection._nonfinite, parse_float=Decimal)
                except (ValueError, UnicodeError, RecursionError):
                    _fail('JSON_INVALID')
        except PaperSubmissionError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError):
            _fail('TRANSPORT_OUTCOME_UNKNOWN_LOOKUP_REQUIRED' if method == 'POST'
                  else 'PREFLIGHT_TRANSPORT_FAILED')
        except Exception:
            _fail('TRANSPORT_OUTCOME_UNKNOWN_LOOKUP_REQUIRED' if method == 'POST'
                  else 'PREFLIGHT_TRANSPORT_FAILED')


def submit_once(journal, client_order_id, api_key, api_secret, *,
                expected_account_id, expected_head, expected_payload_sha256,
                post_budget, expected_post_budget_head,
                confirm_paper_order=False, confirm_single_attempt=False,
                opener=None, wall_clock=time.time, elapsed_clock=time.monotonic,
                permission_check=lambda: False, emergency_stop_check=lambda: True,
                credential_check=lambda: False):
    """Submit one prepared paper IOC limit after durable uncertainty admission."""
    if confirm_paper_order is not True or confirm_single_attempt is not True:
        _fail('EXPLICIT_SINGLE_ATTEMPT_CONFIRMATION_REQUIRED')
    if type(journal) is not BrokerRequestJournal:
        _fail('JOURNAL_REQUIRED')
    if type(post_budget) is not PaperProviderPostBudget:
        _fail('POST_BUDGET_REQUIRED')
    _hash(expected_post_budget_head)
    try:
        check_path_set((journal.path, post_budget.path), 'PAPER_SUBMISSION_')
        post_budget.inspect()
    except (OSError, ValueError, TypeError):
        _fail('POST_BUDGET_INVALID')
    _credential(api_key); _credential(api_secret)
    expected_account_id = inspection._uuid(expected_account_id)
    _hash(expected_head); _hash(expected_payload_sha256)
    selected = journal.lookup_request(client_order_id)
    passive = journal.submission_review(client_order_id, evaluated_at=wall_clock())
    if not passive['local_marker_checks_clear']:
        _fail('LOCAL_REVIEW_BLOCKED')
    if (selected['head_sha256'] != expected_head or
            selected['plan']['payload_sha256'] != expected_payload_sha256):
        _fail('SELECTION_CHANGED')
    if selected['account_sha256'] != hashlib.sha256(expected_account_id.encode()).hexdigest():
        _fail('ACCOUNT_SCOPE_DISAGREEMENT')
    if passive['head_sha256'] != expected_head:
        _fail('LOCAL_REVIEW_BLOCKED')
    payload = selected['plan']['payload']
    session = _Session(api_key, api_secret, opener=opener, wall_clock=wall_clock,
                       elapsed_clock=elapsed_clock, permission_check=permission_check,
                       emergency_stop_check=emergency_stop_check)
    session.checkpoint()
    account = session.request(ACCOUNT_URL)
    try:
        sampled_account = inspection._account(account)
    except (inspection.PaperInspectionError, TypeError, ValueError):
        _fail('ACCOUNT_INVALID')
    if sampled_account['id'] != expected_account_id:
        _fail('ACCOUNT_ID_DISAGREEMENT')
    positions = session.request(POSITIONS_URL)
    asset = session.request(ASSET_URL)
    _account_and_funds(account, positions, expected_account_id, payload)
    _asset(asset, payload)
    initial_risk = _risk_snapshot(account, positions, expected_account_id)
    current_account = session.request(ACCOUNT_URL)
    current_positions = session.request(POSITIONS_URL)
    _account_and_funds(current_account, current_positions, expected_account_id, payload)
    current_risk = _risk_snapshot(current_account, current_positions, expected_account_id)
    if current_risk != initial_risk:
        _fail('PREFLIGHT_STATE_CHANGED')
    if credential_check() is not True:
        _fail('CREDENTIALS_CHANGED')
    with post_budget.ownership() as owner:
        token = post_budget.begin(account_id=expected_account_id,
            request_before_head_sha256=expected_head,
            payload_sha256=expected_payload_sha256, client_order_id=client_order_id,
            admission=session.evaluated_at, ownership=owner,
            expected_head=expected_post_budget_head)
        try:
            marker = journal.begin_submission_checked(client_order_id, expected_head=expected_head,
                expected_payload_sha256=expected_payload_sha256, admission=session.evaluated_at)
            if marker.get('attempt_marker_commit_observed') is not True:
                _fail('ATTEMPT_MARKER_UNCONFIRMED')
            # This checkpoint is intentionally after both durable reservations.
            # A stop or lost permission cannot re-arm the request.
            session.checkpoint()
            if credential_check() is not True:
                _fail('CREDENTIALS_CHANGED_AFTER_MARKER')
            raw = session.request(ORDER_URL, payload=payload)
            session.checkpoint()
            observed = journal.observe(client_order_id, raw)
        except BaseException:
            outcome = 'OUTCOME_UNKNOWN' if session.post_attempted else 'NOT_SENT'
            post_budget.finish(token, outcome=outcome,
                request_after_head_sha256=journal.recovery()['head_sha256'],
                actual_posts=int(session.post_attempted),
                admission=lambda: session.previous[0],
                ownership=owner)
            raise
        post_budget_head = post_budget.finish(token, outcome='RESPONSE_OBSERVED',
            request_after_head_sha256=journal.recovery()['head_sha256'], actual_posts=1,
            admission=session.evaluated_at, ownership=owner)
    return dict(mode='EXPLICIT_SINGLE_ATTEMPT_ALPACA_PAPER_SUBMISSION',
        source='INJECTED_UNATTESTED' if session.injected else 'PAPER_HTTPS_WRITE',
        paper_only=True, single_attempt_consumed=True,
        durable_unknown_outcome_marker_committed=True,
        account_and_position_capacity_resampled=True,
        sequential_preflight_stable=True,
        paper_order_response_observed=True, journal=observed,
        post_budget_head_sha256=post_budget_head,
        local_provider_post_pacing=True, broker_wide_quota_verified=False,
        automatic_retry=False, automatic_continuation=False, sendable=False,
        ready_for_routing=False, broker_account_reconciled=False,
        spendable_funds_verified=False, ledger_applied=False,
        live_orders_enabled=False, money_movement_enabled=False,
        release_approved=False,
        limitations='One explicitly confirmed Alpaca paper POST after fixed-endpoint asset checks and two matching account/BTC-position admission samples. Durable request and shared-local-provider budget reservations precede IO; any lost/ambiguous outcome requires original-client-ID recovery, blocks the POST budget for review and can never be resent automatically. The budget coordinates cooperating callers sharing one private path only; broker-wide quota and distributed ownership are unverified. Matching sequential preflight reads are not atomic, observed amounts are not settled-funds proof, and a response is not execution/fee/hold/account acceptance. No Portal route, saved write credential scope, strategy integration, retry, cancellation, ledger application, live endpoint, funding or release authority.')


def fixture_report():
    """Disposable injected single-attempt proof; no credentials or provider IO."""
    import io
    import tempfile
    from pathlib import Path

    account_id = '01234567-89ab-cdef-0123-456789abcdef'
    key, secret = 'fixture_key_only_123', 'fixture_secret_only_123'
    account = dict(id=account_id,currency='USD',status='ACTIVE',crypto_status='ACTIVE',
        account_blocked=False,trading_blocked=False,trade_suspended_by_user=False,
        cash='1000',non_marginable_buying_power='900',pending_transfer_in='0',
        pending_transfer_out='0',accrued_fees='0')
    positions=[dict(symbol='BTC/USD',asset_class='crypto',side='long',qty='3',qty_available='3')]
    asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,
        fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
    raw=dict(id='01234567-89ab-cdef-0123-456789abcdef',client_order_id='nx_submit_fixture',
        symbol='BTC/USD',side='buy',type='limit',time_in_force='ioc',asset_class='crypto',
        qty='2',limit_price='100',status='new',filled_qty='0',filled_avg_price=None,
        updated_at='2026-10-23T17:00:00Z')

    class Reply:
        status=200;headers={'Content-Type':'application/json'}
        def __init__(self,url,value):self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def geturl(self):return self.url
        def read(self,size):return self.body.read(size)
    class Opener:
        def __init__(self,journal,fail=False,late_account=None,late_positions=None):
            self.journal=journal;self.fail=fail;self.requests=[]
            self.values=[account,positions,asset,
                account if late_account is None else late_account,
                positions if late_positions is None else late_positions,raw]
            self.marker_before_post=False
        def open(self,request,timeout):
            self.requests.append(request)
            if request.get_method()=='POST':
                retained=self.journal.recovery()['requests'][0]
                self.marker_before_post=retained['lookup_by_original_client_id_required']
                if self.fail:raise URLError('fixture transport loss')
            return Reply(request.full_url,self.values[len(self.requests)-1])

    def prepared(root):
        journal=BrokerRequestJournal(root/'requests.sqlite3',account_id)
        plan=contract.preview('nx_submit_fixture','BTC-USD','BUY','2','100',asset,
            metadata_at=1000,evaluated_at=1001)
        journal.prepare(plan);selected=journal.lookup_request('nx_submit_fixture')
        return journal,selected
    def options(selected,opener,budget):
        return dict(expected_account_id=account_id,expected_head=selected['head_sha256'],
            expected_payload_sha256=selected['plan']['payload_sha256'],
            post_budget=budget,expected_post_budget_head=budget.inspect()['head_sha256'],
            confirm_paper_order=True,confirm_single_attempt=True,opener=opener,
            wall_clock=lambda:1000,elapsed_clock=lambda:50,permission_check=lambda:True,
            emergency_stop_check=lambda:False,credential_check=lambda:True)

    with tempfile.TemporaryDirectory(prefix='nexus-paper-submit-') as directory:
        root=Path(directory);journal,selected=prepared(root/'success');opener=Opener(journal)
        budget=PaperProviderPostBudget(root/'success-post-budget.sqlite3')
        result=submit_once(journal,'nx_submit_fixture',key,secret,**options(selected,opener,budget))
        retained=journal.recovery()['requests'][0]
        loss_journal,loss_selected=prepared(root/'loss');loss_opener=Opener(loss_journal,True)
        loss_budget=PaperProviderPostBudget(root/'loss-post-budget.sqlite3')
        loss_preserved=False
        try:submit_once(loss_journal,'nx_submit_fixture',key,secret,
            **options(loss_selected,loss_opener,loss_budget))
        except PaperSubmissionError as error:
            loss=loss_journal.recovery()['requests'][0]
            loss_preserved=(str(error)=='PAPER_SUBMISSION_TRANSPORT_OUTCOME_UNKNOWN_LOOKUP_REQUIRED'
                and loss['lookup_by_original_client_id_required'] and loss['snapshot'] is None)
        changed_journal,changed_selected=prepared(root/'changed')
        changed_budget=PaperProviderPostBudget(root/'changed-post-budget.sqlite3')
        changed_opener=Opener(changed_journal,
            late_account=dict(account,cash='999'))
        late_risk_change_blocked=False
        try:submit_once(changed_journal,'nx_submit_fixture',key,secret,
            **options(changed_selected,changed_opener,changed_budget))
        except PaperSubmissionError as error:
            late_risk_change_blocked=(str(error)=='PAPER_SUBMISSION_PREFLIGHT_STATE_CHANGED'
                and not changed_journal.recovery()['requests'][0]['submission_attempt_marked']
                and changed_budget.inspect()['attempts']==0
                and not any(request.get_method()=='POST' for request in changed_opener.requests))
        repeat_blocked=False
        try:submit_once(journal,'nx_submit_fixture',key,secret,**options(selected,Opener(journal),budget))
        except PaperSubmissionError as error:
            repeat_blocked=str(error)=='PAPER_SUBMISSION_LOCAL_REVIEW_BLOCKED'
        checks=dict(marker_committed_before_post=opener.marker_before_post,
            exactly_one_post=sum(request.get_method()=='POST' for request in opener.requests)==1,
            account_and_position_capacity_resampled=
                result['account_and_position_capacity_resampled'] is True and
                result['sequential_preflight_stable'] is True,
            late_risk_change_blocked_before_reservation=late_risk_change_blocked,
            response_retained=retained['snapshot']['status']=='new',
            transport_loss_preserved_for_original_id_lookup=loss_preserved,
            local_provider_post_budget_retained=budget.inspect()['completed_attempts']==1 and
                loss_budget.inspect()['review_required'] is True,
            repeat_submission_blocked=repeat_blocked,
            output_never_sendable=result['sendable'] is False and
                result['automatic_retry'] is False and result['ready_for_routing'] is False)
        return dict(source='INJECTED_SINGLE_ATTEMPT_PAPER_SUBMISSION_FIXTURE',
            passed=all(checks.values()),**checks,actual_provider_access=False,
            actual_vault_access=False,broker_account_reconciled=False,
            spendable_funds_verified=False,ready_for_routing=False,
            live_orders_enabled=False,money_movement_enabled=False,release_approved=False)
