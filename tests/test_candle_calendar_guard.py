import pytest
from nexus.historical_candles import fetch_candles


def test_impossible_calendar_window_rejected_without_transport():
    def transport(_url):
        raise AssertionError('Network transport must not be called')
    for now in (1, 1e20):
        with pytest.raises(ValueError):
            fetch_candles('BTC-USD', 60, now=now, transport=transport)


def test_oversized_injected_candle_payload_is_rejected():
    from nexus.historical_candles import parse_candles, MAX_RESPONSE
    oversized = [[900, 90, 110, 99, 100, "1" * MAX_RESPONSE]]
    with pytest.raises(ValueError):
        parse_candles("BTC-USD", 60, oversized, now=1200)


def test_early_valid_window_clamped_to_epoch():
    from urllib.parse import parse_qs, urlparse
    seen = []
    def transport(url):
        seen.append(parse_qs(urlparse(url).query))
        return [[900, 90, 110, 99, 100, 5]]
    rows = fetch_candles("BTC-USD", 60, now=1200, transport=transport)
    assert len(rows) == 1
    assert seen[0]["start"][0].startswith("1970-01-01")
