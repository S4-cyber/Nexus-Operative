"""Injected fixed-host quote reads, durable pacing and saved-key revocation."""
import io
import json
from urllib.error import HTTPError

import pytest
from nexus import paper_quote_observation as subject,paper_credentials as credentials
from nexus import alpaca_paper_inspection as transport
from nexus.paper_read_pacing import PaperReadPacing
from test_alpaca_paper_inspection import Opener,Response,account,ACCOUNT,OTHER,KEY,SECRET
from test_paper_credentials import vault,save

EPOCH=1792195200


def quote(**changes):
    row=dict(bp='100',ap='101',bs='2',**{'as':'3'},t='2026-10-17T00:00:00.123456789Z')
    row.update(changes);return {'quotes':{'BTC/USD':row}}


def options(tmp_path,**changes):
    args=dict(expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT),
        opener=Opener([account(),quote(),account()]),wall_clock=lambda:EPOCH+1,elapsed_clock=lambda:50)
    args.update(changes);return args


def test_three_exact_gets_and_private_allowlisted_observation(tmp_path):
    args=options(tmp_path);r=subject.inspect_quote(KEY,SECRET,**args)
    assert [req.full_url for req,_ in args['opener'].requests]==[transport.URLS[0],transport.BTC_QUOTE_URL,transport.URLS[0]]
    assert all(req.method=='GET' and req.data is None and timeout<=4 for req,timeout in args['opener'].requests)
    assert r['quote']['exchange_time_ns']=='1792195200123456789' and r['quote']['sampled_age_ns']=='876543211'
    assert r['quote']['bid']=='100' and r['quote']['ask_size']=='3'
    assert r['source']=='INJECTED_UNATTESTED' and r['account_sha256']==args['pacing'].account_digest
    assert all(r[k] is False for k in subject.FLAGS)
    assert args['pacing'].inspect()['last_outcome']=='SUCCESS'
    encoded=json.dumps(r)
    assert all(v not in encoded for v in (KEY,SECRET,'PRIVATE_DO_NOT_RETURN',ACCOUNT))


@pytest.mark.parametrize('confirmed',[False,None,1,'true'])
def test_unconfirmed_never_creates_storage_or_reads_provider(tmp_path,confirmed):
    args=options(tmp_path,confirm_read_only_paper=confirmed)
    with pytest.raises(ValueError,match='CONFIRMATION_REQUIRED'):subject.inspect_quote(KEY,SECRET,**args)
    assert not list(tmp_path.iterdir()) and not args['opener'].requests


@pytest.mark.parametrize('pacing',[None,object()])
def test_pacing_is_required_before_any_io(tmp_path,pacing):
    args=options(tmp_path,pacing=pacing)
    with pytest.raises(ValueError,match='PACING_ACCOUNT_REQUIRED'):subject.inspect_quote(KEY,SECRET,**args)
    assert not list(tmp_path.iterdir()) and not args['opener'].requests


@pytest.mark.parametrize('change',[{'bp':'102'},{'ap':'NaN'},{'bs':True},{'as':'-1'},
    {'t':'2026-10-16T23:59:40Z'},{'t':'2026-10-17T00:00:04Z'},{'private_extra':SECRET}])
def test_bad_or_stale_quotes_leave_pending_read_without_acknowledgment(tmp_path,change):
    args=options(tmp_path,opener=Opener([account(),quote(**change),account()]))
    with pytest.raises(ValueError):subject.inspect_quote(KEY,SECRET,**args)
    assert len(args['opener'].requests)==2 and args['pacing'].inspect()['pending_token'] is not None


def test_foreign_account_blocks_data_host_before_credentials_sent_there(tmp_path):
    args=options(tmp_path,opener=Opener([dict(account(),id=OTHER),quote(),account()]))
    with pytest.raises(ValueError,match='ACCOUNT_DISAGREEMENT'):subject.inspect_quote(KEY,SECRET,**args)
    assert len(args['opener'].requests)==1


