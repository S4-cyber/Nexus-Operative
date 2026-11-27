"""Explicit single-attempt Alpaca paper order transport.

This internal boundary is deliberately not wired to the Portal, saved read-only
credentials, strategies, startup, retries, cancellation or a ledger.  It checks
one prepared BTC/USD IOC-limit request against fixed paper-account endpoints,
commits the durable unknown-outcome marker before POST, and then sends at most
once.  Every exception after that commit leaves original-client-ID recovery
mandatory.
"""
from contextlib import contextmanager
from decimal import Decimal, localcontext
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

from nexus import alpaca_paper_contract as contract
from nexus import alpaca_paper_inspection as inspection
from nexus import emergency_intent
from nexus.broker_request_journal import BrokerRequestJournal
from nexus.paper_provider_post_budget import PaperProviderPostBudget
from nexus.sqlite_journal_guard import (capture_file_identities, check_path_set,
    require_file_identities)

ACCOUNT_URL = inspection.ROOT + '/v2/account'
POSITIONS_URL = inspection.ROOT + '/v2/positions'
OPEN_ORDERS_URL = inspection.URLS[2]
ASSET_URL = inspection.ASSET_URL
ORDER_URL = contract.PAPER_ORDERS_URL
REQUEST_TIMEOUT = 4
TOTAL_SECONDS = 16
MAX_REQUESTS = 12
MAX_BODY = inspection.MAX_BODY
POST_BUDGET_SCOPE = 'ALPACA_PAPER_ORDERS'
_TRUSTED_MONOTONIC = time.monotonic


class PaperSubmissionError(ValueError):
    """Fixed diagnostic only; never include credentials or provider bodies."""


def _fail(code):
    raise PaperSubmissionError('PAPER_SUBMISSION_' + code)


