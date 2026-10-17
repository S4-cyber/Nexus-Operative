import pytest
from nexus.durable_benchmark import benchmark,percentiles


def test_bounded_benchmark_uses_disposable_accounts(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    result=benchmark(2)
    assert result['application_accounts_changed'] is False
    assert result['target_validated'] is False
    assert list(tmp_path.iterdir())==[]
    assert len(result['treatments'])==4
    for treatment in result['treatments']:
        assert treatment['accounting_consistent']
        assert treatment['durable_commands']==16
        assert treatment['broker_orders']==0
    assert sum(t['portfolio_linked'] for t in result['treatments'])==2
    assert sum(t['tier_checks_included'] for t in result['treatments'])==2
    assert all(t['commit_return_latency']['p99_ms']>=t['commit_return_latency']['p50_ms'] for t in result['treatments'])


def test_nearest_rank_percentiles_and_input_bounds():
    assert percentiles([1,2,3,4,100])==dict(p50_ms=3,p95_ms=100,p99_ms=100)
    for value in (True,0,31,'16'):
        with pytest.raises(ValueError):benchmark(value)
