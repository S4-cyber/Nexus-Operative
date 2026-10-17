from concurrent.futures import ThreadPoolExecutor
import pytest
from nexus.state import isolated_settings,db_path,load_settings,save_settings
from nexus.tier_permissions import configure,current


def test_diagnostic_settings_do_not_change_user_permissions(tmp_path,monkeypatch):
    real=tmp_path/'real';fixture=tmp_path/'fixture'
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(real))
    configure('solar',True)
    before=current()
    original=load_settings()
    with isolated_settings(fixture):
        assert db_path().parent==fixture
        configure('terrestrial',True)
        save_settings({**original,'paper_budget':5000})
        assert not next(t for t in current()['tiers'] if t['tier']=='solar')['enabled']
    assert current()==before
    assert load_settings()==original


def test_scopes_are_thread_local_and_reset_on_error(tmp_path,monkeypatch):
    real=tmp_path/'real';fixture=tmp_path/'fixture'
    monkeypatch.setenv('NEXUS_PORTABLE_DATA',str(real))
    with pytest.raises(RuntimeError):
        with isolated_settings(fixture):
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(lambda:db_path().parent).result()==real
            raise RuntimeError('test')
    assert db_path().parent==real
