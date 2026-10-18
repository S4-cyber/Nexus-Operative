"""Encrypted, bounded quote evidence copies with full offline journal replay.

No credentials, provider calls, import/restore, source attestation or order power.
Existing private journals stay unchanged and unencrypted by this copy operation.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
import sqlite3

from cryptography.exceptions import InvalidTag,UnsupportedAlgorithm
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nexus import encrypted_observation as crypto,paper_quote_capture as capture,paper_quote_store as quotes
from nexus import broker_request_journal as requests,paper_read_pacing as pacing
from nexus.alpaca_paper_contract import canonical,sha
from nexus.alpaca_paper_inspection import _uuid

PROFILE='nexus.private-paper-quote-evidence.v1'
MAGIC=b'NEXUS-QUOTE-EVIDENCE-ENC-1\n'
MAX_PLAINTEXT_BYTES=20*1024*1024
HEADER_BYTES=len(MAGIC)+crypto.SALT_BYTES+crypto.NONCE_BYTES
MAX_ENCRYPTED_BYTES=HEADER_BYTES+MAX_PLAINTEXT_BYTES+16
FLAGS=(*capture.FLAGS,'bundle_source_attested','bundle_imported','original_plaintext_deleted','password_saved','plaintext_file_written','current_source_history_verified','exporter_identity_verified')
CHECKS=('full_three_journal_replay','historical_prefixes_preserved','local_capture_bindings_checked',
    'encrypted_roundtrip','randomized_ciphertext','wrong_password_rejected','tampering_rejected','source_files_unchanged')


def _fail(code):raise ValueError('PAPER_QUOTE_BUNDLE_'+code)


def _rows(value,*,columns,limit,body_index,body_cap):
    if type(value) is not list or not 1<=len(value)<=limit:_fail('ROW_COUNT')
    for row in value:
        if type(row) is not list or len(row)!=columns or type(row[0]) is not int:_fail('ROW_SHAPE')
        if any(type(x) is not str for x in row[1:]):_fail('ROW_SHAPE')
        if len(row[body_index].encode())>body_cap:_fail('ROW_BYTES')
        for digest in row[-2:]:quotes._head(digest)
        if columns==5 and len(row[1])>32:_fail('ROW_KIND')
    return value


def _memory(table,columns,rows):
    # Explicitly memory-only: no supplied path, attachment, SQL or schema enters
    # this connection. Existing retained loaders validate the copied rows.
    db=sqlite3.connect(':memory:')
    try:
        db.execute('PRAGMA temp_store=MEMORY');db.execute('PRAGMA journal_mode=MEMORY')
        db.execute('CREATE TABLE '+table+' ('+columns+')')
        marks=','.join('?' for _ in rows[0]);db.executemany('INSERT INTO '+table+' VALUES ('+marks+')',rows)
        return db
    except BaseException:db.close();raise


def validate(value):
    if type(value) is not dict or set(value)!={'profile','account_id','pacing_scope_sha256','heads','request_events','quote_records','pacing_events','bundle_sha256'}:_fail('PROFILE')
    if value['profile']!=PROFILE:_fail('PROFILE')
    account=_uuid(value['account_id'])
    if account!=value['account_id']:_fail('ACCOUNT')
    account_digest=hashlib.sha256(account.encode()).hexdigest()
    quotes._head(value['pacing_scope_sha256']);quotes._head(value['bundle_sha256'])
    if type(value['heads']) is not dict or set(value['heads'])!={'request','quote','pacing'}:_fail('HEADS')
    for head in value['heads'].values():quotes._head(head)
    if len(canonical(value).encode())>MAX_PLAINTEXT_BYTES:_fail('PAYLOAD_CAP')
    if sha({k:v for k,v in value.items() if k!='bundle_sha256'})!=value['bundle_sha256']:_fail('DIGEST')
    request_rows=_rows(value['request_events'],columns=5,limit=requests.MAX_EVENTS,body_index=2,body_cap=requests.MAX_EVENT_BYTES)
    quote_rows=_rows(value['quote_records'],columns=4,limit=quotes.MAX_RECORDS+1,body_index=1,body_cap=quotes.MAX_RECORD_BYTES)
    pacing_rows=_rows(value['pacing_events'],columns=4,limit=2*pacing.MAX_SESSIONS+1,body_index=1,body_cap=1024)
    # These paths are never opened: original request/pacing constructors only
    # establish account scope. Quote replay needs only the validated account
    # digest, so construct its filesystem-free validator without path preflight.
    request_validator=requests.BrokerRequestJournal('__memory_only_unused__',account)
    with closing(_memory('events','seq INTEGER PRIMARY KEY,kind TEXT,body TEXT,previous TEXT,digest TEXT',request_rows)) as db:
        retained,_,_,request_head=request_validator._load(db)
    original=dict(heads={r[4]:r[0] for r in request_rows},
        prepared={json.loads(r[2])['payload']['client_order_id']:r[0] for r in request_rows if r[1]=='PREPARE'},
        plans={k:r['plan'] for k,r in retained.items()})
    quote_validator=object.__new__(quotes.PaperQuoteStore);quote_validator.account=account_digest
    with closing(_memory('records','seq INTEGER PRIMARY KEY,body TEXT,previous TEXT,digest TEXT',quote_rows)) as db:
        quote_head,records=quote_validator._load(db,original)
    pacing_validator=pacing.PaperReadPacing('__memory_only_unused__',account)
    with closing(_memory('events','seq INTEGER PRIMARY KEY,body TEXT,previous TEXT,digest TEXT',pacing_rows)) as db:
        state=pacing_validator._load(db)
    if dict(request=request_head,quote=quote_head,pacing=state['head_sha256'])!=value['heads']:_fail('HEAD_DISAGREEMENT')
    if (not records or any(r['profile']!=quotes.CAPTURE_PROFILE or r['pacing_scope_sha256']!=value['pacing_scope_sha256'] for r in records) or
        state['pending_token'] is not None or state['review_required'] or state['interruptions'] or state['sessions']!=len(records)):_fail('UNBOUND_HISTORY')
    bound=[dict(before_head_sha256=r['pacing_before_head_sha256'],after_head_sha256=r['pacing_after_head_sha256'],outcome='SUCCESS') for r in records]
    if bound!=state['completed_sessions']:_fail('PACING_BINDINGS')
    return dict(mode='PRIVATE_PAPER_QUOTE_BUNDLE_REVIEW',status='REVIEW_REQUIRED',read_only=True,
        profile=PROFILE,account_sha256=account_digest,bundle_sha256=value['bundle_sha256'],heads=dict(value['heads']),
        retained_requests=len(retained),retained_quotes=len(records),retained_pacing_sessions=state['sessions'],
        full_request_history_replayed=True,full_quote_history_replayed=True,full_pacing_history_replayed=True,
        local_capture_history_agreement=True,**{k:False for k in FLAGS},
        limitations='Full copied original-request, quote and pacing chains replayed in memory, including historical prefixes and one quote per successful local transition. Password authentication protects ciphertext under that password; it does not authenticate a broker/source, prove exporter identity, current/complete history, clocks, order timing, fees/funds or profit. The original path scope is retained as a digest, never reinterpreted as this machine\'s provider quota. No file import/restore, vault/network/order, plaintext file or release authority. Original journals and any other copies remain private plaintext unless separately protected.')


def loads(raw):
    if type(raw) is not bytes or not 1<=len(raw)<=MAX_PLAINTEXT_BYTES:_fail('PAYLOAD_CAP')
    def pairs(items):
        result={}
        for key,val in items:
            if key in result:_fail('DUPLICATE_KEY')
            result[key]=val
        return result
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _: _fail('NONFINITE_JSON'))
    validate(value)
    if canonical(value).encode()!=raw:_fail('CANONICAL_JSON_REQUIRED')
    return value


def snapshot(*,expected_account_id,request_journal_path,quote_store_path,pacing_path,expected_request_head,
             expected_quote_head,expected_pacing_head):
    options=dict(expected_account_id=expected_account_id,request_journal_path=request_journal_path,quote_store_path=quote_store_path,
        pacing_path=pacing_path,expected_request_head=expected_request_head)
    store,gate,scope=capture._stores(expected_account_id,request_journal_path,quote_store_path,pacing_path)
    before=capture.status(**options)
    if not before['local_capture_history_agreement'] or before['retained_quotes']<1:_fail('CAPTURE_HISTORY_REQUIRED')
    if before['quote_head_sha256']!=expected_quote_head or before['pacing_head_sha256']!=expected_pacing_head:_fail('EXPECTED_HEAD_CHANGED')
    with closing(store.journal._connect()) as db:
        db.execute('BEGIN');request_rows=db.execute('SELECT seq,kind,body,previous,digest FROM events ORDER BY seq LIMIT ?',(requests.MAX_EVENTS+1,)).fetchall()
    with closing(store._connect()[0]) as db:quote_rows=db.execute('SELECT seq,body,previous,digest FROM records ORDER BY seq LIMIT ?',(quotes.MAX_RECORDS+2,)).fetchall()
    with closing(gate._connect()) as db:
        db.execute('BEGIN');pacing_rows=db.execute('SELECT seq,body,previous,digest FROM events ORDER BY seq LIMIT ?',(2*pacing.MAX_SESSIONS+2,)).fetchall()
    value=dict(profile=PROFILE,account_id=gate.account,pacing_scope_sha256=scope,
        heads=dict(request=expected_request_head,quote=expected_quote_head,pacing=expected_pacing_head),
        request_events=[list(r) for r in request_rows],quote_records=[list(r) for r in quote_rows],pacing_events=[list(r) for r in pacing_rows])
    value['bundle_sha256']=sha(value);validate(value)
    if capture.status(**options)!=before:_fail('SOURCE_CHANGED_DURING_SNAPSHOT')
    return canonical(value).encode()


def seal(raw,passphrase):
    loads(raw)
    salt=os.urandom(crypto.SALT_BYTES);nonce=os.urandom(crypto.NONCE_BYTES);header=MAGIC+salt+nonce
    encrypted=header+AESGCM(crypto._derive(passphrase,salt)).encrypt(nonce,raw,header)
    if len(encrypted)>MAX_ENCRYPTED_BYTES:_fail('ENCRYPTED_CAP')
    return encrypted


def _envelope(raw):
    if type(raw) is not bytes or not HEADER_BYTES+16<=len(raw)<=MAX_ENCRYPTED_BYTES or not raw.startswith(MAGIC):_fail('ENCRYPTED_FORMAT')


def unseal(raw,passphrase):
    _envelope(raw)
    header=raw[:HEADER_BYTES];salt=header[len(MAGIC):len(MAGIC)+crypto.SALT_BYTES];nonce=header[-crypto.NONCE_BYTES:]
    try:plain=AESGCM(crypto._derive(passphrase,salt)).decrypt(nonce,raw[HEADER_BYTES:],header)
    except InvalidTag:_fail('AUTHENTICATION_FAILED')
    loads(plain)
    return plain


def public_summary(review):
    if review.get('mode')!='PRIVATE_PAPER_QUOTE_BUNDLE_REVIEW' or review.get('local_capture_history_agreement') is not True:_fail('REVIEW_REQUIRED')
    if (review.get('profile')!=PROFILE or review.get('status')!='REVIEW_REQUIRED' or review.get('read_only') is not True or
        any(review.get(k) is not True for k in ('full_request_history_replayed','full_quote_history_replayed','full_pacing_history_replayed')) or
        any(review.get(k) is not False for k in FLAGS)):_fail('AUTHORITY')
    for key,cap in (('retained_requests',requests.MAX_REQUESTS),('retained_quotes',quotes.MAX_RECORDS),('retained_pacing_sessions',pacing.MAX_SESSIONS)):
        if type(review.get(key)) is not int or not 1<=review[key]<=cap:_fail('PUBLIC_COUNT')
    if review['retained_quotes']!=review['retained_pacing_sessions']:_fail('PUBLIC_COUNT')
    return dict(mode='PUBLIC_QUOTE_BUNDLE_CONTENT_DIAGNOSTIC',status='REVIEW_REQUIRED',
        retained_requests=review['retained_requests'],retained_quotes=review['retained_quotes'],retained_pacing_sessions=review['retained_pacing_sessions'],
        full_copied_history_replayed=True,local_capture_history_agreement=True,**{k:False for k in FLAGS},
        scope='Aggregate copied-content replay only; no source/authenticity/currentness/provider/clock/fee/funds/order or release acceptance. No private account IDs, heads, prices, dates or paths included.')


def _fixture(root):
    options,_=capture._fixture(root)
    # Quote belongs to an earlier valid request prefix. Retain a later marker
    # to exercise full-tail and historical-prefix replay on the second host.
    journal=requests.BrokerRequestJournal(options['request_journal_path'],options['expected_account_id'])
    journal.mark_submission_uncertain('nx_quote_fixture')
    options['expected_request_head']=journal.recovery()['head_sha256']
    reviewed=capture.status(**options)
    options.update(expected_quote_head=reviewed['quote_head_sha256'],expected_pacing_head=reviewed['pacing_head_sha256'])
    return options


def fixture_report():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix='nexus-quote-bundle-') as directory:
        root=Path(directory);options=_fixture(root);before={p.name:p.read_bytes() for p in root.iterdir()}
        raw=snapshot(**options);value=loads(raw);reviewed=validate(value);phrase=os.urandom(32).hex()
        first,second=seal(raw,phrase),seal(raw,phrase)
        wrong=tampered=False
        try:unseal(first,phrase+' different')
        except ValueError:wrong=True
        changed=first[:-1]+bytes([first[-1]^1])
        try:unseal(changed,phrase)
        except ValueError:tampered=True
        body=json.loads(value['quote_records'][1][1])
        checks=dict(full_three_journal_replay=all(reviewed[k] is True for k in ('full_request_history_replayed','full_quote_history_replayed','full_pacing_history_replayed')),
            historical_prefixes_preserved=body['request_head_sha256']!=value['heads']['request'],
            local_capture_bindings_checked=reviewed['local_capture_history_agreement'] and reviewed['retained_quotes']==reviewed['retained_pacing_sessions']==1,
            encrypted_roundtrip=unseal(first,phrase)==raw,randomized_ciphertext=first!=second,
            wrong_password_rejected=wrong,tampering_rejected=tampered,source_files_unchanged=before=={p.name:p.read_bytes() for p in root.iterdir()})
        return dict(source='SYNTHETIC_ENCRYPTED_QUOTE_BUNDLE_FIXTURE',passed=all(checks.values()),**checks,
            cipher='AES256GCM_QUOTE_DOMAIN_RANDOM_NONCE12_SALT16',kdf='SCRYPT_N131072_R8_P1',
            actual_provider_access=False,actual_vault_access=False,**{k:False for k in FLAGS})


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PAPER_QUOTE_BUNDLE_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__);parser.add_argument('action',choices=('seal','review','self-test'))
    for field in ('expected-account','request-journal','quote-store','pacing','request-head','quote-head','pacing-head','source','output'):
        parser.add_argument('--'+field)
    parser.add_argument('--public-summary',action='store_true',help='Print only fixed aggregate content counts after authenticated in-memory review; no private IDs/heads/amounts.')
    args=parser.parse_args()
    names=(args.expected_account,args.request_journal,args.quote_store,args.pacing,args.request_head,args.quote_head,args.pacing_head)
    try:
        if args.action=='self-test':
            if any(x is not None for x in (*names,args.source,args.output)) or args.public_summary:_fail('SELF_TEST_ARGUMENTS')
            result=fixture_report()
        elif args.action=='review':
            if any(x is not None for x in (*names,args.output)) or args.source is None:_fail('REVIEW_ARGUMENTS')
            path=crypto._private_path(args.source);encrypted=crypto._read(path,MAX_ENCRYPTED_BYTES)
            _envelope(encrypted)
            raw=unseal(encrypted,crypto.prompt_passphrase());reviewed=validate(loads(raw))
            if crypto._read(path,MAX_ENCRYPTED_BYTES)!=encrypted:_fail('INPUT_CHANGED_DURING_REVIEW')
            result=public_summary(reviewed) if args.public_summary else reviewed
        else:
            if any(x is None for x in names) or args.source is not None or args.output is None or args.public_summary:_fail('SEAL_ARGUMENTS')
            output=crypto._private_path(args.output)
            if output.exists() or output.is_symlink():_fail('NEW_OUTPUT_REQUIRED')
            from pathlib import Path
            families={Path(str(Path(p).resolve())+suffix) for p in (args.request_journal,args.quote_store,args.pacing) for suffix in ('','-journal','-wal','-shm','.owner.lock')}
            if output.resolve() in families:_fail('OUTPUT_ALIASES_SOURCE_FAMILY')
            options=dict(expected_account_id=args.expected_account,request_journal_path=args.request_journal,quote_store_path=args.quote_store,pacing_path=args.pacing,
                expected_request_head=args.request_head,expected_quote_head=args.quote_head,expected_pacing_head=args.pacing_head)
            raw=snapshot(**options);phrase=crypto.prompt_passphrase()
            if phrase!=crypto.prompt_passphrase('Repeat passphrase: '):_fail('PASSWORD_DISAGREEMENT')
            encrypted=seal(raw,phrase)
            if snapshot(**options)!=raw:_fail('SOURCE_CHANGED_BEFORE_WRITE')
            crypto._write_new(output,encrypted)
            if crypto._read(output,MAX_ENCRYPTED_BYTES)!=encrypted or snapshot(**options)!=raw:_fail('SOURCE_OR_OUTPUT_CHANGED_AFTER_WRITE')
            print('ENCRYPTED_QUOTE_EVIDENCE_COPY_CREATED');return 0
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,UnicodeError,OverflowError,MemoryError,RecursionError,KeyboardInterrupt,EOFError,UnsupportedAlgorithm,crypto.getpass.GetPassWarning):
        print('PAPER_QUOTE_BUNDLE_FAILED');return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
