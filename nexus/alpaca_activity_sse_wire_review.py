"""Bounded supplied-wire review for the Alpaca Activity SSE endpoint.

This module validates an exact ``since_id`` request target, streaming response
metadata, UTF-8/SSE framing, documented comments, and the already-supported
BTC/USD trade/fee envelopes.  It deliberately consumes caller-supplied bytes:
no socket, credential, cursor write, reconnect, retry, or ledger action occurs.
"""
import json
import re
from urllib.parse import urlencode

from nexus.alpaca_activity_sse_review import (
    FLAGS as ENVELOPE_FLAGS, SOURCES as ENVELOPE_SOURCES, ULID,
    _event, review as envelope_review)
from nexus.alpaca_paper_contract import canonical, sha


POLICY = 'nexus.supplied-alpaca-activity-sse-wire-review.v1'
ENDPOINT = 'https://paper-api.alpaca.markets/v2beta1/events/activities'
SOURCES = (*ENVELOPE_SOURCES,
    'https://docs.alpaca.markets/us/reference/subscribetoactivitiessse')
MAX_CHUNKS = 256
MAX_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_EVENTS = 2000
SLOW = re.compile(r': you are reading too slowly, dropped ([1-9][0-9]{0,11}) messages')
FLAGS = (*ENVELOPE_FLAGS, 'actual_provider_access', 'actual_vault_access',
    'actual_stream_connection', 'authenticated_session_verified',
    'response_source_attested', 'cursor_checkpoint_eligible',
    'automatic_reconnect_performed', 'automatic_backoff_verified',
    'slow_consumer_recovery_verified', 'historical_replay_complete')


class ActivitySSEWireReviewError(ValueError):
    """Fixed diagnostic only; never echo supplied stream bytes."""


def _fail(code):
    raise ActivitySSEWireReviewError(
        'ALPACA_ACTIVITY_SSE_WIRE_REVIEW_' + code) from None


def _cursor(value):
    if type(value) is not str or ULID.fullmatch(value) is None:
        _fail('SINCE_ID_INVALID')
    return value


def activity_url(since_id):
    """Return the sole admitted cursor-resumption target."""
    return ENDPOINT + '?' + urlencode((('since_id', _cursor(since_id)),))


def _headers(value):
    if type(value) is not dict or not value or len(value) > 32:
        _fail('HEADERS_INVALID')
    normalized = {}
    for key, item in value.items():
        if (type(key) is not str or type(item) is not str or
                len(key) > 128 or len(item) > 1024):
            _fail('HEADERS_INVALID')
        name = key.strip().lower()
        if not name or name in normalized:
            _fail('HEADERS_INVALID')
        normalized[name] = item.strip()
    content_type = normalized.get('content-type', '').lower()
    parts = [part.strip() for part in content_type.split(';')]
    if not parts or parts[0] != 'text/event-stream' or any(
            part not in ('charset=utf-8', 'charset="utf-8"')
            for part in parts[1:]):
        _fail('CONTENT_TYPE_INVALID')
    if normalized.get('content-encoding', 'identity').lower() != 'identity':
        _fail('CONTENT_ENCODING_DENIED')
    return normalized


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('JSON_DUPLICATE_KEY')
        result[key] = value
    return result


def _stream(chunks):
    if type(chunks) is not list or not 1 <= len(chunks) <= MAX_CHUNKS:
        _fail('CHUNK_CAP')
    total = 0
    body = bytearray()
    for chunk in chunks:
        if type(chunk) is not bytes or not chunk:
            _fail('CHUNK_INVALID')
        total += len(chunk)
        if total > MAX_BYTES:
            _fail('BYTE_CAP')
        body.extend(chunk)
    try:
        text = body.decode('utf-8', 'strict')
    except UnicodeDecodeError:
        _fail('UTF8_INVALID')
    if text.startswith('\ufeff') or '\x00' in text or re.search(r'\r(?!\n)', text):
        _fail('FRAMING_INVALID')
    text = text.replace('\r\n', '\n')
    if not text.endswith('\n\n'):
        _fail('TERMINATOR_MISSING')
    lines = text.split('\n')
    if any(len(line.encode('utf-8')) > MAX_LINE_BYTES for line in lines):
        _fail('LINE_CAP')
    return text, lines


def _frames(lines):
    events = []
    data = None
    heartbeats = 0
    dropped = 0
    internal_errors = 0
    comments = 0
    for line in lines:
        if line == '':
            if data is not None:
                try:
                    event = json.loads(data, object_pairs_hook=_pairs,
                        parse_constant=lambda _: _fail('JSON_NUMBER_INVALID'))
                except ActivitySSEWireReviewError:
                    raise
                except (ValueError, TypeError, json.JSONDecodeError):
                    _fail('JSON_INVALID')
                if type(event) is not dict:
                    _fail('JSON_EVENT_INVALID')
                events.append(event)
                if len(events) > MAX_EVENTS:
                    _fail('EVENT_CAP')
                data = None
            continue
        if line.startswith(':'):
            if data is not None:
                _fail('COMMENT_INSIDE_EVENT')
            comments += 1
            if line == ':heartbeat':
                heartbeats += 1
            elif line == ': internal server error':
                internal_errors += 1
            else:
                match = SLOW.fullmatch(line)
                if match is None:
                    _fail('COMMENT_UNSUPPORTED')
                dropped += int(match.group(1))
            continue
        if line.startswith('data:'):
            if data is not None:
                _fail('MULTILINE_DATA_UNSUPPORTED')
            data = line[5:]
            if data.startswith(' '):
                data = data[1:]
            if not data:
                _fail('DATA_EMPTY')
            continue
        _fail('FIELD_UNSUPPORTED')
    if data is not None:
        _fail('TERMINATOR_MISSING')
    if not events:
        _fail('EVENTS_REQUIRED')
    return events, heartbeats, comments, dropped, internal_errors


