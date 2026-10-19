"""Shared local read admission precedes saved-key access and survives races."""
import pytest
import sqlite3
from nexus import paper_credentials as saved
from nexus import paper_read_pacing as pacing
from nexus import alpaca_paper_inspection as inventory
from nexus import paper_asset_rules as assets
from nexus import alpaca_activity_collection as activity
from nexus import broker_paper_recovery as recovery
from nexus import paper_quote_observation as quote
from nexus.broker_request_journal import BrokerRequestJournal
from test_alpaca_paper_inspection import ACCOUNT,OTHER,KEY,SECRET,Opener
from test_alpaca_paper_contract import plan
from test_paper_credentials import vault,save

OPERATIONS=pacing.OPERATIONS


def request_journal(tmp_path):
    journal=BrokerRequestJournal(tmp_path/'requests.sqlite3',ACCOUNT)
    journal.prepare(plan());journal.mark_submission_uncertain('nx_fixture')
    return journal


def invoke(operation,gate,journal,*,saved_pair=True,at=1059,opener=None,**kw):
    args=dict(expected_account_id=ACCOUNT,confirm_read_only_paper=True,pacing=gate,
        opener=opener or Opener(),wall_clock=lambda:at,elapsed_clock=lambda:50,**kw)
    keys=() if saved_pair else (KEY,SECRET)
    if operation=='ACCOUNT_INVENTORY':return (saved.inspect_saved if saved_pair else inventory.inspect_account)(*keys,**args)
    if operation=='BTC_ASSET_RULES':return (saved.inspect_rules_saved if saved_pair else assets.inspect_rules)(*keys,**args)
    if operation=='ACTIVITY_COLLECTION':
        window=dict(after='2026-10-17T00:00:00Z',until='2026-10-18T00:00:00Z')
        return saved.collect_saved([],**args,**window) if saved_pair else activity.collect_activities(*keys,[],**args,**window)
    if operation=='ORIGINAL_ID_RECOVERY':
        return saved.recover_saved(journal,'nx_fixture',**args) if saved_pair else recovery.recover_original(journal,'nx_fixture',*keys,**args)
    return (saved.inspect_quote_saved if saved_pair else quote.inspect_quote)(*keys,**args)


@pytest.mark.parametrize('operation',OPERATIONS)
@pytest.mark.parametrize('block',['cooldown','pending','auth','clock','capacity','foreign','corrupt'])
def test_known_shared_block_never_opens_saved_credentials_or_provider(tmp_path,vault,monkeypatch,operation,block):
    gate=pacing.PaperReadPacing(tmp_path/'pacing.sqlite3',OTHER if block=='foreign' else ACCOUNT)
    token=gate.begin(admission=lambda:1000);at=1060
    if block!='pending':gate.finish(token,admission=lambda:1000,status_code=401 if block=='auth' else 200)
    if block=='cooldown':at=1059
    if block=='clock':at=999
    if block=='capacity':monkeypatch.setattr(pacing,'MAX_SESSIONS',1)
    if block=='corrupt':gate.path.write_bytes(b'not sqlite')
    journal=request_journal(tmp_path);reader=Opener();before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises((ValueError,sqlite3.Error)) as error:
        invoke(operation,gate,journal,at=at,opener=reader)
    codes=dict(cooldown='COOLDOWN',pending='PENDING_REVIEW',auth='BLOCKED_REVIEW',clock='CLOCK_REGRESSION',capacity='SESSION_CAP')
    if block in codes:
        assert isinstance(error.value,inventory.PaperInspectionError)
        assert str(error.value)=='PAPER_READ_PACING_'+codes[block]
    assert vault.reads==vault.writes==vault.deletes==0 and not reader.requests
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('operation',OPERATIONS)
@pytest.mark.parametrize('head',['stale','invalid'])
def test_bad_expected_head_is_vault_silent_and_does_not_create_state(tmp_path,vault,operation,head):
    gate=pacing.PaperReadPacing(tmp_path/'missing'/'pacing.sqlite3',ACCOUNT);journal=request_journal(tmp_path)
    reader=Opener();expected='f'*64 if head=='stale' else 'private-invalid-head'
    with pytest.raises(ValueError):invoke(operation,gate,journal,at=1000,opener=reader,expected_pacing_head=expected)
    assert vault.reads==0 and not reader.requests and not gate.path.parent.exists()


@pytest.mark.parametrize('operation',OPERATIONS)
@pytest.mark.parametrize('explicit_head',[False,True])
def test_history_changed_during_vault_access_stops_before_begin_or_provider(tmp_path,vault,monkeypatch,operation,explicit_head):
    save();gate=pacing.PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT);journal=request_journal(tmp_path)
    original=saved._capture;reads=[];committed=[]
    def raced():
        value=original();reads.append(1)
        if len(reads)==1:
            token=gate.begin(admission=lambda:1000);gate.finish(token,admission=lambda:1000,status_code=200)
            committed.append(gate.path.read_bytes())
        return value
    monkeypatch.setattr(saved,'_capture',raced);reader=Opener();before=journal.path.read_bytes()
    with pytest.raises(ValueError):invoke(operation,gate,journal,at=1060,opener=reader,**({'expected_pacing_head':pacing.ZERO} if explicit_head else {}))
    assert len(reads)==1 and not reader.requests and gate.path.read_bytes()==committed[0]
    assert journal.path.read_bytes()==before and gate.inspect()['sessions']==1


@pytest.mark.parametrize('operation',OPERATIONS)
def test_direct_reader_rechecks_expected_head_after_permission_callback(tmp_path,operation):
    gate=pacing.PaperReadPacing(tmp_path/'pacing.sqlite3',ACCOUNT);journal=request_journal(tmp_path)
    changed=[];reader=Opener();before=journal.path.read_bytes()
    def race():
        if not changed:
            token=gate.begin(admission=lambda:1000);gate.finish(token,admission=lambda:1000,status_code=200)
            changed.append(gate.path.read_bytes())
        return True
    with pytest.raises(ValueError):
        invoke(operation,gate,journal,saved_pair=False,at=1060,opener=reader,
            expected_pacing_head=pacing.ZERO,permission_check=race)
    assert not reader.requests and journal.path.read_bytes()==before
    assert gate.inspect()['sessions']==1 and gate.path.read_bytes()==changed[0]


@pytest.mark.parametrize('operation',OPERATIONS)
def test_expected_head_requires_a_real_account_bound_gate_before_vault(tmp_path,vault,operation):
    journal=request_journal(tmp_path)
    for gate in (None,object()):
        reader=Opener()
        with pytest.raises(ValueError):invoke(operation,gate,journal,at=1000,opener=reader,expected_pacing_head=pacing.ZERO)
        assert vault.reads==0 and not reader.requests
