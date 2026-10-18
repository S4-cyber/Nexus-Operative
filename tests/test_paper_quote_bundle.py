"""Encrypted portable full-chain evidence, never source or routing acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from nexus import paper_quote_bundle as subject,paper_quote_capture as capture,encrypted_observation as crypto
from nexus.alpaca_paper_contract import canonical,sha

PHRASE=' '.join(('temporary','fixture','words','only'))


@pytest.fixture
def evidence(tmp_path):
    options=subject._fixture(tmp_path/'private')
    return options,subject.snapshot(**options)


def redigest(value):value['bundle_sha256']=sha({k:v for k,v in value.items() if k!='bundle_sha256'})


def files(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_export_replays_every_chain_and_older_original_prefix_without_mutation(evidence,tmp_path):
    options,raw=evidence;before=files(tmp_path);value=subject.loads(raw);result=subject.validate(value)
    body=json.loads(value['quote_records'][1][1])
    assert body['request_head_sha256']!=value['heads']['request']
    assert result['retained_requests']==result['retained_quotes']==result['retained_pacing_sessions']==1
    assert result['full_request_history_replayed'] and result['full_quote_history_replayed'] and result['full_pacing_history_replayed']
    assert all(result[k] is False for k in subject.FLAGS) and files(tmp_path)==before
    assert str(tmp_path) not in raw.decode() and not any(x in raw.decode() for x in ('fixture_key_only_123','fixture_secret_only_123'))


def test_real_crypto_randomized_and_password_authenticated(evidence):
    _,raw=evidence;a=subject.seal(raw,PHRASE);b=subject.seal(raw,PHRASE)
    assert a!=b and subject.unseal(a,PHRASE)==raw
    assert subject.MAGIC in a and subject.PROFILE.encode() not in a
    with pytest.raises(ValueError):subject.unseal(a,PHRASE+' wrong')
    for index in (len(subject.MAGIC),subject.HEADER_BYTES-1,len(a)-1):
        changed=a[:index]+bytes([a[index]^1])+a[index+1:]
        with pytest.raises(ValueError):subject.unseal(changed,PHRASE)


def test_format_is_separate_from_inventory_encryption(evidence,monkeypatch):
    _,raw=evidence;encrypted=subject.seal(raw,PHRASE)
    with pytest.raises(ValueError):crypto.unseal(encrypted,PHRASE)
    monkeypatch.setattr(crypto,'_derive',lambda *_:pytest.fail('invalid envelope reached KDF'))
    for malformed in (b'',crypto.MAGIC+b'wrong-profile',subject.MAGIC+b'short',b'x'*(subject.MAX_ENCRYPTED_BYTES+1)):
        with pytest.raises(ValueError):subject.unseal(malformed,PHRASE)


@pytest.mark.parametrize('part',('request_events','quote_records','pacing_events'))
def test_modified_full_tail_refuses_even_with_new_bundle_digest(evidence,part):
    _,raw=evidence;value=subject.loads(raw);value[part][-1][-1]='f'*64;redigest(value)
    with pytest.raises(ValueError):subject.validate(value)


@pytest.mark.parametrize('change',('account','head','scope','extra','row_bool','row_type','duplicate_seq','truncated','row_cap','encoded_extra'))
def test_malformed_or_mixed_evidence_is_never_promoted(evidence,change):
    _,raw=evidence;v=subject.loads(raw)
    if change=='account':v['account_id']='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    if change=='head':v['heads']['quote']='f'*64
    if change=='scope':v['pacing_scope_sha256']='f'*64
    if change=='extra':v['source_attested']=True
    if change=='row_bool':v['request_events'][0][0]=True
    if change=='row_type':v['quote_records'][0][1]={}
    if change=='duplicate_seq':v['pacing_events'][-1][0]=v['pacing_events'][0][0]
    if change=='truncated':v['request_events']=v['request_events'][:1]
    if change=='row_cap':v['quote_records'][0][1]='x'*(subject.quotes.MAX_RECORD_BYTES+1)
    if change=='encoded_extra':v['quote_records'][1][1]='{"source_attested":true}'
    redigest(v)
    with pytest.raises((ValueError,sqlite3.Error)):subject.validate(v)


def test_unknown_fields_do_not_get_encrypted(evidence,monkeypatch):
    _,raw=evidence;v=subject.loads(raw);v['secret_extra']='PRIVATE';redigest(v)
    monkeypatch.setattr(crypto,'_derive',lambda *_:pytest.fail('invalid data reached KDF'))
    with pytest.raises(ValueError):subject.seal(canonical(v).encode(),PHRASE)


def test_canonical_duplicate_and_nonfinite_json_refused(evidence):
    _,raw=evidence
    for changed in (raw[:-1]+b',"profile":"other"}',raw[:-1]+b',"x":NaN}',json.dumps(json.loads(raw),indent=2).encode()):
        with pytest.raises(ValueError):subject.loads(changed)


def test_export_changed_heads_refuses_without_writing(evidence,tmp_path):
    options,_=evidence;before=files(tmp_path)
    for key in ('expected_request_head','expected_quote_head','expected_pacing_head'):
        with pytest.raises(ValueError):subject.snapshot(**dict(options,**{key:'f'*64}))
    assert files(tmp_path)==before


def test_export_detects_original_change_during_collection(evidence,monkeypatch,tmp_path):
    options,_=evidence;original=capture.status;calls=[]
    def changed(**kw):
        result=original(**kw);calls.append(1)
        if len(calls)==1:
            from nexus.alpaca_paper_contract import preview
            from nexus.broker_request_journal import BrokerRequestJournal
            j=BrokerRequestJournal(options['request_journal_path'],options['expected_account_id'])
            # A new original plan changes the current tail while the earlier
            # quote prefix remains valid; export must still refuse stale heads.
            asset=dict(symbol='BTC/USD',**{'class':'crypto'},status='active',tradable=True,fractionable=True,min_order_size='.5',min_trade_increment='.5',price_increment='.5')
            j.prepare(preview('nx_later','BTC-USD','BUY','1','100',asset,metadata_at=1000,evaluated_at=1001))
        return result
    monkeypatch.setattr(capture,'status',changed)
    with pytest.raises(ValueError):subject.snapshot(**options)
    assert not list(tmp_path.glob('*.nexusenc'))


def test_review_replays_only_memory_databases_without_files_network_or_vault(evidence,tmp_path):
    _,raw=evidence;source=tmp_path/'fixture-plaintext.json';source.write_bytes(raw);before=files(tmp_path)
    script='''import json,socket,ssl,sys,sqlite3
from pathlib import Path
raw=Path(sys.argv[1]).read_bytes()
from nexus import paper_quote_bundle as subject,paper_credentials
paper_credentials._capture=lambda:(_ for _ in ()).throw(AssertionError('VAULT'))
socket.socket=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK'))
original=sqlite3.connect
def memory_only(database,*a,**k):
 assert database==':memory:'
 return original(database,*a,**k)
sqlite3.connect=memory_only
r=subject.public_summary(subject.validate(subject.loads(raw)));print(json.dumps(r))'''
    child=subprocess.run([sys.executable,'-c',script,str(source)],capture_output=True,text=True,timeout=30)
    assert child.returncode==0 and json.loads(child.stdout)['local_capture_history_agreement'] and files(tmp_path)==before


def cli_args(options,output):
    result=['tool','seal']
    for flag,key in (('expected-account','expected_account_id'),('request-journal','request_journal_path'),('quote-store','quote_store_path'),('pacing','pacing_path'),('request-head','expected_request_head'),('quote-head','expected_quote_head'),('pacing-head','expected_pacing_head')):
        result+=['--'+flag,str(options[key])]
    return [*result,'--output',str(output)]


def test_cli_seal_and_in_memory_review_never_write_plaintext(evidence,tmp_path,monkeypatch,capsys):
    options,raw=evidence;output=tmp_path/'copy.nexusenc';before=files(tmp_path)
    monkeypatch.setattr(crypto,'prompt_passphrase',lambda *a:PHRASE)
    monkeypatch.setattr(sys,'argv',cli_args(options,output));assert subject.main()==0
    assert capsys.readouterr().out=='ENCRYPTED_QUOTE_EVIDENCE_COPY_CREATED\n'
    assert subject.unseal(output.read_bytes(),PHRASE)==raw
    sealed=files(tmp_path)
    monkeypatch.setattr(sys,'argv',['tool','review','--source',str(output),'--public-summary']);assert subject.main()==0
    r=json.loads(capsys.readouterr().out);assert r['mode']=='PUBLIC_QUOTE_BUNDLE_CONTENT_DIAGNOSTIC'
    assert files(tmp_path)==sealed and set(sealed)-set(before)=={'copy.nexusenc'}
    assert all(private not in json.dumps(r) for private in (options['expected_account_id'],options['expected_request_head'],str(tmp_path),'1000','nx_quote_fixture'))


def test_existing_output_refuses_before_password_prompt(evidence,tmp_path,monkeypatch,capsys):
    options,_=evidence;output=tmp_path/'existing';output.write_bytes(b'RETAIN');before=files(tmp_path)
    monkeypatch.setattr(crypto,'prompt_passphrase',lambda *a:pytest.fail('existing output prompted'))
    monkeypatch.setattr(sys,'argv',cli_args(options,output));assert subject.main()==2
    assert capsys.readouterr().out=='PAPER_QUOTE_BUNDLE_FAILED\n' and files(tmp_path)==before


def test_changed_source_after_new_output_loses_ack_but_preserves_copy(evidence,tmp_path,monkeypatch,capsys):
    options,_=evidence;output=tmp_path/'copy.nexusenc';original=crypto._write_new
    def changed(path,raw):
        original(path,raw)
        from nexus.paper_read_pacing import PaperReadPacing
        gate=PaperReadPacing(options['pacing_path'],options['expected_account_id'])
        gate.begin(admission=lambda:1792195261)
    monkeypatch.setattr(crypto,'_write_new',changed);monkeypatch.setattr(crypto,'prompt_passphrase',lambda *a:PHRASE)
    monkeypatch.setattr(sys,'argv',cli_args(options,output));assert subject.main()==2
    assert capsys.readouterr().out=='PAPER_QUOTE_BUNDLE_FAILED\n' and output.exists()
    assert subject.validate(subject.loads(subject.unseal(output.read_bytes(),PHRASE)))['local_capture_history_agreement']


@pytest.mark.parametrize('field',subject.FLAGS)
def test_public_summary_never_promotes_authority(evidence,field):
    _,raw=evidence;r=subject.validate(subject.loads(raw));r[field]=True
    with pytest.raises(ValueError):subject.public_summary(r)


@pytest.mark.parametrize('value',('PRIVATE_TEXT',True,-1,999999))
def test_public_counts_are_strict_bounded_integers(evidence,value):
    _,raw=evidence;r=subject.validate(subject.loads(raw));r['retained_quotes']=value
    with pytest.raises(ValueError):subject.public_summary(r)


@pytest.mark.parametrize('suffix',('-wal','-shm','-journal','.owner.lock'))
def test_export_never_creates_a_source_journal_sidecar(evidence,monkeypatch,capsys,suffix):
    options,_=evidence;output=Path(str(options['request_journal_path'])+suffix)
    monkeypatch.setattr(crypto,'prompt_passphrase',lambda *a:pytest.fail('reserved sidecar prompted'))
    monkeypatch.setattr(sys,'argv',cli_args(options,output));assert subject.main()==2
    assert capsys.readouterr().out=='PAPER_QUOTE_BUNDLE_FAILED\n' and not output.exists()


def test_echoing_password_fallback_is_a_fixed_failure_without_output(evidence,tmp_path,monkeypatch,capsys):
    options,_=evidence;output=tmp_path/'copy.nexusenc'
    def echoing(*a):raise crypto.getpass.GetPassWarning('PRIVATE_TEXT')
    monkeypatch.setattr(crypto,'prompt_passphrase',echoing);monkeypatch.setattr(sys,'argv',cli_args(options,output))
    assert subject.main()==2 and capsys.readouterr().out=='PAPER_QUOTE_BUNDLE_FAILED\n' and not output.exists()


def test_password_mismatch_creates_no_output(evidence,tmp_path,monkeypatch,capsys):
    options,_=evidence;output=tmp_path/'copy.nexusenc';phrases=iter([PHRASE,PHRASE+' changed'])
    monkeypatch.setattr(crypto,'prompt_passphrase',lambda *a:next(phrases));monkeypatch.setattr(sys,'argv',cli_args(options,output))
    assert subject.main()==2 and capsys.readouterr().out=='PAPER_QUOTE_BUNDLE_FAILED\n' and not output.exists()


def test_bundle_cap_refuses_before_key_derivation(evidence,monkeypatch):
    _,raw=evidence;monkeypatch.setattr(subject,'MAX_PLAINTEXT_BYTES',len(raw)-1)
    monkeypatch.setattr(crypto,'_derive',lambda *a:pytest.fail('oversized payload reached KDF'))
    with pytest.raises(ValueError):subject.seal(raw,PHRASE)


def test_bundle_fixture_only_returns_aggregate_checks():
    r=subject.fixture_report()
    assert r['passed'] and all(r[k] is True for k in subject.CHECKS)
    assert all(type(v) is bool or k in ('source','cipher','kdf') for k,v in r.items())


@pytest.mark.parametrize('field,value,code',[
    ('full_three_journal_replay',False,'BTC_QUOTE_BUNDLE'),
    ('bundle_source_attested',True,'BTC_QUOTE_BUNDLE_AUTHORITY'),
    ('ready_for_routing',True,'BTC_QUOTE_BUNDLE_AUTHORITY')])
def test_required_release_bundle_checks_cannot_be_promoted(field,value,code):
    from nexus import release_trials,paper_quote_observation,paper_quote_store
    r=dict(passed=True,btc_quote_observation=paper_quote_observation.fixture_report(),btc_quote_retention=paper_quote_store.fixture_report(),
        btc_quote_capture=capture.fixture_report(),btc_quote_bundle=subject.fixture_report())
    r['btc_quote_bundle'][field]=value
    with pytest.raises(ValueError,match='RELEASE_TRIAL_'+code):release_trials._validate('inspection_inventory',r)


def test_authenticated_ciphertext_still_requires_valid_journal_replay(evidence):
    _,raw=evidence;value=subject.loads(raw);value['quote_records'][-1][-1]='f'*64;redigest(value)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os
    salt=os.urandom(crypto.SALT_BYTES);nonce=os.urandom(crypto.NONCE_BYTES);header=subject.MAGIC+salt+nonce
    encrypted=header+AESGCM(crypto._derive(PHRASE,salt)).encrypt(nonce,canonical(value).encode(),header)
    with pytest.raises(ValueError):subject.unseal(encrypted,PHRASE)


def test_malformed_review_file_refuses_before_password_prompt(tmp_path,monkeypatch,capsys):
    source=tmp_path/'wrong-file';source.write_bytes(b'PRIVATE_NON_ENCRYPTED')
    monkeypatch.setattr(crypto,'prompt_passphrase',lambda *a:pytest.fail('malformed input prompted'))
    monkeypatch.setattr(sys,'argv',['tool','review','--source',str(source)])
    assert subject.main()==2 and capsys.readouterr().out=='PAPER_QUOTE_BUNDLE_FAILED\n'


def test_review_detects_encrypted_input_replacement(evidence,tmp_path,monkeypatch,capsys):
    _,raw=evidence;source=tmp_path/'copy.nexusenc';source.write_bytes(subject.seal(raw,PHRASE))
    def replaced(*a):source.write_bytes(b'REPLACED_PRIVATE_INPUT');return PHRASE
    monkeypatch.setattr(crypto,'prompt_passphrase',replaced);monkeypatch.setattr(sys,'argv',['tool','review','--source',str(source)])
    assert subject.main()==2 and capsys.readouterr().out=='PAPER_QUOTE_BUNDLE_FAILED\n'


def test_protected_export_path_refuses_before_prompt(evidence,monkeypatch,capsys):
    options,_=evidence;inside=crypto.ROOT/'PRIVATE_QUOTE_EVIDENCE_FIXTURE.nexusenc'
    monkeypatch.setattr(crypto,'prompt_passphrase',lambda *a:pytest.fail('protected output prompted'))
    monkeypatch.setattr(sys,'argv',cli_args(options,inside))
    assert subject.main()==2 and capsys.readouterr().out=='PAPER_QUOTE_BUNDLE_FAILED\n' and not inside.exists()


@pytest.mark.parametrize('phrase',('short',None,'x'*257))
def test_password_policy_has_no_fallback(evidence,phrase):
    _,raw=evidence
    with pytest.raises(ValueError):subject.seal(raw,phrase)
