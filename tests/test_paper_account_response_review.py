"""Private exact-byte account normalization review; no external access."""
import copy
import json

import pytest

from nexus import paper_account_response_review as subject
from nexus.alpaca_paper_contract import canonical,sha
from nexus.paper_read_pacing import PaperReadPacing


def test_exact_response_bytes_replay_normalization_without_writes(tmp_path):
    options=subject._fixture(tmp_path)
    before={path.name:path.read_bytes() for path in tmp_path.iterdir()}
    result=subject.review(**options)
    assert result['mode']==subject.MODE and result['status']=='REVIEW_REQUIRED'
    assert result['response_bodies']==4 and result['exact_response_bytes_match']
    assert result['normalization_replayed'] and result['portable_observation_matches']
    assert len(result['account_fields_sha256'])==64
    assert result['journal_rechecked_unchanged'] and result['admission_reserved'] is False
    assert all(result[flag] is False for flag in subject.FLAGS)
    assert result==subject.review(**options)
    assert before=={path.name:path.read_bytes() for path in tmp_path.iterdir()}


@pytest.mark.parametrize('target,code',[
    ('positions_path','RESPONSE_DIGEST_DISAGREEMENT'),
    ('observation_path','OBSERVATION_DISAGREEMENT'),
])
def test_changed_body_or_self_consistent_observation_is_refused(tmp_path,target,code):
    options=subject._fixture(tmp_path)
    if target=='positions_path':
        options[target].write_text('[]',encoding='utf-8')
    else:
        value=json.loads(options[target].read_text(encoding='utf-8'))
        value['observed_inventory']['cash']='999'
        value['reported_inventory_sha256']=sha(value['observed_inventory'])
        timing={key:float(token) if '.' in token or 'e' in token else int(token)
            for key,token in value['read_timing'].items()}
        value['observation_sha256']=sha(dict(profile='nexus.paper-account-observation.v1',
            account_fields_sha256=value['account_fields_sha256'],
            reported_inventory_sha256=value['reported_inventory_sha256'],
            position_count=value['position_count'],open_order_count=value['open_order_count'],
            source=value['source'],read_timing=timing))
        value['manifest_sha256']=sha({k:v for k,v in value.items() if k!='manifest_sha256'})
        options[target].write_text(canonical(value),encoding='utf-8')
    with pytest.raises(ValueError,match=code):subject.review(**options)


def test_stale_head_wrong_session_alias_and_unbound_session_fail_closed(tmp_path):
    options=subject._fixture(tmp_path)
    with pytest.raises(ValueError,match='HEAD_CHANGED'):
        subject.review(**dict(options,expected_head='f'*64))
    with pytest.raises(ValueError,match='SESSION_NOT_FOUND'):
        subject.review(**dict(options,session_head='f'*64))
    with pytest.raises(ValueError,match='DISTINCT_PATHS_REQUIRED'):
        subject.review(**dict(options,orders_path=options['positions_path']))
    gate=PaperReadPacing(options['pacing_path'],options['expected_account_id'])
    token=gate.begin(admission=lambda:2000)
    gate.finish(token,admission=lambda:2001,status_code=200)
    latest=gate.inspect()['head_sha256']
    with pytest.raises(ValueError,match='SESSION_SCOPE_INVALID'):
        subject.review(**dict(options,expected_head=latest,session_head=gate.operation_history()[-1]['after_head_sha256']))


def test_cli_failure_is_fixed_and_does_not_echo_private_paths(tmp_path,capsys):
    options=subject._fixture(tmp_path)
    options['positions_path'].write_text('[]',encoding='utf-8')
    import sys
    args=[]
    mapping=(('pacing','pacing_path'),('expected-account','expected_account_id'),
        ('expected-head','expected_head'),('session-head','session_head'),
        ('account-before','account_before_path'),('positions','positions_path'),
        ('orders','orders_path'),('account-after','account_after_path'),
        ('observation','observation_path'))
    for flag,key in mapping:args.extend(('--'+flag,str(options[key])))
    saved=sys.argv;sys.argv=['review',*args]
    try:assert subject.main()==2
    finally:sys.argv=saved
    output=capsys.readouterr()
    assert output.out.strip()=='PAPER_ACCOUNT_RESPONSE_REVIEW_FAILED' and output.err==''
    assert str(tmp_path) not in output.out