def review(chunks, *, expected_account_id, since_id, response_url,
        response_status, response_headers):
    """Review one supplied finite wire segment without opening a connection."""
    cursor = _cursor(since_id)
    if response_url != activity_url(cursor):
        _fail('RESPONSE_URL_INVALID')
    if type(response_status) is not int or response_status != 200:
        _fail('STATUS_INVALID')
    headers = _headers(response_headers)
    raw, lines = _stream(chunks)
    events, heartbeats, comments, dropped, internal_errors = _frames(lines)
    try:
        envelope = envelope_review(events,
            expected_account_id=expected_account_id)
    except (ValueError, TypeError):
        _fail('ENVELOPE_INVALID')
    event_ids = [row['event_id'] for row in events]
    if event_ids[0] < cursor or event_ids[-1] <= cursor:
        _fail('CURSOR_WINDOW_INVALID')
    interrupted = dropped > 0 or internal_errors > 0
    canonical_input = dict(policy=POLICY,
        response_url_sha256=sha(response_url), response_status=response_status,
        content_type=headers['content-type'].lower(), since_id=cursor,
        wire_sha256=sha(raw), account_sha256=envelope['account_sha256'])
    return dict(mode='SUPPLIED_ALPACA_ACTIVITY_SSE_WIRE_SESSION_REVIEW',
        status='RECONNECT_REVIEW_REQUIRED' if interrupted else 'REVIEW_REQUIRED',
        read_only=True, input_sha256=sha(canonical_input),
        account_sha256=envelope['account_sha256'], sources=SOURCES,
        request_target_exact=True, response_metadata_valid=True,
        utf8_and_sse_framing_valid=True, chunk_count=len(chunks),
        bytes_reviewed=len(raw.encode('utf-8')), events_reviewed=len(events),
        trades_reviewed=envelope['trades_reviewed'],
        fees_reviewed=envelope['fees_reviewed'],
        heartbeat_comments=heartbeats, comments_reviewed=comments,
        dropped_messages_reported=dropped,
        internal_error_comments=internal_errors,
        gap_or_server_error_observed=interrupted,
        reconnect_review_required=interrupted,
        first_event_id_sha256=sha(event_ids[0]),
        last_event_id_sha256=sha(event_ids[-1]),
        since_id_sha256=sha(cursor), cursor_window_forward=True,
        all_admitted_fees_parent_linked=
            envelope['all_admitted_fees_parent_linked'],
        **{flag: False for flag in FLAGS},
        limitations='Supplied finite response bytes and metadata only. No socket, TLS, DNS, authentication, credential, provider, heartbeat clock, stream entitlement, reconnect, backoff, cursor write, source attestation, complete delivery, fee coverage, holds, settlement, spendable funds, ledger, routing, live trading or release approval. A slow-consumer or internal-error comment requires separate reconnect review and can never authorize a cursor checkpoint.')


def fixture_report():
    """Aggregate-only framing fixture; no provider, credential, or storage I/O."""
    account = '01234567-89ab-cdef-0123-456789abcdef'
    cursor = '01K00000000000000000000001'
    trade = _event(cursor, '11111111-1111-1111-1111-111111111111', 'TRD')
    fee = _event('01K00000000000000000000002',
        '22222222-2222-2222-2222-222222222222', 'FEE')
    target = activity_url(cursor)
    headers = {'Content-Type': 'text/event-stream; charset=utf-8'}
    wire = (':heartbeat\n\ndata: ' + canonical(trade) + '\n\ndata:' +
        canonical(fee) + '\n\n').encode()
    chunks = [wire[:7], wire[7:91], wire[91:]]
    clean = review(chunks, expected_account_id=account, since_id=cursor,
        response_url=target, response_status=200, response_headers=headers)
    slow_wire = wire + b': you are reading too slowly, dropped 2 messages\n\n'
    slow = review([slow_wire], expected_account_id=account, since_id=cursor,
        response_url=target, response_status=200, response_headers=headers)
    error_wire = wire + b': internal server error\n\n'
    internal = review([error_wire], expected_account_id=account,
        since_id=cursor, response_url=target, response_status=200,
        response_headers=headers)
    checks = dict(exact_since_id_target_bound=clean['request_target_exact'] and
            clean['since_id_sha256'] == sha(cursor),
        arbitrary_chunk_boundaries_reassembled=clean['chunk_count'] == 3 and
            clean['utf8_and_sse_framing_valid'],
        heartbeat_and_linked_events_reviewed=clean['heartbeat_comments'] == 1 and
            clean['events_reviewed'] == 2 and
            clean['all_admitted_fees_parent_linked'],
        slow_consumer_requires_reconnect=slow['reconnect_review_required'] and
            slow['dropped_messages_reported'] == 2 and
            not slow['cursor_checkpoint_eligible'],
        internal_error_requires_reconnect=internal['reconnect_review_required'] and
            internal['internal_error_comments'] == 1 and
            not internal['cursor_checkpoint_eligible'],
        output_never_authoritative=all(clean[flag] is False for flag in FLAGS))
    return dict(source='SYNTHETIC_SUPPLIED_ACTIVITY_SSE_WIRE_FIXTURE',
        passed=all(checks.values()), **checks,
        actual_storage_access=False, **{flag: False for flag in FLAGS})
