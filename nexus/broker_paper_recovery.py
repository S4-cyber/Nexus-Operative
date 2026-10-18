"""Explicit original-ID paper lookup with retained attempt and outcome admission.

No default path, request preparation, sending, cancellation, retries or ledger
application. Only a previously uncertain original journal request is admitted.
"""
import hashlib
import time
from nexus import alpaca_paper_inspection as transport
from nexus.broker_lookup_policy import LOOKUP_URL


def recover_original(journal, client_order_id, api_key, api_secret, *, expected_account_id,
                     confirm_read_only_paper=False, opener=None, wall_clock=time.time,
                     elapsed_clock=time.monotonic, permission_check=lambda: True,
                     credential_check=lambda: True, pacing=None, expected_request_head=None):
    session = transport._ReadSession(api_key, api_secret,
        confirm_read_only_paper=confirm_read_only_paper, opener=opener,
        wall_clock=wall_clock, elapsed_clock=elapsed_clock, permission_check=permission_check,
        pacing=pacing, expected_account_id=expected_account_id,operation='ORIGINAL_ID_RECOVERY')
    expected = transport._uuid(expected_account_id)
    # Read-only before any network or write. No untracked/foreign lookup target.
    request = journal.lookup_request(client_order_id)
    if expected_request_head is not None and request['head_sha256']!=expected_request_head:
        transport._fail('PAPER_RECOVERY_SELECTION_HEAD_CHANGED')
    if request['account_sha256'] != hashlib.sha256(expected.encode()).hexdigest():
        transport._fail('PAPER_RECOVERY_ACCOUNT_SCOPE_DISAGREEMENT')
    with session:
        ticket = journal.begin_lookup_checked(client_order_id, admission=session.evaluated_at,expected_head=expected_request_head)
        before = transport._account(session.get(transport.URLS[0]))
        if before['id'] != expected:
            transport._fail('PAPER_RECOVERY_ACCOUNT_ID_DISAGREEMENT')
        outcome = session.get(LOOKUP_URL + '?client_order_id=' + client_order_id, lookup=True)
        double_checked = False
        if outcome.get('status_code') == 200:
            after = transport._account(session.get(transport.URLS[0]))
            if before != after:
                transport._fail('PAPER_RECOVERY_ACCOUNT_CHANGED_DURING_READ')
            double_checked = True
        # Non-found outcomes credit no observation. Preserve their cooldown/auth
        # blocks without extra requests after a rate limit or authentication loss.
        if credential_check() is not True:
            transport._fail('PAPER_RECOVERY_CREDENTIALS_CHANGED')
        session.checkpoint()
        result = journal.finish_lookup_checked(client_order_id, ticket['lookup_token'],
                                                admission=session.evaluated_at, **outcome)
        session.checkpoint()  # A revoked/lost acknowledgment never erases a committed observation.
        return dict(mode='EXPLICIT_PAPER_ORIGINAL_ID_RECOVERY',
                    source='INJECTED_UNATTESTED' if session.injected else 'PAPER_HTTPS_READS',
                    account_identity_checked=True, account_fields_double_checked=double_checked,
                    journal=result, automatic_retry=False, sendable=False,
                    ready_for_routing=False, spendable_funds_verified=False,
                    broker_account_reconciled=False, broker_connected=False,
                    live_orders_enabled=False, ledger_applied=False,
                    limitations='Original-ID lookup observations only. No inferred execution prices, fees, holdings, order absence or resubmission permission. Account reads are sequential, not atomic. Interrupted lookups remain pending for explicit review.')