def test_changed_account_refuses_quote_and_keeps_pending(tmp_path):
    args=options(tmp_path,opener=Opener([account(),quote(),dict(account(),cash='999')]))
    with pytest.raises(ValueError,match='ACCOUNT_CHANGED'):subject.inspect_quote(KEY,SECRET,**args)
    assert args['pacing'].inspect()['pending_token'] is not None


def test_zero_reported_size_is_review_only_not_executable_liquidity(tmp_path):
    r=subject.inspect_quote(KEY,SECRET,**options(tmp_path,opener=Opener([account(),quote(bs='0'),account()])))
    assert r['quote']['checks']=='ZERO_REPORTED_SIZE_REVIEW_REQUIRED' and not r['executable_liquidity_verified']


def test_generic_reader_does_not_accept_new_data_url_or_arbitrary_locations(tmp_path):
    args=options(tmp_path)
    with transport._ReadSession(KEY,SECRET,**{k:v for k,v in args.items() if k!='confirm_read_only_paper'},confirm_read_only_paper=True) as session:
        for url in (transport.BTC_QUOTE_URL,transport.BTC_QUOTE_URL.replace('/us/','/us-1/'),'https://example.invalid/'):
            with pytest.raises(ValueError,match='PATH_DENIED'):session.get(url)
    assert not args['opener'].requests


@pytest.mark.parametrize('url',['https://example.invalid/private','https://data.alpaca.markets/v1beta3/crypto/us-1/latest/quotes'])
def test_changed_response_url_is_rejected_before_reading_body(tmp_path,url):
    response=Response(quote(),url=url);args=options(tmp_path,opener=Opener([account(),response]))
    with pytest.raises(ValueError,match='URL_CHANGED'):subject.inspect_quote(KEY,SECRET,**args)
    assert response.closed and response.reads==0


@pytest.mark.parametrize('status',[401,403,429])
def test_http_error_body_is_never_read_and_shared_outcome_is_retained(tmp_path,status):
    body=io.BytesIO(SECRET.encode())
    error=HTTPError(transport.BTC_QUOTE_URL,status,'PRIVATE_ERROR',{},body)
    args=options(tmp_path,opener=Opener([account(),error]))
    with pytest.raises(ValueError) as result:subject.inspect_quote(KEY,SECRET,**args)
    assert SECRET not in str(result.value) and body.closed
    state=args['pacing'].inspect();assert state['pending_token'] is None and state['last_outcome']!='SUCCESS'


def test_shared_cooldown_refuses_inventory_after_quote_without_new_requests(tmp_path):
    args=options(tmp_path);subject.inspect_quote(KEY,SECRET,**args);before=args['pacing'].path.read_bytes();opener=Opener()
    with pytest.raises(ValueError):transport.inspect_account(KEY,SECRET,confirm_read_only_paper=True,
        expected_account_id=ACCOUNT,pacing=args['pacing'],opener=opener,wall_clock=lambda:EPOCH+1,elapsed_clock=lambda:50)
    assert not opener.requests and args['pacing'].path.read_bytes()==before


def test_quote_that_expires_during_last_account_read_is_not_returned(tmp_path):
    now=[EPOCH+1];opener=Opener([account(),quote(),account()]);original=opener.open
    def delayed(req,timeout):
        if len(opener.requests)==2:now[0]+=10
        return original(req,timeout)
    opener.open=delayed
    args=options(tmp_path,opener=opener,wall_clock=lambda:now[0],elapsed_clock=lambda:50+(now[0]-(EPOCH+1)))
    with pytest.raises(ValueError,match='STALE_QUOTE'):subject.inspect_quote(KEY,SECRET,**args)


