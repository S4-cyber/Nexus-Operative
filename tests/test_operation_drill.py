import pytest
from nexus.operation_drill import drill


def test_grouped_paper_operations_preserve_history_and_isolation(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(tmp_path))
    result=drill(3)
    assert result['passed']
    assert result['buy_fills']==result['sell_fills']==result['hold_checks']==3
    assert result['tier_vetoes']==result['halted_restart_checks']==3
    assert result['archive_batches']==3
    assert result['archived_orders']==6
    assert result['portfolio_reconciled']
    assert result['old_id_deduplicated']
    assert not result['application_accounts_changed']
    assert not result['history_deleted']
    assert list(tmp_path.iterdir())==[]


def test_drill_input_bounds():
    for value in (True,0,25,'12'):
        with pytest.raises(ValueError):drill(value)
