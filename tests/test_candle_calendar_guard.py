import pytest
from nexus.historical_candles import fetch_candles


def test_impossible_calendar_window_rejected_without_transport():
    def transport(_url):
        raise AssertionError('Network transport must not be called')
    for now in (1, 60, 1000, 1e20):
        with pytest.raises(ValueError):
            fetch_candles('BTC-USD', 60, now=now, transport=transport)