def test_quote_that_expires_while_committing_pacing_result_is_not_acknowledged(tmp_path,monkeypatch):
    now=[EPOCH+1];args=options(tmp_path,wall_clock=lambda:now[0],elapsed_clock=lambda:50+(now[0]-(EPOCH+1)))
    original=args['pacing'].finish
    def delayed(*a,**kw):
        result=original(*a,**kw);now[0]+=10;return result
    monkeypatch.setattr(args['pacing'],'finish',delayed)
    with pytest.raises(ValueError,match='STALE_QUOTE'):subject.inspect_quote(KEY,SECRET,**args)
    assert args['pacing'].inspect()['last_outcome']=='SUCCESS'


def test_source_checkout_pacing_is_refused_before_vault_or_creation(vault,tmp_path):
    from nexus.paper_activity_acceptance_probe import ROOT
    gate=PaperReadPacing(ROOT/'PRIVATE_QUOTE_FIXTURE.sqlite3',ACCOUNT);before=vault.reads
    with pytest.raises(ValueError):credentials.inspect_quote_saved(expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=gate)
    assert vault.reads==before and not gate.path.exists()


def test_public_quote_fixture_omits_private_account_prices_and_hashes():
    r=subject.fixture_report();assert r['passed'] and all(r[k] is True for k in subject.CHECKS)
    assert all(r[k] is False for k in (*subject.FLAGS,'actual_provider_access','actual_vault_access'))
    assert all(token not in json.dumps(r) for token in (ACCOUNT,SECRET,KEY,'1792195200','account_sha256'))


@pytest.mark.parametrize('field,value,code',[
    ('shared_cooldown_preserved',False,'BTC_QUOTE_OBSERVATION'),
    ('exchange_nanoseconds_preserved',False,'BTC_QUOTE_OBSERVATION'),
    ('benchmark_source_verified',True,'BTC_QUOTE_AUTHORITY'),
    ('ready_for_routing',True,'BTC_QUOTE_AUTHORITY')])
def test_release_verifier_requires_quote_evidence_without_authority(field,value,code):
    from nexus import release_trials
    r=subject.fixture_report();r[field]=value
    with pytest.raises(ValueError,match='RELEASE_TRIAL_'+code):release_trials._validate('inspection_inventory',{'passed':True,'btc_quote_observation':r})


def test_invalid_saved_scope_refuses_before_vault_and_provider(vault,tmp_path):
    save();before=vault.reads
    with pytest.raises(ValueError):credentials.inspect_quote_saved(expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=None)
    assert vault.reads==before and not list(tmp_path.iterdir())


def test_saved_cooldown_is_vault_silent(vault,tmp_path):
    save();args=options(tmp_path);credentials.inspect_quote_saved(**args);before=vault.reads
    with pytest.raises(ValueError,match='COOLDOWN'):credentials.inspect_quote_saved(**options(tmp_path))
    assert vault.reads==before


def test_key_removal_during_data_read_revokes_and_preserves_pending(vault,tmp_path):
    save();args=options(tmp_path);opener=args['opener'];original=opener.open
    def revoked(req,timeout):
        if req.full_url==transport.BTC_QUOTE_URL:credentials.remove(confirm_remove=True)
        return original(req,timeout)
    opener.open=revoked
    with pytest.raises(ValueError,match='REVOKED'):credentials.inspect_quote_saved(**args)
    assert len(opener.requests)==2 and args['pacing'].inspect()['pending_token'] is not None


def test_external_key_replacement_after_read_suppresses_return(vault,tmp_path):
    save();args=options(tmp_path);opener=args['opener'];original=opener.open
    def replaced(req,timeout):
        result=original(req,timeout)
        if len(opener.requests)==3:vault.data[(credentials.SERVICE,credentials.ACCOUNT)]=vault.data[(credentials.SERVICE,credentials.ACCOUNT)].replace(KEY,'fixture_new_key_only_123')
        return result
    opener.open=replaced
    with pytest.raises(ValueError,match='READ_REVOKED'):credentials.inspect_quote_saved(**args)
    assert args['pacing'].inspect()['last_outcome']=='SUCCESS'
