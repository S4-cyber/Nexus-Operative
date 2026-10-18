"""Portable inspection content consistency, never actual credentials/providers."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from nexus import portable_paper_observation as portable
from nexus.alpaca_paper_contract import canonical, sha
from test_alpaca_paper_inspection import inspect, account, position, Opener, ACCOUNT, KEY, SECRET


def saved(): return inspect()['portable_observation']


def resign(value):
    value['reported_inventory_sha256'] = sha(value['observed_inventory'])
    value['manifest_sha256'] = sha({k:v for k,v in value.items() if k != 'manifest_sha256'})
    return value


def test_actual_read_exports_only_bounded_allowlist_and_detached_data():
    report=inspect();value=report['portable_observation']
    assert set(value)==portable.KEYS
    assert value['read_timing']==dict(started_at='1000',completed_at='1000',elapsed_seconds='0')
    assert portable.validate(value)==portable.loads(canonical(value).encode())==value
    encoded=canonical(value)
    assert KEY not in encoded and SECRET not in encoded and 'PRIVATE_DO_NOT_RETURN' not in encoded
    assert len(encoded.encode())<portable.MAX_BYTES
    assert all(flag is False for flag in value['authority'].values())
    detached=portable.validate(value);detached['observed_inventory']['cash']='0'
    assert value['observed_inventory']['cash']=='1000'
    report['private_extra']=SECRET
    assert portable.create(report)==value


@pytest.mark.parametrize('change',[
    {'profile':'future'}, {'private_extra':SECRET}, {'account_id':'private-invalid'}, {'account_id':ACCOUNT.upper()},
    {'source':'SIGNED_SOURCE'}, {'checks':'READY'}, {'position_count':True}, {'position_count':129},
    {'open_order_count':-1}, {'manifest_sha256':'f'*64}, {'account_fields_sha256':'not-a-hash'},
    {'reported_inventory_sha256':'f'*64}, {'observation_sha256':'f'*64}, {'limitations':SECRET},
    {'observation_digest_scope':'SOURCE_ATTESTED'}
],ids=['profile','extra','account','account-case','source','checks','bool-count','cap','negative-count','manifest','account-hash','inventory-hash','observation-hash','private-note','scope'])
def test_changed_or_malformed_content_is_not_certified(change):
    value=saved();value.update(change)
    with pytest.raises(ValueError,match='^PAPER_OBSERVATION_INVALID$'): portable.validate(value)


@pytest.mark.parametrize('flag',(*portable.FLAGS,'source_attested'))
def test_rehashing_promoted_authority_never_enables_it(flag):
    value=saved();value['authority'][flag]=True;resign(value)
    with pytest.raises(ValueError,match='^PAPER_OBSERVATION_INVALID$'): portable.validate(value)


@pytest.mark.parametrize('changes',[
    {'cash':'1000.00'}, {'cash':'1e3'}, {'cash':'-1'}, {'cash':'NaN'}, {'cash':1000},
    {'btc_available':'3','btc_unavailable_difference':'0'}, {'btc_unavailable_difference':'0.2'},
    {'scope_supported':False}, {'scope_supported':1}, {'private_field':SECRET}
],ids=['noncanonical','scientific-money','negative','nonfinite','numeric-money','available-exceeds','wrong-difference','unsupported-with-values','bool','private-extra'])
def test_rehashed_invalid_inventory_never_bypasses_arithmetic_or_schema(changes):
    value=saved();value['observed_inventory'].update(changes);resign(value)
    with pytest.raises(ValueError,match='^PAPER_OBSERVATION_INVALID$'): portable.validate(value)


@pytest.mark.parametrize('timing',[
    dict(started_at='1000',completed_at='999',elapsed_seconds='0'),
    dict(started_at='1000',completed_at='1020',elapsed_seconds='20'),
    dict(started_at='1000',completed_at='1003',elapsed_seconds='0'),
    dict(started_at=1000,completed_at='1000',elapsed_seconds='0'),
    dict(started_at='1000.00',completed_at='1000',elapsed_seconds='0'),
    dict(started_at='1e999',completed_at='1000',elapsed_seconds='0'),
    dict(started_at='1000',completed_at='1000',elapsed_seconds='0',private=SECRET),
],ids=['regression','budget','continuity','numeric','spelling','overflow','extra'])
def test_invalid_timing_is_not_a_portable_identity(timing):
    value=saved();value['read_timing']=timing;resign(value)
    with pytest.raises(ValueError,match='^PAPER_OBSERVATION_INVALID$'): portable.validate(value)


@pytest.mark.parametrize('raw',[
    b'{"profile":0,"profile":1}', b'NaN', b'null', b'[]', b'\xff', b'x'*16385,
    b'['*1000+b']'*1000,
],ids=['duplicate','nonfinite','null','array','utf8','cap','depth'])
def test_file_loader_is_bounded_duplicate_free_and_sanitized(raw):
    with pytest.raises(ValueError,match='^PAPER_OBSERVATION_INVALID$'): portable.loads(raw)


def test_self_consistent_rewrite_does_not_become_source_authentication_or_freshness():
    value=saved();value['account_id']='11234567-89ab-cdef-0123-456789abcdef';resign(value)
    checked=portable.validate(value)
    assert not checked['authority']['source_attested']
    assert not checked['authority']['ready_for_routing']
    assert 'not source authenticity' in checked['limitations']
    # The raw normalized account body is intentionally absent. Its digest is an
    # opaque reference, not independently verified account identity or signature.
    assert 'account' not in checked


@pytest.mark.parametrize('rows',[[position(qty_available=None)],[position(symbol='ETHUSD')],[]])
def test_unknown_unsupported_and_true_zero_survive_export(rows):
    value=inspect(Opener([account(),rows,[],account()]))['portable_observation']
    assert portable.loads(canonical(value).encode())==value
    if rows and rows[0]['symbol']=='ETHUSD':assert value['observed_inventory']['btc_total'] is None
    elif not rows:assert value['observed_inventory']['btc_total']=='0'
    else:assert value['observed_inventory']['btc_available'] is None


@pytest.mark.parametrize('elapsed',[1e-07,0.00010000000000000002,0.0,-0.0,5e-324])
def test_real_javascript_json_roundtrip_preserves_integral_float_and_scientific_timing(elapsed):
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual JavaScript roundtrip')
    # Original JSON numeric timing cannot be hashed identically after JS drops
    # .0 or the leading zero in e-07. Portable spelling must remain a string.
    report=inspect();report['read_timing']=dict(started_at=1000.0,completed_at=1000.0,elapsed_seconds=elapsed)
    report['observation_sha256']=sha(dict(profile='nexus.paper-account-observation.v1',**{k:report[k] for k in
        ('account_fields_sha256','reported_inventory_sha256','position_count','open_order_count','source','read_timing')}))
    value=portable.create(report)
    result=subprocess.run([node,'-e','process.stdout.write(JSON.stringify(JSON.parse(require("fs").readFileSync(0,"utf8"))))'],input=canonical(value),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert portable.loads(result.stdout.encode())==value


def test_cli_validation_is_offline_bounded_and_does_not_echo_account_or_balances(tmp_path):
    path=tmp_path/'private.json';path.write_text(canonical(saved()))
    env={**os.environ,'NEXUS_PORTABLE_DATA':str(tmp_path/'app')}
    command=[sys.executable,'-m','nexus.portable_paper_observation',str(path)]
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    summary=json.loads(result.stdout)
    assert summary['status']=='CONTENT_CONSISTENCY_CHECKED_SOURCE_UNVERIFIED'
    assert not summary['source_attested'] and not summary['ready_for_routing']
    assert ACCOUNT not in result.stdout and '1000' not in result.stdout and not (tmp_path/'app').exists()
    path.write_bytes(b'x'*16385)
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and result.stdout.strip()=='PAPER_OBSERVATION_INVALID' and not result.stderr
    path.unlink()
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and str(path) not in result.stdout and not result.stderr


def test_actual_portal_controller_download_validates_with_offline_reader():
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual download controller')
    script=Path(__file__).resolve().parents[1]/'tools/paper_credentials_smoke.cjs'
    result=subprocess.run([node,str(script),'--export-fixture'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    value=portable.loads(result.stdout.encode())
    assert value['observed_inventory']['btc_total']=='2'
    assert not value['authority']['source_attested']


def test_intercepted_browser_fixture_uses_float_safe_portable_content():
    from tools.portal_browser_smoke import paper_observation_fixture
    report=paper_observation_fixture()
    assert report['portable_observation']['read_timing']['started_at']=='1000.0'
    assert portable.validate(report['portable_observation'])==portable.create(report)
