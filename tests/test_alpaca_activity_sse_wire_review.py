"""Raw framing, request-scope, and interruption tests for Activity SSE."""
import copy
import json

import pytest

from nexus import alpaca_activity_sse_wire_review as subject
from nexus.alpaca_activity_sse_review import _event
from nexus.alpaca_paper_contract import canonical, sha


ACCOUNT = '01234567-89ab-cdef-0123-456789abcdef'
CURSOR = '01K00000000000000000000001'
LATER = '01K00000000000000000000002'
HEADERS = {'Content-Type': 'text/event-stream; charset="utf-8"'}


def events():
    return [
        _event(CURSOR, '11111111-1111-1111-1111-111111111111', 'TRD'),
        _event(LATER, '22222222-2222-2222-2222-222222222222', 'FEE')]


def wire(rows=None, prefix=':heartbeat\n\n', suffix=''):
    rows = events() if rows is None else rows
    return (prefix + ''.join('data: ' + canonical(row) + '\n\n'
        for row in rows) + suffix).encode()


def checked(chunks=None, **changes):
    args = dict(expected_account_id=ACCOUNT, since_id=CURSOR,
        response_url=subject.activity_url(CURSOR), response_status=200,
        response_headers=HEADERS)
    args.update(changes)
    return subject.review([wire()] if chunks is None else chunks, **args)


def test_clean_chunked_session_binds_target_framing_and_envelopes():
    raw = wire()
    result = checked([raw[:1], raw[1:19], raw[19:101], raw[101:]])
    assert result['mode'] == 'SUPPLIED_ALPACA_ACTIVITY_SSE_WIRE_SESSION_REVIEW'
    assert result['status'] == 'REVIEW_REQUIRED' and result['read_only']
    assert result['request_target_exact'] and result['response_metadata_valid']
    assert result['utf8_and_sse_framing_valid'] and result['chunk_count'] == 4
    assert result['heartbeat_comments'] == 1 and result['events_reviewed'] == 2
    assert result['trades_reviewed'] == result['fees_reviewed'] == 1
    assert result['first_event_id_sha256'] == sha(CURSOR)
    assert result['last_event_id_sha256'] == sha(LATER)
    assert result['cursor_window_forward'] and not result['reconnect_review_required']
    assert all(result[flag] is False for flag in subject.FLAGS)
    encoded = json.dumps(result)
    assert ACCOUNT not in encoded and CURSOR not in encoded and 'fixture_client' not in encoded


@pytest.mark.parametrize(('suffix','field','expected'), [
    (': you are reading too slowly, dropped 12 messages\n\n',
        'dropped_messages_reported', 12),
    (': internal server error\n\n', 'internal_error_comments', 1),
])
def test_documented_interruption_comments_require_reconnect_without_checkpoint(suffix, field, expected):
    result = checked([wire(suffix=suffix)])
    assert result['status'] == 'RECONNECT_REVIEW_REQUIRED'
    assert result['gap_or_server_error_observed']
    assert result['reconnect_review_required']
    assert result[field] == expected
    assert result['cursor_checkpoint_eligible'] is False
    assert result['automatic_reconnect_performed'] is False


@pytest.mark.parametrize(('changes','code'), [
    ({'since_id': 'bad'}, 'SINCE_ID_INVALID'),
    ({'response_url': subject.ENDPOINT}, 'RESPONSE_URL_INVALID'),
    ({'response_url': subject.ENDPOINT + '?since_id=' + CURSOR + '&until_id=' + LATER},
        'RESPONSE_URL_INVALID'),
    ({'response_status': True}, 'STATUS_INVALID'),
    ({'response_status': 204}, 'STATUS_INVALID'),
    ({'response_headers': {'Content-Type': 'application/json'}}, 'CONTENT_TYPE_INVALID'),
    ({'response_headers': {'Content-Type': 'text/event-stream',
        'Content-Encoding': 'gzip'}}, 'CONTENT_ENCODING_DENIED'),
])
def test_exact_request_and_response_metadata_scope(changes, code):
    with pytest.raises(subject.ActivitySSEWireReviewError, match=code):
        checked(**changes)


@pytest.mark.parametrize(('raw','code'), [
    (b'data: {}\n', 'TERMINATOR_MISSING'),
    (b'event: activity\n\ndata: {}\n\n', 'FIELD_UNSUPPORTED'),
    (b':unknown\n\ndata: {}\n\n', 'COMMENT_UNSUPPORTED'),
    (b'data: {}\ndata: {}\n\n', 'MULTILINE_DATA_UNSUPPORTED'),
    (b'data:\n\n', 'DATA_EMPTY'),
    (b'\xff\n\n', 'UTF8_INVALID'),
    (b'data: {}\r\n\rX', 'FRAMING_INVALID'),
])
def test_malformed_wire_fails_with_fixed_diagnostic(raw, code):
    with pytest.raises(subject.ActivitySSEWireReviewError, match=code):
        checked([raw])


def test_duplicate_json_keys_and_bad_envelope_are_never_admitted():
    duplicate = wire(prefix='', rows=[]).decode() + (
        'data: {"account_id":"' + ACCOUNT + '","account_id":"' + ACCOUNT + '"}\n\n')
    with pytest.raises(subject.ActivitySSEWireReviewError, match='JSON_DUPLICATE_KEY'):
        checked([duplicate.encode()])
    bad = events(); bad[0] = copy.deepcopy(bad[0]); bad[0]['currency'] = 'EUR'
    with pytest.raises(subject.ActivitySSEWireReviewError, match='ENVELOPE_INVALID'):
        checked([wire(bad)])


def test_cursor_window_requires_inclusive_replay_and_forward_progress():
    older = events()
    older[0] = copy.deepcopy(older[0]); older[0]['event_id'] = '01JZZZZZZZZZZZZZZZZZZZZZZZ'
    with pytest.raises(subject.ActivitySSEWireReviewError, match='CURSOR_WINDOW_INVALID'):
        checked([wire(older)])
    only_cursor = [events()[0]]
    with pytest.raises(subject.ActivitySSEWireReviewError, match='ENVELOPE_INVALID'):
        checked([wire(only_cursor)])


def test_chunk_and_size_bounds_fail_before_content_is_exposed(monkeypatch):
    with pytest.raises(subject.ActivitySSEWireReviewError, match='CHUNK_INVALID'):
        checked([b''])
    monkeypatch.setattr(subject, 'MAX_BYTES', 10)
    with pytest.raises(subject.ActivitySSEWireReviewError, match='BYTE_CAP'):
        checked([wire()])


def test_fixture_is_deterministic_aggregate_only_and_non_authoritative():
    first = subject.fixture_report()
    assert first == subject.fixture_report() and first['passed']
    assert all(first[key] for key in (
        'exact_since_id_target_bound',
        'arbitrary_chunk_boundaries_reassembled',
        'heartbeat_and_linked_events_reviewed',
        'slow_consumer_requires_reconnect',
        'internal_error_requires_reconnect',
        'output_never_authoritative'))
    encoded = json.dumps(first)
    assert all(token not in encoded for token in (
        ACCOUNT, CURSOR, '11111111', 'fixture_client',
        'SUPPLIED_ALPACA_ACTIVITY_SSE_WIRE_SESSION_REVIEW'))
    assert first['actual_storage_access'] is False
    assert all(first[flag] is False for flag in subject.FLAGS)
