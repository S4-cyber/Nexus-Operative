import json
import subprocess
import sys
import pytest
from nexus.artifact_manifest import manifest, verify


def test_manifest_deterministic_and_detects_changed_distribution(tmp_path):
    a=tmp_path/'Nexus.exe';b=tmp_path/'Setup.exe'
    a.write_bytes(b'packaged application');b.write_bytes(b'installer')
    result=manifest([b,a],'0.62.0','a'*40)
    assert result==manifest([a,b],'0.62.0','a'*40)
    assert verify(result,tmp_path)['artifact_integrity_ok']
    assert not result['signed'] and not result['release_approved']
    b.write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='DIGEST_DISAGREEMENT'):verify(result,tmp_path)


@pytest.mark.parametrize('name',['../private','..','/absolute','folder/file','folder\\file','C:private'])
def test_manifest_rejects_paths(tmp_path,name):
    with pytest.raises(ValueError,match='INVALID_ARTIFACT_NAME'):
        verify(dict(schema='nexus.artifacts.v1',artifacts=[dict(name=name)]),tmp_path)


def test_duplicate_names_rejected(tmp_path):
    p=tmp_path/'a.exe';p.write_bytes(b'a')
    with pytest.raises(ValueError,match='UNIQUE'):manifest([p,p],'0.62.0','a'*40)
    doc=manifest([p],'0.62.0','a'*40);doc['artifacts']*=2
    with pytest.raises(ValueError,match='INVALID_ARTIFACT_NAME'):verify(doc,tmp_path)


def test_cli_create_verify_missing_file(tmp_path):
    binary=tmp_path/'a.exe';binary.write_bytes(b'a')
    output=tmp_path/'manifest.json'
    cmd=[sys.executable,'-m','nexus.artifact_manifest']
    subprocess.run(cmd+['create','--version','0.62.0','--commit','a'*40,'--output',str(output),str(binary)],check=True)
    assert json.loads(output.read_text())['source_commit']=='a'*40
    subprocess.run(cmd+['verify',str(output),str(tmp_path)],check=True,capture_output=True)
    binary.unlink()
    assert subprocess.run(cmd+['verify',str(output),str(tmp_path)],capture_output=True).returncode==1