def _hash(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        _fail('REFERENCE_INVALID')
    return value


def _credential(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', value):
        _fail('CREDENTIAL_FORMAT_INVALID')


def _credential_pair_digest(api_key, api_secret):
    _credential(api_key); _credential(api_secret)
    return hashlib.sha256(
        api_key.encode('ascii') + b'\0' + api_secret.encode('ascii')).digest()


def _bind_emergency_stop_order_path(value, *, confirmed):
    if confirmed is not True or type(value) is not str or not value:
        _fail('EMERGENCY_STOP_BINDING_REQUIRED')
    try:
        selected = Path(value)
        parent = selected.parent.resolve(strict=True)
        canonical = parent / selected.name
        if (not selected.is_absolute() or selected != canonical or
                not selected.name):
            _fail('EMERGENCY_STOP_BINDING_INVALID')
        identity = emergency_intent._sample_directory(parent)
    except PaperSubmissionError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        _fail('EMERGENCY_STOP_BINDING_INVALID')
    return canonical, identity


class PaperTransportAuthorizationLease:
    """One-shot local credential/permission fence for a paper POST.

    The lease is OFF by default, stores only an in-memory digest of the exact
    credential pair, and serializes cooperating local revocation with opener
    entry.  It is not a provider credential lease, distributed lock, saved
    write scope, engagement permission, or release authority.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._armed = False
        self._claimed = False
        self._digest = None

    def arm_once(self, api_key, api_secret, *, confirm_single_attempt=False):
        if confirm_single_attempt is not True:
            _fail('TRANSPORT_AUTHORIZATION_CONFIRMATION_REQUIRED')
        digest = _credential_pair_digest(api_key, api_secret)
        with self._lock:
            if self._armed or self._claimed:
                _fail('TRANSPORT_AUTHORIZATION_ALREADY_USED')
            self._digest = digest
            self._armed = True
        return self

    def revoke(self):
        """Revoke before dispatch, or wait until an entered opener returns."""
        with self._lock:
            self._armed = False
            self._claimed = True
            self._digest = None

    @contextmanager
    def claim(self, api_key, api_secret):
        digest = _credential_pair_digest(api_key, api_secret)
        self._lock.acquire()
        try:
            if not self._armed or self._claimed or self._digest is None:
                _fail('TRANSPORT_AUTHORIZATION_NOT_ARMED')
            if not hmac.compare_digest(self._digest, digest):
                _fail('TRANSPORT_AUTHORIZATION_CREDENTIALS_CHANGED')
            # Consume before any later stop/head check.  A refused dispatch is
            # never silently re-armed.
            self._armed = False
            self._claimed = True
            self._digest = None
            yield
        finally:
            self._lock.release()


def _asset(raw, payload):
    if type(raw) is not dict or len(raw) > 64:
        _fail('ASSET_INVALID')
    text_fields = ('symbol', 'class', 'status')
    flag_fields = ('tradable', 'fractionable')
    number_fields = ('min_order_size', 'min_trade_increment',
                     'price_increment')
    if (any(type(raw.get(key)) is not str for key in text_fields) or
            any(type(raw.get(key)) is not bool for key in flag_fields) or
            any(type(raw.get(key)) not in (str, int, float, Decimal)
                for key in number_fields)):
        _fail('ASSET_INVALID')
    expected = {
        'symbol': 'BTC/USD', 'class': 'crypto', 'status': 'active',
        'tradable': True, 'fractionable': True,
    }
    if (any(raw.get(key) != value for key, value in expected.items()) or
            raw['tradable'] is not True or raw['fractionable'] is not True):
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
    return dict(symbol='BTC/USD',asset_class='crypto',status='active',
        tradable=True,fractionable=True,min_order_size=contract.text(minimum),
        min_trade_increment=contract.text(step),price_increment=contract.text(tick))


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


def _require_empty_open_orders(raw, expected_account_id, *, nonempty_code):
    """Require one bounded, account-consistent empty provider collection.

    Reported terms are not provider hold reconciliation or spendable-funds
    proof.  This POST boundary therefore admits no reported open orders rather
    than interpreting their quantities or prices as safe capacity offsets.
    """
    try:
        if (type(raw) is not list or len(raw) > inspection.MAX_ITEMS or
                any(type(row) is not dict for row in raw)):
            _fail('OPEN_ORDERS_INVALID')
        inspection._collection_rows(raw, expected_account_id)
    except PaperSubmissionError:
        raise
    except (inspection.PaperInspectionError, TypeError, ValueError):
        _fail('OPEN_ORDERS_INVALID')
    if raw:
        _fail(nonempty_code)


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


def _pin_stores(journal, post_budget, expected_account_id):
    """Retain private store objects that callbacks cannot retarget.

    The caller-held objects remain useful as a tamper signal.  All durable
    reads and writes use new exact-path/account/provider-bound instances.
    """
    expected_account_digest = hashlib.sha256(expected_account_id.encode()).hexdigest()
    expected_provider_digest = hashlib.sha256(POST_BUDGET_SCOPE.encode()).hexdigest()
    try:
        paths = check_path_set((journal.path, post_budget.path), 'PAPER_SUBMISSION_')
        if journal.account_digest != expected_account_digest:
            _fail('ACCOUNT_SCOPE_DISAGREEMENT')
        if (post_budget.provider_scope != POST_BUDGET_SCOPE or
                post_budget.provider_digest != expected_provider_digest):
            _fail('POST_BUDGET_SCOPE_DISAGREEMENT')
    except PaperSubmissionError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
        _fail('STORE_BINDING_INVALID')

    file_identities = None

    def check():
        try:
            current = check_path_set((journal.path, post_budget.path),
                                     'PAPER_SUBMISSION_')
            stable = (current == paths and
                journal.account_digest == expected_account_digest and
                post_budget.provider_scope == POST_BUDGET_SCOPE and
                post_budget.provider_digest == expected_provider_digest)
            if stable and file_identities is not None:
                require_file_identities(paths, file_identities,
                    'PAPER_SUBMISSION_')
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
            stable = False
        if not stable:
            _fail('STORE_BINDING_CHANGED')

    def pin_file_identities():
        nonlocal file_identities
        check()
        try:
            file_identities = capture_file_identities(paths,
                'PAPER_SUBMISSION_')
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
            _fail('STORE_FILE_IDENTITY_INVALID')
        check()

    return (BrokerRequestJournal(paths[0], expected_account_id),
            PaperProviderPostBudget(paths[1], POST_BUDGET_SCOPE), check,
            pin_file_identities)


def _reservation_state_check(journal, post_budget, *, client_order_id,
        expected_request_head, expected_payload_sha256, expected_admission,
        expected_post_token, expected_post_admitted_at, ownership):
    """Recheck both exact durable reservation tails without mutating them."""
    try:
        post_budget._owned(ownership)
        request = journal.recovery()
        budget = post_budget.inspect()
        request_again = journal.recovery()
        budget_again = post_budget.inspect()
        post_budget._owned(ownership)
        selected = [row for row in request['requests']
                    if row['client_order_id'] == client_order_id]
        stable = (request == request_again and budget == budget_again and
            request['head_sha256'] == expected_request_head and
            len(selected) == 1 and
            selected[0]['payload_sha256'] == expected_payload_sha256 and
            selected[0]['submission_attempt_marked'] is True and
            selected[0]['lookup_by_original_client_id_required'] is True and
            selected[0]['snapshot'] is None and
            selected[0]['submission_admission'] == expected_admission and
            budget['head_sha256'] == expected_post_token and
            budget['pending_token'] == expected_post_token and
            budget['last_evaluated_at'] == expected_post_admitted_at and
            expected_post_admitted_at <= expected_admission['evaluated_at'] and
            budget['review_required'] is False)
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError,
            KeyError):
        stable = False
    if not stable:
        _fail('POST_RESERVATION_DURABLE_STATE_CHANGED')


def _post_budget_reservation_token(post_budget, store_binding_check, *,
        expected_account_id, client_order_id, expected_request_head,
        expected_payload_sha256, expected_admission_floor,
        expected_admission_ceiling, ownership):
    """Cold-replay one exact pending provider reservation before any marker.

    The BEGIN return or raised detail is never durable evidence.  Continuation
    requires two identical read-only receipts plus the original in-process
    ownership lease, so a restart cannot turn an abandoned reservation into
    provider I/O.
    """
    try:
        store_binding_check()
        receipt = post_budget.pending_receipt()
        receipt_again = post_budget.pending_receipt()
        expected_account = hashlib.sha256(expected_account_id.encode()).hexdigest()
        expected_client = hashlib.sha256(client_order_id.encode()).hexdigest()
        expected_owner = ownership.identity()
        stable = (receipt == receipt_again and
            receipt['token'] == receipt['head_sha256'] and
            receipt['account_sha256'] == expected_account and
            receipt['client_order_sha256'] == expected_client and
            receipt['request_before_head_sha256'] == expected_request_head and
            receipt['payload_sha256'] == expected_payload_sha256 and
            receipt['owner_sha256'] == expected_owner and
            type(receipt['admitted_at']) in (int, float) and
            expected_admission_floor <= receipt['admitted_at'] <=
                expected_admission_ceiling and
            receipt['exact_pending_reservation_replayed'] is True and
            receipt['read_only'] is True and
            receipt['automatic_retry'] is False and
            receipt['ready_for_routing'] is False and
            receipt['release_approved'] is False)
        if stable:
            post_budget.confirm_active_pending(receipt['token'],
                account_id=expected_account_id,
                request_before_head_sha256=expected_request_head,
                payload_sha256=expected_payload_sha256,
                client_order_id=client_order_id,
                admitted_at=receipt['admitted_at'],
                ownership=ownership)
        store_binding_check()
    except BaseException:
        stable = False
    if not stable:
        _fail('POST_BUDGET_RESERVATION_UNCERTAIN')
    return receipt


def _request_marker_receipt(journal, store_binding_check, *,
        client_order_id, expected_previous_head, expected_payload_sha256,
        expected_admission_floor, expected_admission_ceiling):
    """Cold-replay one exact request marker after its write acknowledgement.

    The write return or exception is transport only.  Two full-chain reads must
    agree that the current head is the marker whose retained admission binds
    the exact previous head and payload.  A later append, fabricated return or
    pre-commit failure therefore grants no provider request authority.
    """
    try:
        store_binding_check()
        receipt = journal.recovery()
        receipt_again = journal.recovery()
        store_binding_check()
        selected = [row for row in receipt['requests']
                    if row['client_order_id'] == client_order_id]
        admission = selected[0]['submission_admission'] if len(selected) == 1 else None
        lookup = selected[0]['lookup'] if len(selected) == 1 else None
        stable = (receipt == receipt_again and len(selected) == 1 and
            type(admission) is dict and
            set(admission) == {'profile', 'clock_scope', 'evaluated_at',
                               'expected_head_sha256', 'payload_sha256'} and
            admission['profile'] == 'nexus.paper-submission-admission.v1' and
            admission['clock_scope'] ==
                'CALLER_WALL_SECONDS_NOT_SEND_OR_ARRIVAL' and
            admission['expected_head_sha256'] == expected_previous_head and
            admission['payload_sha256'] == expected_payload_sha256 and
            type(admission['evaluated_at']) in (int, float) and
            expected_admission_floor <= admission['evaluated_at'] <=
                expected_admission_ceiling and
            receipt['head_sha256'] != expected_previous_head and
            type(receipt['head_sha256']) is str and
            re.fullmatch(r'[0-9a-f]{64}', receipt['head_sha256']) is not None and
            selected[0]['payload_sha256'] == expected_payload_sha256 and
            selected[0]['submission_attempt_marked'] is True and
            selected[0]['lookup_by_original_client_id_required'] is True and
            selected[0]['snapshot'] is None and type(lookup) is dict and
            lookup['attempts'] == 0 and lookup['pending'] is False and
            lookup['review_blocked'] is False and lookup['interruptions'] == 0 and
            lookup['last_outcome'] is None and
            receipt['automatic_resume'] is False and
            receipt['automatic_submission'] is False and
            receipt['broker_connected'] is False and
            receipt['live_orders_enabled'] is False and
            receipt['ledger_applied'] is False)
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError,
            KeyError, IndexError):
        stable = False
    if not stable:
        _fail('REQUEST_MARKER_DURABLE_STATE_CHANGED')
    return dict(head_sha256=receipt['head_sha256'],
                submission_admission=dict(admission),
                exact_request_marker_replayed=True, read_only=True,
                sendable=False, routing_authorized=False,
                automatic_retry=False, live_orders_enabled=False,
                money_movement_enabled=False, release_approved=False)


def _post_budget_completion_head(post_budget, *, token, expected_account_id,
        client_order_id, expected_request_before_head, expected_payload_sha256,
        expected_request_after_head, expected_outcome, expected_actual_posts,
        expected_completed_at_floor, expected_completed_at_ceiling,
        failure_code, store_binding_check=None):
    """Cold-replay one exact terminal budget row without provider IO.

    The write-side acknowledgement is deliberately ignored.  This proves both
    normal response completion and the safety closure performed while another
    exception is active.  The latter cannot require the cross-store binding
    callback because a binding failure may be the exception being preserved;
    the exact attempt token still binds the immutable BEGIN row and ownership.
    """
    try:
        if store_binding_check is not None:
            store_binding_check()
        state = post_budget.inspect()
        history = post_budget.history()
        state_again = post_budget.inspect()
        history_again = post_budget.history()
        if store_binding_check is not None:
            store_binding_check()
        expected_account = hashlib.sha256(expected_account_id.encode()).hexdigest()
        expected_client = hashlib.sha256(client_order_id.encode()).hexdigest()
        completion_window_valid = (
            type(expected_completed_at_floor) in (int, float) and
            type(expected_completed_at_ceiling) in (int, float) and
            math.isfinite(expected_completed_at_floor) and
            math.isfinite(expected_completed_at_ceiling) and
            expected_completed_at_floor <= expected_completed_at_ceiling)
        stable = (state == state_again and history == history_again and
            completion_window_valid and
            len(history) >= 1 and state['pending_token'] is None and
            state['head_sha256'] == history[-1]['after_head_sha256'] and
            state['completed_attempts'] == len(history) and
            state['last_outcome'] == expected_outcome and
            state['review_required'] is (expected_outcome == 'OUTCOME_UNKNOWN') and
            history[-1]['token'] == token and
            history[-1]['account_sha256'] == expected_account and
            history[-1]['client_order_sha256'] == expected_client and
            history[-1]['request_before_head_sha256'] ==
                expected_request_before_head and
            history[-1]['payload_sha256'] == expected_payload_sha256 and
            history[-1]['request_after_head_sha256'] ==
                expected_request_after_head and
            history[-1]['outcome'] == expected_outcome and
            history[-1]['actual_posts'] == expected_actual_posts and
            type(history[-1]['completed_at']) in (int, float) and
            expected_completed_at_floor <= history[-1]['completed_at'] <=
                expected_completed_at_ceiling)
    except BaseException:
        stable = False
    if not stable:
        _fail(failure_code)
    return history[-1]['after_head_sha256']


def _safety_closure_request_receipt(journal, *, expected_account_id,
        client_order_id, expected_payload_sha256, expected_actual_posts):
    """Cold-replay the exact request head used by a safety closure.

    A safety closure can be preserving a store-binding failure, so this reader
    deliberately uses the already pinned journal object without requiring the
    cross-store callback.  Two complete replays must nevertheless agree and
    retain the exact marked request/account/payload.  The caller repeats this
    receipt after the provider-budget terminal write to reject a torn
    cross-journal closure without repeating provider IO.
    """
    try:
        receipt = journal.recovery()
        receipt_again = journal.recovery()
        selected = [row for row in receipt['requests']
                    if row['client_order_id'] == client_order_id]
        expected_account = hashlib.sha256(expected_account_id.encode()).hexdigest()
        stable = (receipt == receipt_again and
            receipt['mode'] == 'DISCONNECTED_DURABLE_BROKER_REQUEST_DIAGNOSTIC' and
            receipt['head_sha256'] != '0' * 64 and
            type(receipt['head_sha256']) is str and
            re.fullmatch(r'[0-9a-f]{64}', receipt['head_sha256']) is not None and
            journal.account_digest == expected_account and
            len(selected) == 1 and
            selected[0]['payload_sha256'] == expected_payload_sha256 and
            type(selected[0]['submission_attempt_marked']) is bool and
            selected[0]['lookup_by_original_client_id_required'] is
                selected[0]['submission_attempt_marked'] and
            (expected_actual_posts == 0 or
                selected[0]['submission_attempt_marked'] is True) and
            receipt['automatic_resume'] is False and
            receipt['automatic_submission'] is False and
            receipt['broker_connected'] is False and
            receipt['live_orders_enabled'] is False and
            receipt['ledger_applied'] is False)
    except BaseException:
        stable = False
    if not stable:
        _fail('POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN')
    return receipt


def _response_observation_receipt(journal, store_binding_check, *,
        client_order_id, response, expected_payload, expected_previous_head,
        expected_payload_sha256,
        failure_code='POST_RESPONSE_DURABLE_STATE_CHANGED'):
    """Cold-replay one exact request response without provider IO."""
    try:
        store_binding_check()
        receipt=journal.observation_receipt(client_order_id,response,
            expected_previous_head=expected_previous_head,
            expected_payload_sha256=expected_payload_sha256)
        receipt_again=journal.observation_receipt(client_order_id,response,
            expected_previous_head=expected_previous_head,
            expected_payload_sha256=expected_payload_sha256)
        store_binding_check()
        stable=(contract.sha(expected_payload)==expected_payload_sha256 and
            receipt==receipt_again and
            receipt['client_order_id']==client_order_id and
            receipt['payload_sha256']==expected_payload_sha256 and
            receipt['submission_attempt_marked'] is True and
            receipt['snapshot']==contract.normalize(response,expected_payload) and
            receipt['exact_response_observation_replayed'] is True and
            receipt['read_only'] is True and receipt['duplicate'] is False)
    except Exception:
        stable=False
    if not stable:
        _fail(failure_code)
    return receipt


class _Session:
    def __init__(self, key, secret, *, opener, wall_clock, elapsed_clock,
                 permission_check, emergency_stop_check, credential_check,
                 store_binding_check):
        self.key = key; self.secret = secret
        self.reader = opener or build_opener(ProxyHandler({}), inspection._NoRedirect())
        self.injected = opener is not None
        self.wall_clock = wall_clock; self.elapsed_clock = elapsed_clock
        self.permission_check = permission_check
        self.emergency_stop_check = emergency_stop_check
        self.credential_check = credential_check
        self.store_binding_check = store_binding_check
        self.reservation_check = None
        self.transport_authorization = None
        self.request_head_guard = None
        self.emergency_stop_path = None
        self.emergency_stop_parent_identity = None
        self.start = self.previous = None
        self.requests = 0
        self.post_attempted = False

    def credential_checkpoint(self, code):
        """Sample the caller's exact credential binding with fixed errors."""
        try:
            if self.credential_check() is not True:
                _fail(code)
        except PaperSubmissionError:
            raise
        except Exception:
            _fail('CREDENTIAL_CHECK_FAILED')

    def independent_stop_checkpoint(self):
        """Read the canonical durable stop after all pre-transport work."""
        try:
            if (self.emergency_stop_path is None or
                    self.emergency_stop_parent_identity is None):
                _fail('EMERGENCY_STOP_ACTIVE')
            parent = self.emergency_stop_path.parent
            emergency_intent._verify_directory(
                parent, self.emergency_stop_parent_identity, None)
            sample,_ = emergency_intent._TRUSTED_REVIEW_SAMPLE(
                self.emergency_stop_path)
            emergency_intent._verify_directory(
                parent, self.emergency_stop_parent_identity, None)
            if (type(sample) is not dict or
                    sample.get('requested') is not False):
                _fail('EMERGENCY_STOP_ACTIVE')
        except PaperSubmissionError:
            raise
        except Exception:
            _fail('ADMISSION_CHECK_FAILED')

    def checkpoint(self):
        try:
            self.store_binding_check()
            if self.reservation_check is not None:
                self.reservation_check()
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
            self.store_binding_check()
            if self.reservation_check is not None:
                self.reservation_check()
            return max(.001, min(REQUEST_TIMEOUT, TOTAL_SECONDS-(current[1]-self.start[1])))
        except PaperSubmissionError:
            raise
        except Exception:
            _fail('ADMISSION_CHECK_FAILED')

    def evaluated_at(self):
        self.checkpoint()
        return self.previous[0]

    def reconciliation_checkpoint(self):
        """Prove local durable scope without reauthorizing completed IO."""
        try:
            self.store_binding_check()
            if self.reservation_check is not None:
                self.reservation_check()
            self.store_binding_check()
            if self.reservation_check is not None:
                self.reservation_check()
        except PaperSubmissionError:
            raise
        except Exception:
            _fail('RECONCILIATION_CHECK_FAILED')

    def request(self, url, *, payload=None):
        if url not in (ACCOUNT_URL, POSITIONS_URL, OPEN_ORDERS_URL,
                       ASSET_URL, ORDER_URL):
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
        trusted_transport_start = None
        if method == 'POST':
            trusted_transport_start = _TRUSTED_MONOTONIC()
            if (type(trusted_transport_start) not in (int, float) or
                    not math.isfinite(trusted_transport_start)):
                _fail('CLOCK_INVALID')
        timeout = self.checkpoint(); self.requests += 1
        transport_deadline = (None if trusted_transport_start is None else
                              trusted_transport_start + timeout)
        response_handle = None
        response_entered = False
        def discard_unentered_response():
            if response_handle is None or response_entered:
                return
            try:
                close = getattr(response_handle, 'close', None)
                if callable(close):
                    close()
                else:
                    response_handle.__exit__(None, None, None)
            except Exception:
                pass
        try:
            if method == 'POST':
                # Finish opaque caller callbacks first.  The typed local lease
                # then serializes cooperating credential/permission revocation,
                # while the journal snapshot excludes cooperating SQLite
                # writers through synchronous opener entry.  The independent
                # stop remains unlocked and is sampled last.  Its writer never
                # waits on either local guard.
                self.credential_checkpoint('CREDENTIALS_CHANGED_AT_TRANSPORT')
                if (type(self.transport_authorization) is not
                        PaperTransportAuthorizationLease or
                        not callable(self.request_head_guard)):
                    _fail('TRANSPORT_AUTHORIZATION_REQUIRED')
                with self.transport_authorization.claim(self.key, self.secret):
                    with self.request_head_guard() as locked_read:
                        locked_read()
                        self.store_binding_check()
                        self.reservation_check()
                        self.store_binding_check()
                        locked_read()
                        remaining = transport_deadline - _TRUSTED_MONOTONIC()
                        if not math.isfinite(remaining) or remaining < .001:
                            _fail('TIME_BUDGET')
                        self.independent_stop_checkpoint()
                        remaining = transport_deadline - _TRUSTED_MONOTONIC()
                        if not math.isfinite(remaining) or remaining < .001:
                            _fail('TIME_BUDGET')
                        self.post_attempted = True
                        response_handle = self.reader.open(request,
                            timeout=min(timeout, remaining))
            else:
                response_handle = self.reader.open(request, timeout=timeout)
            with response_handle as response:
                response_entered = True
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
            _fail('TRANSPORT_OUTCOME_UNKNOWN_LOOKUP_REQUIRED'
                  if method == 'POST' and self.post_attempted
                  else 'PREFLIGHT_TRANSPORT_FAILED')
        except Exception:
            _fail('TRANSPORT_OUTCOME_UNKNOWN_LOOKUP_REQUIRED'
                  if method == 'POST' and self.post_attempted else
                  'PRE_POST_TRANSPORT_GUARD_FAILED' if method == 'POST' else
                  'PREFLIGHT_TRANSPORT_FAILED')
        finally:
            # Guard exit can raise any BaseException after opener entry but
            # before the response context is entered.  Always close that
            # unconsumed handle without swallowing the original signal.
            discard_unentered_response()


def submit_once(journal, client_order_id, api_key, api_secret, *,
                expected_account_id, expected_head, expected_payload_sha256,
                post_budget, expected_post_budget_head,
                confirm_paper_order=False, confirm_single_attempt=False,
                opener=None, wall_clock=time.time, elapsed_clock=time.monotonic,
                permission_check=lambda: False, emergency_stop_check=lambda: True,
                credential_check=lambda: False, transport_authorization=None,
                emergency_stop_order_path=None,
                confirm_emergency_stop_binding=False):
    """Submit one prepared paper IOC limit after durable uncertainty admission."""
    if confirm_paper_order is not True or confirm_single_attempt is not True:
        _fail('EXPLICIT_SINGLE_ATTEMPT_CONFIRMATION_REQUIRED')
    if type(journal) is not BrokerRequestJournal:
        _fail('JOURNAL_REQUIRED')
    # Counterfactual forward bindings describe a local modeled fill that has
    # already changed the synthetic ledger.  Reject before credentials, clocks,
    # callbacks, transport construction, account reads, or durable attempt marks.
    try:
        initial_selection=journal.lookup_request(client_order_id)
    except (OSError,ValueError,TypeError):
        _fail('JOURNAL_INVALID')
    if initial_selection['plan'].get('forward_intent_binding') is not None:
        _fail('COUNTERFACTUAL_FORWARD_INTENT_NOT_SENDABLE')
    if type(post_budget) is not PaperProviderPostBudget:
        _fail('POST_BUDGET_REQUIRED')
    if type(transport_authorization) is not PaperTransportAuthorizationLease:
        _fail('TRANSPORT_AUTHORIZATION_REQUIRED')
    (bound_emergency_stop_path,
     emergency_stop_parent_identity) = _bind_emergency_stop_order_path(
        emergency_stop_order_path, confirmed=confirm_emergency_stop_binding)
    _hash(expected_post_budget_head)
    _credential(api_key); _credential(api_secret)
    expected_account_id = inspection._uuid(expected_account_id)
    _hash(expected_head); _hash(expected_payload_sha256)
    bound_journal, bound_budget, store_binding_check, pin_file_identities = _pin_stores(
        journal, post_budget, expected_account_id)
    try:
        store_binding_check(); bound_budget.inspect(); store_binding_check()
    except PaperSubmissionError:
        raise
    except (OSError, ValueError, TypeError):
        _fail('POST_BUDGET_INVALID')
    selected = bound_journal.lookup_request(client_order_id); store_binding_check()
    passive = bound_journal.submission_review(
        client_order_id, evaluated_at=wall_clock()); store_binding_check()
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
                       emergency_stop_check=emergency_stop_check,
                       credential_check=credential_check,
                       store_binding_check=store_binding_check)
    session.checkpoint()
    account = session.request(ACCOUNT_URL)
    try:
        sampled_account = inspection._account(account)
    except (inspection.PaperInspectionError, TypeError, ValueError):
        _fail('ACCOUNT_INVALID')
    if sampled_account['id'] != expected_account_id:
        _fail('ACCOUNT_ID_DISAGREEMENT')
    positions = session.request(POSITIONS_URL)
    open_orders = session.request(OPEN_ORDERS_URL)
    _require_empty_open_orders(open_orders, expected_account_id,
        nonempty_code='OPEN_ORDER_RESERVATIONS_REVIEW_REQUIRED')
    asset = session.request(ASSET_URL)
    _account_and_funds(account, positions, expected_account_id, payload)
    initial_asset = _asset(asset, payload)
    initial_risk = _risk_snapshot(account, positions, expected_account_id)
    current_account = session.request(ACCOUNT_URL)
    current_positions = session.request(POSITIONS_URL)
    current_open_orders = session.request(OPEN_ORDERS_URL)
    _require_empty_open_orders(current_open_orders, expected_account_id,
        nonempty_code='PREFLIGHT_OPEN_ORDERS_CHANGED')
    _account_and_funds(current_account, current_positions, expected_account_id, payload)
    current_risk = _risk_snapshot(current_account, current_positions, expected_account_id)
    if current_risk != initial_risk:
        _fail('PREFLIGHT_STATE_CHANGED')
    session.credential_checkpoint('CREDENTIALS_CHANGED')
    # Materialize only the empty provider binding after permission, asset and
    # two stable risk samples have passed.  This lets the critical reservation
    # window pin both existing single-link database identities before either
    # durable attempt record is appended.
    try:
        bound_budget.ensure_initialized(expected_head=expected_post_budget_head)
        pin_file_identities()
    except PaperSubmissionError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
        _fail('STORE_FILE_IDENTITY_INVALID')
    with bound_budget.ownership() as owner:
        reservation_admission_floor = session.evaluated_at()
        try:
            bound_budget.begin(account_id=expected_account_id,
                request_before_head_sha256=expected_head,
                payload_sha256=expected_payload_sha256,
                client_order_id=client_order_id,
                admission=session.evaluated_at, ownership=owner,
                expected_head=expected_post_budget_head)
        except BaseException:
            # BEGIN may have committed while its local acknowledgement was
            # lost.  Never trust the raised detail or repeat the append; the
            # exact pending reservation and original lease are proved below.
            pass
        # The write callback itself advances ``session.previous`` on every
        # admitted sample.  Reuse that observed ceiling without introducing a
        # new permission/stop callback between commit and cold replay.
        reservation_admission_ceiling = session.previous[0]
        reservation = _post_budget_reservation_token(bound_budget,
            store_binding_check, expected_account_id=expected_account_id,
            client_order_id=client_order_id,
            expected_request_head=expected_head,
            expected_payload_sha256=expected_payload_sha256,
            expected_admission_floor=reservation_admission_floor,
            expected_admission_ceiling=reservation_admission_ceiling,
            ownership=owner)
        token = reservation['token']
        reservation_admitted_at = reservation['admitted_at']
        try:
            marker_admission_floor = session.evaluated_at()
            try:
                bound_journal.begin_submission_checked(client_order_id,
                    expected_head=expected_head,
                    expected_payload_sha256=expected_payload_sha256,
                    admission=session.evaluated_at)
            except Exception:
                # A request-marker commit acknowledgement can be lost.  Trust
                # neither the return nor raised detail; prove the exact marker
                # through the pinned read-only journal below.
                pass
            marker_admission_ceiling = session.previous[0]
            marker = _request_marker_receipt(bound_journal,
                store_binding_check, client_order_id=client_order_id,
                expected_previous_head=expected_head,
                expected_payload_sha256=expected_payload_sha256,
                expected_admission_floor=marker_admission_floor,
                expected_admission_ceiling=marker_admission_ceiling)
            marker_head = marker['head_sha256']
            marker_admission = marker['submission_admission']
            session.reservation_check = lambda: _reservation_state_check(
                bound_journal, bound_budget, client_order_id=client_order_id,
                expected_request_head=marker_head,
                expected_payload_sha256=expected_payload_sha256,
                expected_admission=marker_admission,
                expected_post_token=token,
                expected_post_admitted_at=reservation_admitted_at,
                ownership=owner)
            session.reservation_check()
            # Re-sample after both durable reservations and immediately before
            # the only POST.  A disagreement consumes the local request marker
            # as lookup-only; it never re-arms or sends the prepared request.
            final_account = session.request(ACCOUNT_URL)
            final_positions = session.request(POSITIONS_URL)
            final_risk = _risk_snapshot(final_account, final_positions,
                                        expected_account_id)
            if final_risk != current_risk:
                _fail('POST_RESERVATION_STATE_CHANGED')
            _account_and_funds(final_account, final_positions,
                               expected_account_id, payload)
            # Asset eligibility and increments are part of the same safety
            # premise as account funds and available positions.  Re-sample
            # them after both durable reservations: even a changed contract
            # that happens to admit this payload consumes the marker without
            # crossing the POST boundary.
            final_asset = session.request(ASSET_URL)
            if _asset(final_asset, payload) != initial_asset:
                _fail('POST_RESERVATION_ASSET_CHANGED')
            # Keep the most volatile provider collection as the last network
            # read before transport.  This remains sequential rather than
            # atomic, but does not add another GET-sized blind window.
            final_open_orders = session.request(OPEN_ORDERS_URL)
            _require_empty_open_orders(final_open_orders, expected_account_id,
                nonempty_code='POST_RESERVATION_OPEN_ORDERS_CHANGED')
            session.credential_checkpoint('CREDENTIALS_CHANGED_AFTER_MARKER')
            session.transport_authorization = transport_authorization
            session.emergency_stop_path = bound_emergency_stop_path
            session.emergency_stop_parent_identity = (
                emergency_stop_parent_identity)
            session.request_head_guard = lambda: (
                bound_journal.locked_request_snapshot(
                    client_order_id, expected_head=marker_head))
            raw = session.request(ORDER_URL, payload=payload)
            # A complete parsed provider response is already known.  From
            # here onward only local durable reconciliation is permitted; a
            # newly asserted stop or permission revocation must not discard
            # that known response into unknown-outcome recovery.  Retain the
            # exact store/reservation proofs without invoking control readers.
            session.reconciliation_checkpoint()
            # The POST outcome now has priority over the pre-POST reservation
            # fence.  Stop requiring the pre-response request head before
            # appending the observed response.  The checked append itself still
            # requires the exact marker head, so a concurrent lookup cannot be
            # silently composed with this response.
            session.reservation_check = None
            try:
                bound_journal.observe_checked(client_order_id, raw,
                    expected_head=marker_head,
                    expected_payload_sha256=expected_payload_sha256)
            except Exception:
                # A local response-commit acknowledgement can be lost.  Never
                # repeat provider IO; prove the exact committed event below.
                pass
            observed = _response_observation_receipt(bound_journal,
                store_binding_check,client_order_id=client_order_id,response=raw,
                expected_payload=payload,
                expected_previous_head=marker_head,
                expected_payload_sha256=expected_payload_sha256)
            observation_head = observed['head_sha256']
        except BaseException:
            outcome = 'OUTCOME_UNKNOWN' if session.post_attempted else 'NOT_SENT'
            # The active reservation guarantees at least one prior successful
            # checkpoint.  A safety closure must use that last trusted wall
            # sample exactly; it must not need to re-grant permission or ignore
            # an emergency stop merely to retire the local provider budget.
            closure_admitted_at = session.previous[0]
            closure_request = _safety_closure_request_receipt(bound_journal,
                expected_account_id=expected_account_id,
                client_order_id=client_order_id,
                expected_payload_sha256=expected_payload_sha256,
                expected_actual_posts=int(session.post_attempted))
            request_after_head = closure_request['head_sha256']
            try:
                bound_budget.finish(token, outcome=outcome,
                    request_after_head_sha256=request_after_head,
                    actual_posts=int(session.post_attempted),
                    admission=lambda: closure_admitted_at,
                    ownership=owner)
            except BaseException:
                # A commit acknowledgement can be lost while preserving the
                # failure that caused this safety closure.  Trust neither the
                # return value nor the raised detail; prove the exact row below.
                pass
            _post_budget_completion_head(bound_budget, token=token,
                expected_account_id=expected_account_id,
                client_order_id=client_order_id,
                expected_request_before_head=expected_head,
                expected_payload_sha256=expected_payload_sha256,
                expected_request_after_head=request_after_head,
                expected_outcome=outcome,
                expected_actual_posts=int(session.post_attempted),
                expected_completed_at_floor=closure_admitted_at,
                expected_completed_at_ceiling=closure_admitted_at,
                failure_code='POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN')
            closure_request_after = _safety_closure_request_receipt(
                bound_journal, expected_account_id=expected_account_id,
                client_order_id=client_order_id,
                expected_payload_sha256=expected_payload_sha256,
                expected_actual_posts=int(session.post_attempted))
            if closure_request_after != closure_request:
                _fail('POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN')
            raise
        # Provider IO is finished and the exact response is already durable.
        # Local reconciliation must not require engagement permission to be
        # granted again, or an asserted emergency stop could strand this known
        # result as a pending provider attempt.  Reuse the final trusted
        # transport checkpoint exactly; the terminal row and response are
        # still cold-replayed and cross-journal bound below.
        completion_admitted_at = session.previous[0]
        try:
            bound_budget.finish(token, outcome='RESPONSE_OBSERVED',
                request_after_head_sha256=observation_head, actual_posts=1,
                admission=lambda: completion_admitted_at, ownership=owner)
        except Exception:
            # A local commit acknowledgement can be lost.  Never repeat the
            # provider POST: accept only an exact cold replay of the completed
            # local result; otherwise leave the pending attempt review-blocked.
            pass
        post_budget_head = _post_budget_completion_head(bound_budget,
            store_binding_check=store_binding_check, token=token,
            expected_account_id=expected_account_id,
            client_order_id=client_order_id,
            expected_request_before_head=expected_head,
            expected_payload_sha256=expected_payload_sha256,
            expected_request_after_head=observation_head,
            expected_outcome='RESPONSE_OBSERVED', expected_actual_posts=1,
            expected_completed_at_floor=completion_admitted_at,
            expected_completed_at_ceiling=completion_admitted_at,
            failure_code='POST_BUDGET_COMPLETION_UNCERTAIN')
        # The successful cross-journal transition is not complete merely
        # because each side was proved independently before the budget write.
        # Replay the exact observation again after the terminal budget row so
        # a semantic-reader failure or an invalidated request prefix cannot be
        # hidden by the already committed provider-budget result.  A later
        # valid original-ID lookup remains permitted because the receipt binds
        # the immutable OBSERVE prefix rather than requiring it to be the
        # current request head.
        observed_after_completion = _response_observation_receipt(
            bound_journal, store_binding_check,
            client_order_id=client_order_id, response=raw,
            expected_payload=payload, expected_previous_head=marker_head,
            expected_payload_sha256=expected_payload_sha256,
            failure_code='POST_BUDGET_COMPLETION_UNCERTAIN')
        # ``current_head_sha256`` may legitimately advance through a later
        # original-ID lookup.  Every field describing the exact immutable
        # OBSERVE prefix must still match.
        observation_binding = {key:value for key,value in observed.items()
                               if key != 'current_head_sha256'}
        observation_binding_after = {
            key:value for key,value in observed_after_completion.items()
            if key != 'current_head_sha256'}
        if observation_binding_after != observation_binding:
            _fail('POST_BUDGET_COMPLETION_UNCERTAIN')
    store_binding_check()
    return dict(mode='EXPLICIT_SINGLE_ATTEMPT_ALPACA_PAPER_SUBMISSION',
        source='INJECTED_UNATTESTED' if session.injected else 'PAPER_HTTPS_WRITE',
        paper_only=True, single_attempt_consumed=True,
        durable_unknown_outcome_marker_committed=True,
        account_and_position_capacity_resampled=True,
        sequential_preflight_stable=True,
        open_order_reservations_absent_at_three_samples=True,
        post_reservation_risk_resampled=True,
        post_reservation_asset_resampled=True,
        post_transport_credentials_rechecked=True,
        post_transport_controls_rechecked=True,
        typed_local_transport_authorization_consumed=True,
        pre_post_journal_writer_exclusion=True,
        explicit_emergency_stop_binding=True,
        trusted_transport_deadline_enforced=True,
        terminal_store_bindings_rechecked=True,
        request_and_post_budget_store_bindings_pinned=True,
        request_and_post_budget_file_identities_pinned=True,
        post_reservation_durable_heads_rechecked=True,
        response_observation_head_bound_to_post_budget=True,
        post_budget_reservation_cold_replayed=True,
        request_marker_cold_replayed=True,
        reservation_and_marker_admission_windows_bound=True,
        response_observation_cold_replayed=True,
        response_budget_completion_cold_replayed=True,
        post_response_persistence_stop_independent=True,
        post_response_local_completion_stop_independent=True,
        response_observation_completion_bracketed=True,
        response_and_safety_completion_windows_bound=True,
        safety_closure_request_head_bracketed=True,
        alpaca_post_budget_scope_required=True,
        paper_order_response_observed=True, journal=observed,
        post_budget_head_sha256=post_budget_head,
        local_provider_post_pacing=True, broker_wide_quota_verified=False,
        automatic_retry=False, automatic_continuation=False, sendable=False,
        ready_for_routing=False, broker_account_reconciled=False,
        spendable_funds_verified=False, ledger_applied=False,
        live_orders_enabled=False, money_movement_enabled=False,
        release_approved=False,
        limitations="One explicitly confirmed Alpaca paper POST after two matching fixed-endpoint asset eligibility/increment samples, two matching pre-reservation account/BTC-position admission samples, one matching post-reservation account/BTC-position sample and three sequential bounded empty reported-open-order samples. Any reported open order blocks rather than being interpreted as a provider hold or spendable-capacity offset. Exact request and Alpaca provider-budget paths/scopes are pinned in private retained store objects. Opaque caller callbacks finish before an OFF-by-default, one-shot typed local credential/permission lease and request-journal writer-exclusion snapshot are acquired. The exact credential pair and local permission remain serialized for cooperating in-process writers through synchronous opener entry, while cooperating SQLite writers cannot advance the marked request head before that entry. The caller explicitly confirms one canonical owner runtime/order path for the durable latch; it is never inferred from broker evidence storage. After opaque callbacks, the originally pinned request/provider-budget identities, exact pending reservation and a callback-free trusted monotonic deadline are rechecked under the request-head guard. The independent emergency stop does not share either guard and is sampled last; a cut observed before opener entry closes the retained reservation as NOT_SENT, consumes the one-shot authorization and cannot re-arm automatically. The provider-budget BEGIN and request-marker write returns are ignored; their exact durable records are accepted only after stable read-only replay, with the pending provider reservation additionally requiring the original in-process ownership lease. A restart may inspect or explicitly close retained state but cannot silently resume provider IO. Once a complete response is parsed, response persistence and terminal local provider-budget reconciliation use local store/reservation proofs plus the final trusted transport timestamp without permission or emergency-stop reauthorization; they perform no further provider IO and remain exact-head/cold-replay checked. The response append atomically requires the exact marked request head and payload; both the exact response event and provider-budget completion are accepted only after stable cold replay, and the immutable response observation is replayed again after the terminal budget write. Lost local commit acknowledgements never repeat provider IO, while uncommitted results remain pending and review-blocked. This is cooperating local linearization plus a sampled stop, not an atomic provider snapshot, a distributed credential/permission lease, provider hold reconciliation, a provider-side revocation guarantee, descriptor-held multi-file atomicity, an ACL audit or protection against an administrator. A reported order can appear between sequential samples; a non-cooperating writer, provider-side credential revocation, another host/process outside the typed lease, or a stop asserted after the final sample is not atomically excluded. Any lost/ambiguous POST outcome requires original-client-ID recovery, blocks the POST budget for review and can never be resent automatically. The budget coordinates cooperating callers sharing one private path only; broker-wide quota and distributed ownership are unverified. Matching sequential reads are not atomic, observed amounts are not settled-funds proof, and a response is not execution/fee/hold/account acceptance. No Portal route, saved write credential scope, strategy integration, retry, cancellation, ledger application, live endpoint, funding or release authority.")


def fixture_report():
    """Disposable injected single-attempt proof; no credentials or provider IO."""
    import io
    import tempfile
    from pathlib import Path
    from nexus import emergency_intent

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
        def __init__(self,url,value,on_eof=None):
            self.url=url;self.body=io.BytesIO(json.dumps(value).encode())
            self.on_eof=on_eof
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def geturl(self):return self.url
        def read(self,size):
            chunk=self.body.read(size)
            if not chunk and self.on_eof is not None:self.on_eof()
            return chunk
    class Opener:
        def __init__(self,journal,fail=False,late_account=None,late_positions=None,
                     final_account=None,final_positions=None,final_asset=None,
                     initial_open_orders=None,late_open_orders=None,
                     final_open_orders=None):
            self.journal=journal;self.fail=fail;self.requests=[]
            self.values=[account,positions,
                [] if initial_open_orders is None else initial_open_orders,asset,
                account if late_account is None else late_account,
                positions if late_positions is None else late_positions,
                [] if late_open_orders is None else late_open_orders,
                account if final_account is None else final_account,
                positions if final_positions is None else final_positions,
                asset if final_asset is None else final_asset,
                [] if final_open_orders is None else final_open_orders,raw]
            self.marker_before_post=False;self.post_response_complete=False
        def open(self,request,timeout):
            self.requests.append(request)
            if request.get_method()=='POST':
                retained=self.journal.recovery()['requests'][0]
                self.marker_before_post=retained['lookup_by_original_client_id_required']
                if self.fail:raise URLError('fixture transport loss')
            on_eof=(lambda:setattr(self,'post_response_complete',True)
                    if request.get_method()=='POST' else None)
            return Reply(request.full_url,self.values[len(self.requests)-1],on_eof)

    def prepared(root):
        journal=BrokerRequestJournal(root/'requests.sqlite3',account_id)
        plan=contract.preview('nx_submit_fixture','BTC-USD','BUY','2','100',asset,
            metadata_at=1000,evaluated_at=1001)
        journal.prepare(plan);selected=journal.lookup_request('nx_submit_fixture')
        return journal,selected
    def options(selected,opener,budget):
        authorization=PaperTransportAuthorizationLease()
        authorization.arm_once(key,secret,confirm_single_attempt=True)
        return dict(expected_account_id=account_id,expected_head=selected['head_sha256'],
            expected_payload_sha256=selected['plan']['payload_sha256'],
            post_budget=budget,expected_post_budget_head=budget.inspect()['head_sha256'],
            confirm_paper_order=True,confirm_single_attempt=True,opener=opener,
            wall_clock=lambda:1000,elapsed_clock=lambda:50,permission_check=lambda:True,
            emergency_stop_check=lambda:False,credential_check=lambda:True,
            transport_authorization=authorization,
            emergency_stop_order_path=str(opener.journal.path.resolve()),
            confirm_emergency_stop_binding=True)

    with tempfile.TemporaryDirectory(prefix='nexus-paper-submit-') as directory:
        root=Path(directory);journal,selected=prepared(root/'success');opener=Opener(journal)
        budget=PaperProviderPostBudget(root/'success-post-budget.sqlite3')
        completion_control_observed=[]
        def completion_permission():
            observed=(opener.post_response_complete or
                journal.recovery()['requests'][0]['snapshot'] is not None)
            completion_control_observed.append(observed)
            return not observed
        def completion_stop():
            observed=(opener.post_response_complete or
                journal.recovery()['requests'][0]['snapshot'] is not None)
            completion_control_observed.append(observed)
            return observed
        result=submit_once(journal,'nx_submit_fixture',key,secret,
            **dict(options(selected,opener,budget),
                   permission_check=completion_permission,
                   emergency_stop_check=completion_stop))
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
        late_orders_journal,late_orders_selected=prepared(root/'late-open-orders')
        late_orders_budget=PaperProviderPostBudget(
            root/'late-open-orders-post-budget.sqlite3')
        late_orders_opener=Opener(late_orders_journal,
            late_open_orders=[dict(raw)])
        late_open_order_blocked=False
        try:submit_once(late_orders_journal,'nx_submit_fixture',key,secret,
            **options(late_orders_selected,late_orders_opener,
                      late_orders_budget))
        except PaperSubmissionError as error:
            late_open_order_blocked=(
                str(error)=='PAPER_SUBMISSION_PREFLIGHT_OPEN_ORDERS_CHANGED'
                and not late_orders_journal.recovery()['requests'][0]
                    ['submission_attempt_marked']
                and late_orders_budget.inspect()['attempts']==0
                and not any(request.get_method()=='POST'
                            for request in late_orders_opener.requests))
        reserved_journal,reserved_selected=prepared(root/'reserved-changed')
        reserved_budget=PaperProviderPostBudget(root/'reserved-changed-post-budget.sqlite3')
        reserved_opener=Opener(reserved_journal,
            final_account=dict(account,cash='998'))
        post_reservation_change_blocked=False
        safety_closure_cold_replayed=False
        try:submit_once(reserved_journal,'nx_submit_fixture',key,secret,
            **options(reserved_selected,reserved_opener,reserved_budget))
        except PaperSubmissionError as error:
            history=reserved_budget.history()
            post_reservation_change_blocked=(
                str(error)=='PAPER_SUBMISSION_POST_RESERVATION_STATE_CHANGED'
                and reserved_journal.recovery()['requests'][0]
                    ['lookup_by_original_client_id_required']
                and len(history)==1 and history[0]['outcome']=='NOT_SENT'
                and history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in reserved_opener.requests))
            if len(history)==1:
                try:
                    replayed_head=_post_budget_completion_head(reserved_budget,
                        token=history[0]['token'],expected_account_id=account_id,
                        client_order_id='nx_submit_fixture',
                        expected_request_before_head=reserved_selected['head_sha256'],
                        expected_payload_sha256=
                            reserved_selected['plan']['payload_sha256'],
                        expected_request_after_head=
                            history[0]['request_after_head_sha256'],
                        expected_outcome='NOT_SENT',expected_actual_posts=0,
                        expected_completed_at_floor=1000,
                        expected_completed_at_ceiling=1000,
                        failure_code='POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN')
                    safety_closure_cold_replayed=(
                        replayed_head==history[0]['after_head_sha256'])
                except PaperSubmissionError:
                    pass
        reserved_orders_journal,reserved_orders_selected=prepared(
            root/'reserved-open-orders')
        reserved_orders_budget=PaperProviderPostBudget(
            root/'reserved-open-orders-post-budget.sqlite3')
        reserved_orders_opener=Opener(reserved_orders_journal,
            final_open_orders=[dict(raw)])
        post_reservation_open_order_blocked=False
        try:submit_once(reserved_orders_journal,'nx_submit_fixture',key,secret,
            **options(reserved_orders_selected,reserved_orders_opener,
                      reserved_orders_budget))
        except PaperSubmissionError as error:
            orders_history=reserved_orders_budget.history()
            post_reservation_open_order_blocked=(
                str(error)==
                    'PAPER_SUBMISSION_POST_RESERVATION_OPEN_ORDERS_CHANGED'
                and reserved_orders_journal.recovery()['requests'][0]
                    ['lookup_by_original_client_id_required']
                and len(orders_history)==1
                and orders_history[0]['outcome']=='NOT_SENT'
                and orders_history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in reserved_orders_opener.requests))
        asset_journal,asset_selected=prepared(root/'reserved-asset-changed')
        asset_budget=PaperProviderPostBudget(
            root/'reserved-asset-changed-post-budget.sqlite3')
        asset_opener=Opener(asset_journal,
            final_asset=dict(asset,min_trade_increment='.25'))
        post_reservation_asset_change_blocked=False
        try:submit_once(asset_journal,'nx_submit_fixture',key,secret,
            **options(asset_selected,asset_opener,asset_budget))
        except PaperSubmissionError as error:
            asset_history=asset_budget.history()
            post_reservation_asset_change_blocked=(
                str(error)=='PAPER_SUBMISSION_POST_RESERVATION_ASSET_CHANGED'
                and asset_journal.recovery()['requests'][0]
                    ['lookup_by_original_client_id_required']
                and len(asset_history)==1 and asset_history[0]['outcome']=='NOT_SENT'
                and asset_history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in asset_opener.requests))
        credential_journal,credential_selected=prepared(root/'credential-change')
        credential_budget=PaperProviderPostBudget(
            root/'credential-change-post-budget.sqlite3')
        credential_opener=Opener(credential_journal)
        credential_current=True;credential_samples=0
        def credential_check():
            nonlocal credential_samples
            credential_samples += 1
            return credential_current
        def credential_permission():
            nonlocal credential_current
            if credential_samples >= 2 and len(credential_opener.requests)==11:
                credential_current=False
            return True
        post_transport_credential_change_blocked=False
        try:submit_once(credential_journal,'nx_submit_fixture',key,secret,
            **dict(options(credential_selected,credential_opener,
                           credential_budget),
                   permission_check=credential_permission,
                   credential_check=credential_check))
        except PaperSubmissionError as error:
            credential_history=credential_budget.history()
            post_transport_credential_change_blocked=(
                str(error)=='PAPER_SUBMISSION_CREDENTIALS_CHANGED_AT_TRANSPORT'
                and credential_samples==3
                and credential_journal.recovery()['requests'][0]
                    ['lookup_by_original_client_id_required']
                and len(credential_history)==1
                and credential_history[0]['outcome']=='NOT_SENT'
                and credential_history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in credential_opener.requests))
        stop_journal,stop_selected=prepared(root/'terminal-stop')
        stop_budget=PaperProviderPostBudget(
            root/'terminal-stop-post-budget.sqlite3')
        stop_opener=Opener(stop_journal);stop_credential_samples=0
        def stop_credential_check():
            nonlocal stop_credential_samples
            stop_credential_samples += 1
            if stop_credential_samples==3:
                emergency_intent.persist(stop_journal.path)
            return True
        post_transport_stop_blocked=False
        try:submit_once(stop_journal,'nx_submit_fixture',key,secret,
            **dict(options(stop_selected,stop_opener,stop_budget),
                   emergency_stop_check=lambda:emergency_intent.requested(
                       stop_journal.path),
                   credential_check=stop_credential_check))
        except PaperSubmissionError as error:
            stop_history=stop_budget.history()
            post_transport_stop_blocked=(
                str(error)=='PAPER_SUBMISSION_EMERGENCY_STOP_ACTIVE'
                and stop_credential_samples==3
                and emergency_intent.requested(stop_journal.path)
                and stop_journal.recovery()['requests'][0]
                    ['lookup_by_original_client_id_required']
                and len(stop_history)==1
                and stop_history[0]['outcome']=='NOT_SENT'
                and stop_history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in stop_opener.requests))
        binding_journal,binding_selected=prepared(root/'binding')
        binding_budget=PaperProviderPostBudget(root/'binding-post-budget.sqlite3')
        binding_request_path=binding_journal.path
        binding_budget_path=binding_budget.path
        binding_opener=Opener(binding_journal);retargeted=False
        def binding_stop():
            nonlocal retargeted
            binding_retained=BrokerRequestJournal(
                binding_request_path,account_id).recovery()['requests'][0]
            if binding_retained['submission_attempt_marked'] and not retargeted:
                binding_journal.path=root/'decoy-request.sqlite3'
                binding_budget.path=root/'decoy-budget.sqlite3'
                retargeted=True
            return False
        store_bindings_pinned=False
        try:submit_once(binding_journal,'nx_submit_fixture',key,secret,
            **dict(options(binding_selected,binding_opener,binding_budget),
                   emergency_stop_check=binding_stop))
        except PaperSubmissionError as error:
            binding_retained=BrokerRequestJournal(
                binding_request_path,account_id).recovery()['requests'][0]
            history=PaperProviderPostBudget(binding_budget_path).history()
            store_bindings_pinned=(str(error)=='PAPER_SUBMISSION_STORE_BINDING_CHANGED'
                and retargeted and binding_retained['lookup_by_original_client_id_required']
                and len(history)==1 and history[0]['outcome']=='NOT_SENT'
                and history[0]['actual_posts']==0
                and not (root/'decoy-request.sqlite3').exists()
                and not (root/'decoy-budget.sqlite3').exists()
                and not any(request.get_method()=='POST'
                            for request in binding_opener.requests))
        identity_journal,identity_selected=prepared(root/'identity')
        identity_budget=PaperProviderPostBudget(
            root/'identity-post-budget.sqlite3')
        identity_opener=Opener(identity_journal);identity_replaced=False
        def identity_stop():
            nonlocal identity_replaced
            identity_retained=BrokerRequestJournal(
                identity_journal.path,account_id).recovery()['requests'][0]
            if identity_retained['submission_attempt_marked'] and not identity_replaced:
                replacement=root/'identity-request-replacement.sqlite3'
                replacement.write_bytes(identity_journal.path.read_bytes())
                os.replace(replacement,identity_journal.path)
                identity_replaced=True
            return False
        file_identities_pinned=False
        try:submit_once(identity_journal,'nx_submit_fixture',key,secret,
            **dict(options(identity_selected,identity_opener,identity_budget),
                   emergency_stop_check=identity_stop))
        except PaperSubmissionError as error:
            identity_retained=BrokerRequestJournal(
                identity_journal.path,account_id).recovery()['requests'][0]
            history=identity_budget.history()
            file_identities_pinned=(
                str(error)=='PAPER_SUBMISSION_STORE_BINDING_CHANGED'
                and identity_replaced
                and identity_retained['lookup_by_original_client_id_required']
                and len(history)==1 and history[0]['outcome']=='NOT_SENT'
                and history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in identity_opener.requests))
        durable_journal,durable_selected=prepared(root/'durable-head')
        durable_budget=PaperProviderPostBudget(
            root/'durable-head-post-budget.sqlite3')
        durable_opener=Opener(durable_journal);durable_advanced=False
        def durable_stop():
            nonlocal durable_advanced
            durable_retained=durable_journal.recovery()['requests'][0]
            if durable_retained['submission_attempt_marked'] and not durable_advanced:
                durable_journal.begin_lookup('nx_submit_fixture',evaluated_at=1001)
                durable_advanced=True
            return False
        durable_heads_rechecked=False
        try:submit_once(durable_journal,'nx_submit_fixture',key,secret,
            **dict(options(durable_selected,durable_opener,durable_budget),
                   emergency_stop_check=durable_stop))
        except PaperSubmissionError as error:
            durable_retained=durable_journal.recovery()['requests'][0]
            history=durable_budget.history()
            durable_heads_rechecked=(
                str(error)==
                    'PAPER_SUBMISSION_POST_RESERVATION_DURABLE_STATE_CHANGED'
                and durable_advanced
                and durable_retained['lookup_by_original_client_id_required']
                and durable_retained['lookup']['pending'] is True
                and len(history)==1 and history[0]['outcome']=='NOT_SENT'
                and history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in durable_opener.requests))
        ack_journal,ack_selected=prepared(root/'reservation-ack')
        ack_budget=PaperProviderPostBudget(
            root/'reservation-ack-post-budget.sqlite3')
        ack_opener=Opener(ack_journal);reservation_ack_cold_replayed=False
        original_begin=PaperProviderPostBudget.begin
        def lose_reservation_ack(self,*args,**kwargs):
            original_begin(self,*args,**kwargs)
            raise RuntimeError('fixture lost reservation acknowledgement')
        PaperProviderPostBudget.begin=lose_reservation_ack
        try:
            ack_result=submit_once(ack_journal,'nx_submit_fixture',key,secret,
                **options(ack_selected,ack_opener,ack_budget))
            reservation_ack_cold_replayed=(
                ack_result['post_budget_reservation_cold_replayed'] is True
                and len(ack_budget.history())==1
                and ack_budget.history()[0]['outcome']=='RESPONSE_OBSERVED'
                and sum(request.get_method()=='POST'
                        for request in ack_opener.requests)==1)
        except PaperSubmissionError:
            pass
        finally:
            PaperProviderPostBudget.begin=original_begin
        marker_journal,marker_selected=prepared(root/'marker-ack')
        marker_budget=PaperProviderPostBudget(
            root/'marker-ack-post-budget.sqlite3')
        marker_opener=Opener(marker_journal);request_marker_cold_replayed=False
        original_marker=BrokerRequestJournal.begin_submission_checked
        def lose_marker_ack(self,*args,**kwargs):
            original_marker(self,*args,**kwargs)
            raise RuntimeError('fixture lost request marker acknowledgement')
        BrokerRequestJournal.begin_submission_checked=lose_marker_ack
        try:
            marker_result=submit_once(marker_journal,'nx_submit_fixture',key,
                secret,**options(marker_selected,marker_opener,marker_budget))
            request_marker_cold_replayed=(
                marker_result['request_marker_cold_replayed'] is True
                and len(marker_budget.history())==1
                and marker_budget.history()[0]['outcome']=='RESPONSE_OBSERVED'
                and sum(request.get_method()=='POST'
                        for request in marker_opener.requests)==1)
        except PaperSubmissionError:
            pass
        finally:
            BrokerRequestJournal.begin_submission_checked=original_marker
        completion_window_journal,completion_window_selected=prepared(
            root/'completion-window')
        completion_window_budget=PaperProviderPostBudget(
            root/'completion-window-post-budget.sqlite3')
        completion_window_opener=Opener(completion_window_journal)
        response_completion_window_bound=False
        original_finish=PaperProviderPostBudget.finish
        def wrong_response_completion_admission(self,*args,**kwargs):
            if kwargs.get('outcome')=='RESPONSE_OBSERVED':
                kwargs=dict(kwargs,admission=lambda:1001)
            return original_finish(self,*args,**kwargs)
        PaperProviderPostBudget.finish=wrong_response_completion_admission
        try:
            submit_once(completion_window_journal,'nx_submit_fixture',key,
                secret,**options(completion_window_selected,
                                 completion_window_opener,
                                 completion_window_budget))
        except PaperSubmissionError as error:
            completion_history=completion_window_budget.history()
            response_completion_window_bound=(
                str(error)=='PAPER_SUBMISSION_POST_BUDGET_COMPLETION_UNCERTAIN'
                and len(completion_history)==1
                and completion_history[0]['outcome']=='RESPONSE_OBSERVED'
                and completion_history[0]['actual_posts']==1
                and completion_history[0]['completed_at']==1001
                and sum(request.get_method()=='POST'
                        for request in completion_window_opener.requests)==1)
        finally:
            PaperProviderPostBudget.finish=original_finish
        response_bracket_journal,response_bracket_selected=prepared(
            root/'response-observation-completion-bracket')
        response_bracket_budget=PaperProviderPostBudget(
            root/'response-observation-completion-bracket-post-budget.sqlite3')
        response_bracket_opener=Opener(response_bracket_journal)
        response_observation_completion_bracketed=False
        response_completion_written=False
        original_observation_receipt=BrokerRequestJournal.observation_receipt
        def close_response_reader_after_finish(self,*args,**kwargs):
            nonlocal response_completion_written
            result=original_finish(self,*args,**kwargs)
            if kwargs.get('outcome')=='RESPONSE_OBSERVED':
                response_completion_written=True
            return result
        def fail_response_reader_after_finish(self,*args,**kwargs):
            if response_completion_written:
                raise RuntimeError('fixture private response reader detail')
            return original_observation_receipt(self,*args,**kwargs)
        PaperProviderPostBudget.finish=close_response_reader_after_finish
        BrokerRequestJournal.observation_receipt=fail_response_reader_after_finish
        try:
            submit_once(response_bracket_journal,'nx_submit_fixture',key,
                secret,**options(response_bracket_selected,
                                 response_bracket_opener,
                                 response_bracket_budget))
        except PaperSubmissionError as error:
            response_bracket_history=response_bracket_budget.history()
            response_observation_completion_bracketed=(
                str(error)=='PAPER_SUBMISSION_POST_BUDGET_COMPLETION_UNCERTAIN'
                and response_completion_written
                and len(response_bracket_history)==1
                and response_bracket_history[0]['outcome']=='RESPONSE_OBSERVED'
                and response_bracket_history[0]['actual_posts']==1
                and sum(request.get_method()=='POST'
                        for request in response_bracket_opener.requests)==1)
        finally:
            BrokerRequestJournal.observation_receipt=original_observation_receipt
            PaperProviderPostBudget.finish=original_finish
        safety_window_journal,safety_window_selected=prepared(
            root/'safety-completion-window')
        safety_window_budget=PaperProviderPostBudget(
            root/'safety-completion-window-post-budget.sqlite3')
        safety_window_opener=Opener(safety_window_journal)
        not_sent_completion_window_bound=False
        def wrong_safety_completion_admission(self,*args,**kwargs):
            if kwargs.get('outcome')=='NOT_SENT':
                kwargs=dict(kwargs,admission=lambda:1001)
            return original_finish(self,*args,**kwargs)
        def safety_window_stop():
            retained_safety=safety_window_journal.recovery()['requests'][0]
            return retained_safety['submission_attempt_marked']
        PaperProviderPostBudget.finish=wrong_safety_completion_admission
        try:
            submit_once(safety_window_journal,'nx_submit_fixture',key,secret,
                **dict(options(safety_window_selected,safety_window_opener,
                               safety_window_budget),
                       emergency_stop_check=safety_window_stop))
        except PaperSubmissionError as error:
            safety_window_history=safety_window_budget.history()
            not_sent_completion_window_bound=(
                str(error)==
                    'PAPER_SUBMISSION_POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN'
                and len(safety_window_history)==1
                and safety_window_history[0]['outcome']=='NOT_SENT'
                and safety_window_history[0]['actual_posts']==0
                and safety_window_history[0]['completed_at']==1001
                and not any(request.get_method()=='POST'
                            for request in safety_window_opener.requests))
        finally:
            PaperProviderPostBudget.finish=original_finish
        unknown_window_journal,unknown_window_selected=prepared(
            root/'unknown-completion-window')
        unknown_window_budget=PaperProviderPostBudget(
            root/'unknown-completion-window-post-budget.sqlite3')
        unknown_window_opener=Opener(unknown_window_journal,fail=True)
        outcome_unknown_completion_window_bound=False
        def wrong_unknown_completion_admission(self,*args,**kwargs):
            if kwargs.get('outcome')=='OUTCOME_UNKNOWN':
                kwargs=dict(kwargs,admission=lambda:1001)
            return original_finish(self,*args,**kwargs)
        PaperProviderPostBudget.finish=wrong_unknown_completion_admission
        try:
            submit_once(unknown_window_journal,'nx_submit_fixture',key,secret,
                **options(unknown_window_selected,unknown_window_opener,
                          unknown_window_budget))
        except PaperSubmissionError as error:
            unknown_window_history=unknown_window_budget.history()
            outcome_unknown_completion_window_bound=(
                str(error)==
                    'PAPER_SUBMISSION_POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN'
                and len(unknown_window_history)==1
                and unknown_window_history[0]['outcome']=='OUTCOME_UNKNOWN'
                and unknown_window_history[0]['actual_posts']==1
                and unknown_window_history[0]['completed_at']==1001
                and sum(request.get_method()=='POST'
                        for request in unknown_window_opener.requests)==1)
        finally:
            PaperProviderPostBudget.finish=original_finish
        closure_head_journal,closure_head_selected=prepared(
            root/'closure-request-head')
        closure_head_budget=PaperProviderPostBudget(
            root/'closure-request-head-post-budget.sqlite3')
        closure_head_opener=Opener(closure_head_journal)
        safety_closure_request_head_bracketed=False
        closure_head_advanced=False
        def advance_request_after_safety_finish(self,*args,**kwargs):
            nonlocal closure_head_advanced
            result=original_finish(self,*args,**kwargs)
            if kwargs.get('outcome')=='NOT_SENT' and not closure_head_advanced:
                closure_head_journal.begin_lookup('nx_submit_fixture',
                    evaluated_at=1001)
                closure_head_advanced=True
            return result
        def closure_head_stop():
            return closure_head_journal.recovery()['requests'][0][
                'submission_attempt_marked']
        PaperProviderPostBudget.finish=advance_request_after_safety_finish
        try:
            submit_once(closure_head_journal,'nx_submit_fixture',key,secret,
                **dict(options(closure_head_selected,closure_head_opener,
                               closure_head_budget),
                       emergency_stop_check=closure_head_stop))
        except PaperSubmissionError as error:
            closure_head_history=closure_head_budget.history()
            closure_head_retained=closure_head_journal.recovery()['requests'][0]
            safety_closure_request_head_bracketed=(
                str(error)==
                    'PAPER_SUBMISSION_POST_BUDGET_SAFETY_CLOSURE_UNCERTAIN'
                and closure_head_advanced
                and closure_head_retained['lookup']['pending'] is True
                and len(closure_head_history)==1
                and closure_head_history[0]['outcome']=='NOT_SENT'
                and closure_head_history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in closure_head_opener.requests))
        finally:
            PaperProviderPostBudget.finish=original_finish
        reservation_window_journal,reservation_window_selected=prepared(
            root/'reservation-window')
        reservation_window_budget=PaperProviderPostBudget(
            root/'reservation-window-post-budget.sqlite3')
        reservation_window_opener=Opener(reservation_window_journal)
        reservation_admission_window_bound=False
        def wrong_reservation_admission(self,*args,**kwargs):
            return original_begin(self,*args,
                **dict(kwargs,admission=lambda:999))
        PaperProviderPostBudget.begin=wrong_reservation_admission
        try:
            submit_once(reservation_window_journal,'nx_submit_fixture',key,
                secret,**options(reservation_window_selected,
                                 reservation_window_opener,
                                 reservation_window_budget))
        except PaperSubmissionError as error:
            retained_window=reservation_window_journal.recovery()['requests'][0]
            reservation_window_state=reservation_window_budget.inspect()
            reservation_admission_window_bound=(
                str(error)=='PAPER_SUBMISSION_POST_BUDGET_RESERVATION_UNCERTAIN'
                and retained_window['submission_attempt_marked'] is False
                and reservation_window_state['pending_token'] is not None
                and reservation_window_state['last_evaluated_at']==999
                and reservation_window_budget.history()==()
                and not any(request.get_method()=='POST'
                            for request in reservation_window_opener.requests))
        finally:
            PaperProviderPostBudget.begin=original_begin
        marker_window_journal,marker_window_selected=prepared(
            root/'marker-window')
        marker_window_budget=PaperProviderPostBudget(
            root/'marker-window-post-budget.sqlite3')
        marker_window_opener=Opener(marker_window_journal)
        request_marker_admission_window_bound=False
        def wrong_marker_admission(self,*args,**kwargs):
            return original_marker(self,*args,
                **dict(kwargs,admission=lambda:1001))
        BrokerRequestJournal.begin_submission_checked=wrong_marker_admission
        try:
            submit_once(marker_window_journal,'nx_submit_fixture',key,secret,
                **options(marker_window_selected,marker_window_opener,
                          marker_window_budget))
        except PaperSubmissionError as error:
            retained_window=marker_window_journal.recovery()['requests'][0]
            marker_window_history=marker_window_budget.history()
            request_marker_admission_window_bound=(
                str(error)=='PAPER_SUBMISSION_REQUEST_MARKER_DURABLE_STATE_CHANGED'
                and retained_window['submission_attempt_marked'] is True
                and retained_window['submission_admission']['evaluated_at']==1001
                and len(marker_window_history)==1
                and marker_window_history[0]['outcome']=='NOT_SENT'
                and marker_window_history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in marker_window_opener.requests))
        finally:
            BrokerRequestJournal.begin_submission_checked=original_marker
        repeat_blocked=False
        try:submit_once(journal,'nx_submit_fixture',key,secret,**options(selected,Opener(journal),budget))
        except PaperSubmissionError as error:
            repeat_blocked=str(error)=='PAPER_SUBMISSION_LOCAL_REVIEW_BLOCKED'
        typed_asset_journal,typed_asset_selected=prepared(
            root/'typed-asset-flags')
        typed_asset_budget=PaperProviderPostBudget(
            root/'typed-asset-flags-post-budget.sqlite3')
        typed_asset_opener=Opener(typed_asset_journal,
            final_asset=dict(asset,tradable=1))
        exact_asset_flag_types_required=False
        try:
            submit_once(typed_asset_journal,'nx_submit_fixture',key,secret,
                **options(typed_asset_selected,typed_asset_opener,
                          typed_asset_budget))
        except PaperSubmissionError as error:
            typed_history=typed_asset_budget.history()
            exact_asset_flag_types_required=(
                str(error)=='PAPER_SUBMISSION_ASSET_INVALID'
                and len(typed_history)==1
                and typed_history[0]['outcome']=='NOT_SENT'
                and typed_history[0]['actual_posts']==0
                and not any(request.get_method()=='POST'
                            for request in typed_asset_opener.requests))
        owner_journal,owner_selected=prepared(root/'final-owner-reproof')
        owner_budget=PaperProviderPostBudget(
            root/'final-owner-reproof-post-budget.sqlite3')
        owner_opener=Opener(owner_journal);owner_lost=False
        owner_samples=0;owner_type=type(owner_budget.ownership())
        original_owner_validate=owner_type.validate
        def lost_owner_validate(self):
            if owner_lost:
                raise ValueError(
                    'PAPER_PROVIDER_POST_BUDGET_OWNER_FILE_CHANGED')
            return original_owner_validate(self)
        def lose_owner_at_final_transport_sample():
            nonlocal owner_lost,owner_samples
            owner_samples+=1
            if owner_samples==3: owner_lost=True
            return True
        owner_type.validate=lost_owner_validate
        final_provider_owner_reproved_before_post=False
        try:
            submit_once(owner_journal,'nx_submit_fixture',key,secret,
                **dict(options(owner_selected,owner_opener,owner_budget),
                    credential_check=lose_owner_at_final_transport_sample))
        except ValueError as error:
            owner_state=owner_budget.inspect()
            final_provider_owner_reproved_before_post=(
                str(error)=='PAPER_PROVIDER_POST_BUDGET_OWNER_FILE_CHANGED'
                and owner_samples==3
                and owner_state['pending_token'] is not None
                and owner_state['review_required'] is False
                and owner_budget.history()==()
                and not any(request.get_method()=='POST'
                            for request in owner_opener.requests))
        finally:
            owner_type.validate=original_owner_validate
        checks=dict(marker_committed_before_post=opener.marker_before_post,
            exactly_one_post=sum(request.get_method()=='POST' for request in opener.requests)==1,
            account_and_position_capacity_resampled=
                result['account_and_position_capacity_resampled'] is True and
                result['sequential_preflight_stable'] is True,
            post_reservation_risk_resampled=
                result['post_reservation_risk_resampled'] is True,
            post_reservation_asset_resampled=
                result['post_reservation_asset_resampled'] is True,
            post_transport_credentials_rechecked=
                result['post_transport_credentials_rechecked'] is True and
                post_transport_credential_change_blocked,
            post_transport_controls_rechecked=
                result['post_transport_controls_rechecked'] is True and
                post_transport_stop_blocked,
            typed_local_transport_authorization_consumed=
                result['typed_local_transport_authorization_consumed'] is True,
            pre_post_journal_writer_exclusion=
                result['pre_post_journal_writer_exclusion'] is True,
            explicit_emergency_stop_binding=
                result['explicit_emergency_stop_binding'] is True,
            trusted_transport_deadline_enforced=
                result['trusted_transport_deadline_enforced'] is True,
            terminal_store_bindings_rechecked=
                result['terminal_store_bindings_rechecked'] is True,
            request_and_post_budget_store_bindings_pinned=
                result['request_and_post_budget_store_bindings_pinned'] is True and
                store_bindings_pinned,
            request_and_post_budget_file_identities_pinned=
                result['request_and_post_budget_file_identities_pinned'] is True and
                file_identities_pinned,
            post_reservation_durable_heads_rechecked=
                result['post_reservation_durable_heads_rechecked'] is True and
                durable_heads_rechecked,
            response_observation_head_bound_to_post_budget=
                result['response_observation_head_bound_to_post_budget'] is True and
                budget.history()[0]['request_after_head_sha256']==
                    result['journal']['head_sha256'],
            post_budget_reservation_cold_replayed=
                result['post_budget_reservation_cold_replayed'] is True and
                reservation_ack_cold_replayed,
            request_marker_cold_replayed=
                result['request_marker_cold_replayed'] is True and
                request_marker_cold_replayed,
            reservation_admission_window_bound=
                result['reservation_and_marker_admission_windows_bound'] is True and
                reservation_admission_window_bound,
            request_marker_admission_window_bound=
                result['reservation_and_marker_admission_windows_bound'] is True and
                request_marker_admission_window_bound,
            response_observation_cold_replayed=
                result['response_observation_cold_replayed'] is True and
                result['journal']['exact_response_observation_replayed'] is True,
            response_budget_completion_cold_replayed=
                result['response_budget_completion_cold_replayed'] is True and
                budget.history()[0]['after_head_sha256']==
                    result['post_budget_head_sha256'],
            post_response_persistence_stop_independent=
                result['post_response_persistence_stop_independent'] is True and
                opener.post_response_complete and
                completion_control_observed and
                not any(completion_control_observed),
            post_response_local_completion_stop_independent=
                result['post_response_local_completion_stop_independent'] is True and
                completion_control_observed and
                not any(completion_control_observed),
            response_observation_completion_bracketed=
                result['response_observation_completion_bracketed'] is True and
                response_observation_completion_bracketed,
            response_completion_window_bound=
                result['response_and_safety_completion_windows_bound'] is True and
                response_completion_window_bound,
            not_sent_completion_window_bound=
                result['response_and_safety_completion_windows_bound'] is True and
                not_sent_completion_window_bound,
            outcome_unknown_completion_window_bound=
                result['response_and_safety_completion_windows_bound'] is True and
                outcome_unknown_completion_window_bound,
            safety_closure_request_head_bracketed=
                result['safety_closure_request_head_bracketed'] is True and
                safety_closure_request_head_bracketed,
            alpaca_post_budget_scope_required=
                result['alpaca_post_budget_scope_required'] is True,
            open_order_reservations_absent_at_three_samples=
                result['open_order_reservations_absent_at_three_samples'] is True and
                sum(request.full_url==OPEN_ORDERS_URL
                    for request in opener.requests)==3,
            late_open_order_blocked_before_reservation=late_open_order_blocked,
            post_reservation_open_order_blocked_before_post=
                post_reservation_open_order_blocked,
            late_risk_change_blocked_before_reservation=late_risk_change_blocked,
            post_reservation_risk_change_blocked_before_post=
                post_reservation_change_blocked,
            post_reservation_asset_change_blocked_before_post=
                post_reservation_asset_change_blocked,
            exact_asset_flag_types_required=exact_asset_flag_types_required,
            final_provider_owner_reproved_before_post=
                final_provider_owner_reproved_before_post,
            safety_closure_cold_replayed=safety_closure_cold_replayed,
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
