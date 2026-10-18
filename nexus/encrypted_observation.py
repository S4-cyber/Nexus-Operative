"""Opt-in password-encrypted copies of portable paper observations.

No saved password/key, provider access, automatic import or trading authority.
Original downloads/local databases remain plaintext; keep them private too.
"""
import argparse
import getpass
import os
from pathlib import Path
import stat
import warnings

from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from nexus import portable_paper_observation as portable
from nexus.alpaca_paper_contract import canonical

MAGIC=b'NEXUS-OBS-ENC-1\n'
SALT_BYTES=16
NONCE_BYTES=12
MAX_BYTES=len(MAGIC)+SALT_BYTES+NONCE_BYTES+portable.MAX_BYTES+16


def _fail():raise ValueError('ENCRYPTED_OBSERVATION_FAILED') from None


def _derive(passphrase,salt):
    if not isinstance(passphrase,str) or not 14<=len(passphrase)<=256:_fail()
    encoded=passphrase.encode('utf-8')
    if len(encoded)>1024:_fail()
    # Fixed cost; untrusted files cannot request larger memory/CPU or downgrade.
    return Scrypt(salt=salt,length=32,n=2**17,r=8,p=1).derive(encoded)


def seal(raw,passphrase):
    """Validate first; encrypt canonical data with authenticated header and salt."""
    try:
        value=portable.loads(raw);plaintext=canonical(value).encode('utf-8')
        salt=os.urandom(SALT_BYTES);nonce=os.urandom(NONCE_BYTES);header=MAGIC+salt+nonce
        return header+AESGCM(_derive(passphrase,salt)).encrypt(nonce,plaintext,header)
    except (ValueError,TypeError,UnicodeError,MemoryError,UnsupportedAlgorithm,OSError):_fail()


def unseal(raw,passphrase):
    """Authenticate before returning any plaintext; validate the inner profile too."""
    try:
        header_size=len(MAGIC)+SALT_BYTES+NONCE_BYTES
        if type(raw) is not bytes or not header_size+16<=len(raw)<=MAX_BYTES or not raw.startswith(MAGIC):_fail()
        header=raw[:header_size];salt=header[len(MAGIC):len(MAGIC)+SALT_BYTES];nonce=header[-NONCE_BYTES:]
        plaintext=AESGCM(_derive(passphrase,salt)).decrypt(nonce,raw[header_size:],header)
        return canonical(portable.loads(plaintext)).encode('utf-8')
    except (InvalidTag,ValueError,TypeError,UnicodeError,MemoryError,UnsupportedAlgorithm,OSError):_fail()


def fixture_report():
    from nexus.paper_observation_review import _fixture_observation
    value=_fixture_observation('01234567-89ab-cdef-0123-456789abcdef','1000','0',1000,1001)
    raw=canonical(value).encode();phrase=os.urandom(32).hex()
    first,second=seal(raw,phrase),seal(raw,phrase)
    rejected=False
    try:unseal(first,phrase+' different')
    except ValueError:rejected=True
    return dict(passed=unseal(first,phrase)==raw and first!=second and rejected,
        roundtrip_matches=unseal(first,phrase)==raw,ciphertext_randomized=first!=second,wrong_password_rejected=rejected,
        kdf='SCRYPT_N131072_R8_P1',cipher='AES256GCM_RANDOM_NONCE12_SALT16',
        password_saved=False,vault_accessed=False,external_connections=False,original_plaintext_deleted=False,
        output_file_writes=False,ready_for_routing=False,live_orders_enabled=False,
        source='SYNTHETIC_OBSERVATION_REAL_AUTHENTICATED_ENCRYPTION',
        limitations='In-memory synthetic observation only. No key/password/ciphertext retained in this report. Existing downloads/databases stay plaintext. No OS ACL/disk-encryption/native-profile acceptance or trading permission.')


def _read(path,cap):
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
    if Path(path).is_symlink():_fail()
    with os.fdopen(os.open(path,flags),'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>cap:_fail()
        raw=stream.read(cap+1);after=os.fstat(stream.fileno())
        if len(raw)>cap or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):_fail()
    return raw


def _write_new(path,raw):
    # Exclusive creation rejects existing files/symlinks. POSIX mode is private;
    # Windows inherits the selected directory's ACL. Never claim universal ACLs.
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
    with os.fdopen(os.open(path,flags,0o600),'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    # Failed IO may leave a partial NEW output; no existing path is deleted or
    # overwritten, no success acknowledged. A password failure creates no output.


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'ENCRYPTED_OBSERVATION_USAGE_ERROR\n')


def prompt_passphrase(label='Passphrase (not stored): '):
    """Hidden terminal input only; refuse getpass's echoing fallback."""
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        return getpass.getpass(label)


def main():
    parser=_Parser(description=__doc__)
    parser.add_argument('action',choices=('seal','unseal'));parser.add_argument('source');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        raw=_read(args.source,portable.MAX_BYTES if args.action=='seal' else MAX_BYTES)
        phrase=prompt_passphrase()
        if args.action=='seal' and phrase!=prompt_passphrase('Repeat passphrase: '):_fail()
        payload=seal(raw,phrase) if args.action=='seal' else unseal(raw,phrase)+b'\n'
        _write_new(args.output,payload)
    except (OSError,ValueError,TypeError,EOFError,KeyboardInterrupt,MemoryError,getpass.GetPassWarning):
        print('ENCRYPTED_OBSERVATION_FAILED');return 2
    print('ENCRYPTED_COPY_CREATED' if args.action=='seal' else 'PRIVATE_PLAINTEXT_COPY_CREATED')
    return 0


if __name__=='__main__':raise SystemExit(main())
