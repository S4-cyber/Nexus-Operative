"""Encrypted input CLI exercises real crypto and retained replay, no provider IO."""
import json
import sys
import warnings

import pytest
from nexus import encrypted_observation as envelope
from nexus import paper_observation_review as review
from nexus.alpaca_paper_contract import canonical
from test_paper_observation_review import observations
from test_retained_balance_review import target, ACCOUNT, OTHER

PHRASES=('disposable opening fixture passphrase','different closing fixture passphrase')


@pytest.fixture
def inputs(tmp_path):
    store=target(tmp_path);values=observations();paths=[]
    for i,value in enumerate(values):
        plain=tmp_path/f'{i}.json';plain.write_bytes(canonical(value).encode())
        sealed=tmp_path/f'{i}.nexusenc';sealed.write_bytes(envelope.seal(plain.read_bytes(),PHRASES[i]))
        paths.append((plain,sealed))
    return store,values,paths


def invoke(monkeypatch,store,paths,encrypted=(True,True),extra=()):
    args=['tool','--activities',str(store.path),'--account',ACCOUNT,'--head',store.inspect()['head_sha256']]
    for i,label in enumerate(('opening','closing')):
        args.extend(['--'+label,str(paths[i][int(encrypted[i])])])
        if encrypted[i]:args.append('--'+label+'-encrypted')
    monkeypatch.setattr(sys,'argv',args+list(extra))
    return review.main()


@pytest.mark.parametrize('encrypted',[(True,True),(True,False),(False,True),(False,False)])
def test_mixed_explicit_inputs_match_plain_review_without_decrypted_files(inputs,monkeypatch,capsys,encrypted):
    store,values,paths=inputs;before={p:p.read_bytes() for p in store.path.parent.iterdir()};prompts=[]
    def prompt(label):
        prompts.append(label)
        return PHRASES[0 if label.startswith('Opening') else 1]
    monkeypatch.setattr(envelope.getpass,'getpass',prompt)
    expected=review.review(store,expected_account_id=ACCOUNT,expected_head=store.inspect()['head_sha256'],
        opening_observation=values[0],closing_observation=values[1])
    assert invoke(monkeypatch,store,paths,encrypted)==0
    capture=capsys.readouterr();assert not capture.err
    assert json.loads(capture.out)==expected
    assert len(prompts)==sum(encrypted)
    assert {p:p.read_bytes() for p in store.path.parent.iterdir()}==before
    assert not any(phrase in capture.out for phrase in PHRASES)


@pytest.mark.parametrize('failure',['wrong','tamper','echo','eof','interrupt','mode','account','head'])
def test_failures_print_no_partial_report_or_secret_and_preserve_all_files(inputs,monkeypatch,capsys,failure):
    store,values,paths=inputs
    if failure=='tamper':
        raw=paths[1][1].read_bytes();paths[1][1].write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    if failure=='mode':paths[1][1].write_bytes(paths[1][0].read_bytes())
    if failure=='account':
        changed=dict(values[1],account_id=OTHER)
        from test_paper_observation_review import rehash
        paths[1][1].write_bytes(envelope.seal(canonical(rehash(changed)).encode(),PHRASES[1]))
    def prompt(label):
        if label.startswith('Closing'):
            if failure=='wrong':return 'disposable wrong private phrase'
            if failure=='echo':warnings.warn('private fallback message',envelope.getpass.GetPassWarning)
            if failure=='eof':raise EOFError('private error')
            if failure=='interrupt':raise KeyboardInterrupt('private error')
        return PHRASES[0 if label.startswith('Opening') else 1]
    monkeypatch.setattr(envelope.getpass,'getpass',prompt)
    before={p:p.read_bytes() for p in store.path.parent.iterdir()}
    assert invoke(monkeypatch,store,paths,extra=('--head','0'*64) if failure=='head' else ())==2
    capture=capsys.readouterr();assert capture.out.strip()=='PAPER_OBSERVATION_REVIEW_FAILED' and not capture.err
    assert {p:p.read_bytes() for p in store.path.parent.iterdir()}==before


def test_plaintext_mode_never_prompts_or_auto_detects_encryption(inputs,monkeypatch):
    _,_,paths=inputs
    monkeypatch.setattr(envelope.getpass,'getpass',lambda _:pytest.fail('implicit password prompt'))
    with pytest.raises(ValueError):review._load_file(paths[0][1])


def test_usage_error_does_not_echo_mistaken_passphrase(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['tool','--password','private-mistaken-passphrase'])
    with pytest.raises(SystemExit) as stopped:review.main()
    assert stopped.value.code==2
    capture=capsys.readouterr();assert capture.err.strip()=='PAPER_OBSERVATION_REVIEW_USAGE_ERROR' and not capture.out
