"""Real authenticated encryption/KDF on disposable synthetic observations."""
import json
import os
from pathlib import Path
import stat
import sys

import pytest
from nexus import encrypted_observation as encrypted
from nexus.alpaca_paper_contract import canonical
from nexus import portable_paper_observation as portable
from test_alpaca_paper_inspection import inspect

PHRASE=' '.join(('disposable','fixture','words','only'))


def plaintext():return canonical(inspect()['portable_observation']).encode()


def test_real_scrypt_aesgcm_roundtrip_is_randomized_and_keeps_metadata_private():
    raw=plaintext();first=encrypted.seal(raw,PHRASE);second=encrypted.seal(raw,PHRASE)
    assert first!=second and first.startswith(encrypted.MAGIC)
    assert b'01234567-89ab-cdef' not in first and b'btc_total' not in first and PHRASE.encode() not in first
    assert portable.loads(encrypted.unseal(first,PHRASE))==portable.loads(raw)
    assert encrypted.MAX_BYTES>=len(first)


def test_wrong_password_and_authenticated_header_ciphertext_or_tag_changes_return_no_plaintext():
    raw=encrypted.seal(plaintext(),PHRASE)
    indexes=(len(encrypted.MAGIC),len(encrypted.MAGIC)+encrypted.SALT_BYTES,len(encrypted.MAGIC)+encrypted.SALT_BYTES+encrypted.NONCE_BYTES+1,len(raw)-1)
    altered=[raw[:index]+bytes([raw[index]^1])+raw[index+1:] for index in indexes]
    for changed in (raw,*altered):
        phrase=PHRASE+' wrong' if changed==raw else PHRASE
        with pytest.raises(ValueError,match='^ENCRYPTED_OBSERVATION_FAILED$'):encrypted.unseal(changed,phrase)


@pytest.mark.parametrize('raw',[b'',b'x'*32,b'x'*(encrypted.MAX_BYTES+1),encrypted.MAGIC+b'short'])
def test_malformed_or_oversized_envelope_fails_before_kdf(raw,monkeypatch):
    monkeypatch.setattr(encrypted,'_derive',lambda *_:pytest.fail('malformed input reached KDF'))
    with pytest.raises(ValueError,match='^ENCRYPTED_OBSERVATION_FAILED$'):encrypted.unseal(raw,PHRASE)


@pytest.mark.parametrize('phrase',['short','x'*257,None])
def test_invalid_password_policy_has_no_fallback(phrase):
    with pytest.raises(ValueError,match='^ENCRYPTED_OBSERVATION_FAILED$'):encrypted.seal(plaintext(),phrase)


def test_invalid_or_secret_bearing_plaintext_is_not_encrypted(monkeypatch):
    monkeypatch.setattr(encrypted,'_derive',lambda *_:pytest.fail('invalid observation reached KDF'))
    value=json.loads(plaintext());value['private_extra']='fixture'
    with pytest.raises(ValueError,match='^ENCRYPTED_OBSERVATION_FAILED$'):encrypted.seal(json.dumps(value).encode(),PHRASE)


def test_exclusive_new_output_preserves_existing_files_and_rejects_symlinks(tmp_path):
    existing=tmp_path/'existing';existing.write_bytes(b'original')
    with pytest.raises(FileExistsError):encrypted._write_new(existing,b'new')
    assert existing.read_bytes()==b'original'
    if os.name!='nt':
        link=tmp_path/'link';link.symlink_to(existing)
        with pytest.raises((OSError,ValueError)):encrypted._read(link,100)
        with pytest.raises(OSError):encrypted._write_new(link,b'new')
        assert existing.read_bytes()==b'original'
    output=tmp_path/'new';encrypted._write_new(output,b'private')
    if os.name!='nt':assert stat.S_IMODE(output.stat().st_mode)==0o600


def test_cli_prompts_without_password_arguments_and_failed_decryption_creates_no_output(tmp_path,monkeypatch,capsys):
    source=tmp_path/'original.json';source.write_bytes(plaintext());output=tmp_path/'copy.nexusenc'
    replies=iter([PHRASE,PHRASE]);monkeypatch.setattr(encrypted.getpass,'getpass',lambda _:next(replies))
    monkeypatch.setattr(sys,'argv',['tool','seal',str(source),'--output',str(output)])
    assert encrypted.main()==0 and capsys.readouterr().out.strip()=='ENCRYPTED_COPY_CREATED'
    decrypted=tmp_path/'decrypted.json';monkeypatch.setattr(encrypted.getpass,'getpass',lambda _:PHRASE+' wrong')
    monkeypatch.setattr(sys,'argv',['tool','unseal',str(output),'--output',str(decrypted)])
    assert encrypted.main()==2 and capsys.readouterr().out.strip()=='ENCRYPTED_OBSERVATION_FAILED' and not decrypted.exists()
    assert source.read_bytes()==plaintext()


def test_cli_password_mismatch_and_missing_parent_are_not_success(tmp_path,monkeypatch,capsys):
    source=tmp_path/'original.json';source.write_bytes(plaintext());output=tmp_path/'absent'/'copy.nexusenc'
    replies=iter([PHRASE,PHRASE+' different']);monkeypatch.setattr(encrypted.getpass,'getpass',lambda _:next(replies))
    monkeypatch.setattr(sys,'argv',['tool','seal',str(source),'--output',str(output)])
    assert encrypted.main()==2 and not output.parent.exists()
    assert capsys.readouterr().out.strip()=='ENCRYPTED_OBSERVATION_FAILED'


def test_bounded_source_read_does_not_accept_oversized_or_nonregular_files(tmp_path):
    source=tmp_path/'large';source.write_bytes(b'x'*101)
    with pytest.raises(ValueError,match='^ENCRYPTED_OBSERVATION_FAILED$'):encrypted._read(source,100)
    with pytest.raises((OSError,ValueError)):encrypted._read(tmp_path,100)


def test_cli_refuses_echoing_password_fallback(tmp_path,monkeypatch,capsys):
    import warnings
    source=tmp_path/'original.json';source.write_bytes(plaintext());output=tmp_path/'copy.nexusenc'
    def insecure(_):warnings.warn('fixture fallback',encrypted.getpass.GetPassWarning);return PHRASE
    monkeypatch.setattr(encrypted.getpass,'getpass',insecure)
    monkeypatch.setattr(sys,'argv',['tool','seal',str(source),'--output',str(output)])
    assert encrypted.main()==2 and not output.exists()
    captured=capsys.readouterr();assert captured.out.strip()=='ENCRYPTED_OBSERVATION_FAILED' and not captured.err


def test_usage_errors_never_echo_mistaken_secret_arguments(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['tool','seal','source','--password','private-argument','--output','target'])
    with pytest.raises(SystemExit) as stopped:encrypted.main()
    assert stopped.value.code==2
    capture=capsys.readouterr();assert capture.err.strip()=='ENCRYPTED_OBSERVATION_USAGE_ERROR' and 'private-argument' not in capture.out+capture.err


def test_authenticated_ciphertext_still_requires_a_valid_unprivileged_observation():
    salt=os.urandom(encrypted.SALT_BYTES);nonce=os.urandom(encrypted.NONCE_BYTES);header=encrypted.MAGIC+salt+nonce
    key=encrypted._derive(PHRASE,salt)
    forged=header+encrypted.AESGCM(key).encrypt(nonce,b'{"profile":"unsupported"}',header)
    with pytest.raises(ValueError,match='^ENCRYPTED_OBSERVATION_FAILED$'):encrypted.unseal(forged,PHRASE)
