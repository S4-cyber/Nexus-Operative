from pathlib import Path
import pytest
from tools.installed_smoke import launch_environment


def test_default_layout_clears_overrides_without_mutating_parent(tmp_path):
    inherited={key:'external' for key in ('NEXUS_PORTABLE_DATA','NEXUS_AUDIT_PATH','NEXUS_RESERVE_PATH','NEXUS_STRATEGY_MEMORY_PATH')}
    inherited['LOCALAPPDATA']='original';inherited['KEEP']='value';before=dict(inherited)
    child,expected=launch_environment(default_data_root=tmp_path,environ=inherited)
    assert inherited==before and child['KEEP']=='value'
    assert child['LOCALAPPDATA']==str(tmp_path.resolve())
    assert expected==tmp_path.resolve()/'NexusOperative'/'storage'
    assert all(key not in child for key in before if key.startswith('NEXUS_'))
    assert not expected.exists()


def test_portable_layout_isolates_auxiliary_databases(tmp_path):
    inherited={'NEXUS_AUDIT_PATH':'external','NEXUS_PORTABLE_DATA':'original'}
    child,expected=launch_environment(data=tmp_path,environ=inherited)
    assert child['NEXUS_PORTABLE_DATA']==str(tmp_path.resolve())
    assert 'NEXUS_AUDIT_PATH' not in child
    assert inherited['NEXUS_AUDIT_PATH']=='external'
    assert expected==tmp_path.resolve()


@pytest.mark.parametrize('both',[False,True])
def test_layout_selection_is_unambiguous(tmp_path,both):
    with pytest.raises(ValueError,match='CHOOSE_ONE_DATA_LAYOUT'):
        launch_environment(data=tmp_path if both else None,default_data_root=tmp_path if both else None)
