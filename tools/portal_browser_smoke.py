"""Real-browser PORTAL help acceptance against intercepted fixtures, never account/feed IO."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
ORIGIN='http://nexus-ui.invalid'
ORDER='pf_0123456789abcdef_7_BUY'


def fixtures(path,pending=True,missing_history=False):
    if path=='/api/onboarding/tasks':return dict(checked_at=1000,summary='Automatic local observations; no execution permission.',items=[
        dict(id='engine',title='Use built-in research and AI',group='Start here',status='COMPLETE',checked=True,detail='Built-in numerical learning is included; no provider account is required.',tab='ai',target='counciltest'),
        dict(id='budget',title='Save your virtual budget',group='Before a paper session',status='NEEDS_SETUP',checked=False,detail='Save virtual allocations in Risk; no money is deposited.',tab='risk',target='paperbudget'),
        dict(id='payment',title='Link payment or a bank account',group='Future connections',status='UNAVAILABLE',checked=False,detail='Payment linking is not implemented and is not needed for offline research.',tab='accounts',target='preview')])
    if path=='/api/matrix/hub/refresh':return dict(phase='STOPPED',reason=None,configuration=None,polls=0,poll_limit=60,fits=0,fit_limit=8,unchanged_skips=0,cycle_active=False)
    if path=='/api/matrix/hub':return dict(bars=300,conflicts=0,database_bytes=32768,database_byte_limit=134217728,artifact_bytes=8192,artifact_byte_limit=33554432)
    if path.startswith('/api/matrix/hub/signal?'):return dict(decision='ABSTAIN_STALE_OR_FUTURE_DATA',model_content_sha256='a'*64,forecast_as_of=1000,age_seconds=999999,forecasts=[])
    if path=='/api/roadmap':return dict(revision='0.86.0',branch='development/windows-portal-foundation',delivery_stages=[dict(id='D5',title='Secure deposits',status='NOT_IMPLEMENTED',progress='Preview only.',remaining='Reconcile spendable funds.'),dict(id='D9',title='1.0 production release',status='BLOCKED',progress='Prerelease.',remaining='Independent acceptance.')],production_scope='Separate live authorization required.',milestones=[],strategic_goals=[],next_steps=['Validate sustained paper operation.'],supporting_track='Cloud planned.',speed_target='No broker speed validation.',resume='Read source roadmap.')
    if path=='/api/execution/alpaca-paper/fee-projection-fixture':return dict(input_consistency_ok=True,reasons=[],projected_balances=dict(USD='1009.78011',BTC='0'),fees_by_asset=dict(BTC='0.001',USD='0.10989'),cash_path=dict(declared_path_consistency_ok=True),activity_normalization=dict(status='REVIEW_REQUIRED',fee_coverage_complete=False,executions=[{},{}],fee_debits=[{},{}]))
    if path=='/api/execution/alpaca-paper/request-recovery-fixture':return dict(passed=True,current_replay_duplicate=True,lookup_recovery=dict(attempts=2,rate_limit_wait_seconds=30,interruption_recorded=True,late_completion_rejected=True),recovered=dict(retained_events=6,requests=[dict(snapshot=dict(filled_quantity='1'))]))
    if path=='/api/public-data/efficiency':return dict(read_only=True,requests=12,transport_attempts=4,cache_hits=6,shared_returns=2,transport_failures=0,wait_timeouts=0,active_requests=0,active_request_limit=3,mean_successful_transport_ms=120)
    if path=='/api/status':return dict(portal_enabled=False,halted=False)
    if path=='/api/tiers':return dict(tiers=[],tier_permissions=[dict(tier=t,enabled=False,maximum_intent_usd=n) for t,n in [('terrestrial',100),('solar',200),('universal',200)]])
    if path.startswith('/api/tiers/performance'):return dict(tiers=[])
    if path=='/api/markets':return dict(providers=[])
    if path=='/api/strategy-branches':return dict(branches=[])
    if path=='/api/paper/settings':return dict(paper_budget=10000,terrestrial_allocation=50,solar_allocation=35,universal_allocation=15)
    if path=='/api/paper/portfolio':return dict(initialized=False)
    if path=='/api/paper/ai-provider/status':return dict(configured=False,requests_used_process=0,max_requests_process=5,credential_observation=dict(state='NOT_CHECKED',current_vault_checked=False))
    if path=='/api/observer/status':return dict(active=False,cycles=0,last_error=None)
    if path.startswith('/api/observer/history'):return dict(events=[])
    if path=='/api/paper/forward/recovery':
        if missing_history:return dict(provisional=False,runner_enabled=False,halted=False,order_history_present=False,
            pending_orders=[],positions={},session_reviews=[],sessions_examined=0)
        return dict(provisional=False,runner_enabled=False,halted=False,order_history_present=True,
                    pending_orders=[dict(id=ORDER,side='BUY',status='SIMULATED_PARTIALLY_FILLED',simulated_filled_notional='49')] if pending else [],
                    positions={'BTC-USD':dict(quantity='0.49',entry='100')},
                    session_reviews=[dict(session='0123456789abcdef',reasons=['ORDER_CLEANUP_REQUIRES_REVIEW'])],sessions_examined=1)
    if path=='/api/execution/intent/'+ORDER:return dict(order=dict(id=ORDER,status='SIMULATED_PARTIALLY_FILLED'))
    if path=='/api/paper/forward':return dict(enabled=False,product='BTC-USD',cycles=7,tier='terrestrial',last_error=None,
            storage_capacity=dict(quote_observations_retained=7,quote_limit=100000,sessions_retained=1,session_limit=1000,warnings=[]),history=[])
    raise ValueError('UNEXPECTED_BROWSER_FIXTURE_REQUEST: '+path)


def paper_observation_fixture():
    from nexus.portable_paper_observation import create, DIGEST_SCOPE
    from nexus.alpaca_paper_contract import sha
    result = dict(mode='READ_ONLY_PAPER_INSPECTION', source='INJECTED_UNATTESTED', checks='OBSERVED_FIELDS_COVERED',
        account_id='01234567-89ab-cdef-0123-456789abcdef', observed_usd_ceiling='900', observed_btc_available='1.9',
        position_count=1, open_order_count=0, ready_for_routing=False, spendable_funds_verified=False,
        broker_account_reconciled=False, broker_connected=False, ledger_applied=False, live_orders_enabled=False,
        atomic_snapshot=False, automatic_retry=False,
        observed_inventory=dict(cash='1000',non_marginable_buying_power='900',btc_total='2',btc_available='1.9',btc_unavailable_difference='0.1',scope_supported=True),
        account_fields_sha256='a'*64, read_timing=dict(started_at=1000.0,completed_at=1000.0,elapsed_seconds=0.0),
        observation_digest_scope=DIGEST_SCOPE)
    result['reported_inventory_sha256'] = sha(result['observed_inventory'])
    result['observation_sha256'] = sha(dict(profile='nexus.paper-account-observation.v1', **{k:result[k] for k in
        ('account_fields_sha256','reported_inventory_sha256','position_count','open_order_count','source','read_timing')}))
    from nexus.paper_open_order_terms import summarize
    result['open_order_terms'] = summarize([],expected_account_id=result['account_id'])
    result['portable_observation'] = create(result)
    return result


def paper_pacing_fixture():
    return dict(account_sha256='a'*64,before_head_sha256='b'*64,after_head_sha256='c'*64,
        events=2,sessions=1,next_allowed_at=1060,min_post_result_cooldown_seconds=60,
        coordination_scope='EXACT_LOCAL_JOURNAL_ONLY_NOT_BROKER_WIDE_OR_DISTRIBUTED')


def paper_pacing_status_fixture():
    from nexus.paper_acceptance_probe import FLAGS
    return dict(mode='PAPER_ACCEPTANCE_PACING_STATUS',status='REVIEW_REQUIRED',read_only=True,
        journal_present=True,account_sha256='a'*64,head_sha256='c'*64,sessions=1,
        session_capacity_remaining=255,next_allowed_at=1060,pending_read=False,retained_review_block=False,
        suggested_action='RESPECT_RETAINED_COOLDOWN_AND_SEPARATELY_CONFIRM_ONE_READ',
        limitations='Local replay only, no vault or provider access.',**{flag:False for flag in FLAGS})


def validate_geometry(data):
    if data['position']!='fixed' or data['background']!='rgb(0, 0, 0)' or data['color']!='rgb(230, 164, 255)':
        raise ValueError('HELP_BROWSER_STYLE_DISAGREEMENT')
    if data['font_size']<16 or data['pointer_events']!='none':raise ValueError('HELP_BROWSER_LEGIBILITY_DISAGREEMENT')
    r=data['rect'];width=data['viewport_width'];height=data['viewport_height']
    if r['x']<0 or r['y']<0 or abs(width-r['right']-16)>1 or abs(height-r['bottom']-16)>1:
        raise ValueError('HELP_BROWSER_POSITION_DISAGREEMENT')
    if r['width']>width-30 or data['scroll_height']>data['client_height']+1:
        raise ValueError('HELP_BROWSER_TEXT_CLIPPED')


def run(output,channel=None):
    from playwright.sync_api import sync_playwright,expect
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    html=(ROOT/'dashboard/portal_preview.html').read_text(encoding='utf-8')
    reports=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'channel':channel} if channel else {}))
        try:
            for width,height in ((1280,900),(390,844),(320,568)):
                context=browser.new_context(viewport=dict(width=width,height=height),service_workers='block')
                page=context.new_page();errors=[];blocked=[];writes=[];state={'pending':True,'paper_saved':False,'missing_history':False};current_control=None
                page.add_init_script('''window.__helpEvents=[];for(const kind of ['pointerover','pointerout','scroll','focusin','focusout','blur'])document.addEventListener(kind,e=>{const c=e.target&&e.target.closest?e.target.closest('button,input,select,textarea'):null;window.__helpEvents.push({kind,id:c&&c.id,tag:c&&c.tagName,scrollY:window.scrollY});if(window.__helpEvents.length>60)window.__helpEvents.shift();},true);''')
                page.on('pageerror',lambda error:errors.append(str(error)))
                def route(request):
                    url=urlsplit(request.request.url);path=url.path+('?' + url.query if url.query else '')
                    if url.scheme+'://'+url.netloc!=ORIGIN:
                        blocked.append(request.request.url);request.abort();return
                    if path=='/portal':request.fulfill(status=200,content_type='text/html',body=html);return
                    if path=='/favicon.ico':request.fulfill(status=204);return
                    if request.request.method!='GET':
                        writes.append(path)
                        paper_base='/api/execution/alpaca-paper/local-credentials/'
                        if path.startswith(paper_base):
                            action=path[len(paper_base):]
                            if action not in ('status','save','inspect','paced-inspect','pacing-status','remove'):
                                blocked.append('UNKNOWN_PAPER_ACTION');request.abort();return
                            if action=='save':state['paper_saved']=True
                            if action=='remove':state['paper_saved']=False
                            if action in ('inspect','paced-inspect'):
                                result=paper_observation_fixture()
                                if action=='paced-inspect':
                                    if request.request.post_data_json!={'expected_account_id':result['account_id'],'confirm_read_only_paper':True}:
                                        blocked.append('INVALID_PACED_BODY');request.abort();return
                                    result.update(durable_local_pacing=True,local_pacing=paper_pacing_fixture())
                                request.fulfill(json=result);return
                            if action=='pacing-status':
                                if request.request.post_data_json!={'expected_account_id':paper_observation_fixture()['account_id']}:
                                    blocked.append('INVALID_PACING_STATUS_BODY');request.abort();return
                                request.fulfill(json=paper_pacing_status_fixture());return
                            request.fulfill(json=dict(configured=state['paper_saved'],review_required=False,ready_for_routing=False));return
                        if path=='/api/execution/intent/'+ORDER+'/cancel':
                            state['pending']=False;request.fulfill(json=dict(status='SIMULATED_CANCELLED'));return
                        blocked.append('UNEXPECTED_WRITE '+path);request.abort();return
                    try:request.fulfill(json=fixtures(path,state['pending'],state['missing_history']))
                    except ValueError as exc:blocked.append(str(exc));request.abort()
                page.route('**/*',route)
                try:
                    page.goto(ORIGIN+'/portal')
                    expect(page.locator('#tier-engagement button')).to_have_count(3)
                    # Render cut-state fixtures only; no stop/reset request is sent.
                    page.evaluate("() => {halted=true;byId('reset-halt').hidden=false;renderEngineStatus({emergency_cut_state:'PENDING',emergency_cut_pending:true})}")
                    expect(page.locator('#status')).to_contain_text('TRADING CUT PENDING')
                    expect(page.locator('#reset-halt')).to_be_disabled()
                    page.locator('#reset-halt').hover()
                    expect(page.locator('#control-help-text')).to_contain_text('Clear the trading cut')
                    page.mouse.move(0,0)
                    page.screenshot(path=str(output/f'emergency-pending-{width}x{height}.png'))
                    page.evaluate("() => renderEngineStatus({emergency_cut_state:'UNCONFIRMED',emergency_cut_pending:false})")
                    expect(page.locator('#status')).to_contain_text('CUT UNCONFIRMED')
                    expect(page.locator('#reset-halt')).to_be_enabled()
                    page.evaluate("() => renderEngineStatus({emergency_cut_state:'VERIFIED',emergency_cut_pending:false})")
                    expect(page.locator('#status')).to_contain_text('TRADING CUT VERIFIED')
                    page.evaluate("() => {halted=false;byId('reset-halt').hidden=true;renderEngineStatus({emergency_cut_state:'CLEAR',emergency_cut_pending:false});syncTradingControls()}")
                    state['missing_history']=True
                    page.locator('#forward-recovery').click()
                    expect(page.locator('#forward-recovery-status')).to_contain_text('No order journal found')
                    expect(page.locator('#forward-recovery-status')).to_contain_text('do not prove a flat account')
                    expect(page.get_by_role('button',name='Cancel Pending Remainder')).to_have_count(0)
                    state['missing_history']=False
                    page.locator('#forward-recovery').click()
                    expect(page.get_by_role('button',name='Cancel Pending Remainder')).to_have_count(1)
                    # Display account fixtures without activating PORTAL, observation or order IO.
                    now=time.time()
                    fixture_points=[]
                    for i in range(4):
                        fixture_points.append(dict(at=now-18+i*6,complete=True,equity=str(1000+i*3),cash='800',pnl=str(i*3),initial='1000',account_revision=2,
                            positions=[dict(product='BTC-USD',quantity='1',unrealized_pnl=str(i*5)),dict(product='ETH-USD',quantity='2',unrealized_pnl=str(-i*2))],missing_products=[]))
                    matrix_fixture=dict(points=fixture_points,range_start=now-60,range_end=now,observations_in_range=4,downsampled=False,account_initialized=True,current_revision=2,
                        retention=dict(total_samples=4,active_samples=4,archived_samples=0,retained_limit=6000000,storage=dict(database_bytes=10485760,database_byte_limit=1073741824,warnings=[])),
                        adjustments=[dict(id=2,at=now,action='PAPER_BUY',product='BTC-USD',quantity='1',entry='100',cash_delta='-100')])
                    page.evaluate("d=>{const section=document.getElementById('matrix-hangar');section.hidden=false;section.classList.add('open');section.setAttribute('aria-hidden','false');document.getElementById('matrix-state').textContent='INTERCEPTED ACCOUNT FIXTURE / NO BROKER';applyAccountHistory(d)}",matrix_fixture)
                    expect(page.locator('#matrix-account-summary')).to_contain_text('1,009.00')
                    expect(page.locator('#matrix-account-summary')).to_contain_text('retained 4 of 6,000,000')
                    expect(page.locator('#matrix-account-summary')).to_contain_text('history storage 10.0 of 1024 MiB')
                    page.locator('#matrix-fractal').click()
                    expect(page.locator('#matrix-fractal')).to_have_attribute('aria-pressed','true')
                    expect(page.locator('#matrix-account-title')).to_contain_text('Coordinated position movement')
                    expect(page.locator('#matrix-account-guide')).to_contain_text('ring = zero USD')
                    expect(page.locator('#matrix-account-chart')).to_have_attribute('aria-label','Fractal: coordinated position points and individual unrealized USD histories')
                    expect(page.locator('#matrix-account-legend')).to_contain_text('qty 2 / unrealized $-6.00')
                    expect(page.locator('#matrix-account-legend')).to_contain_text('BTC-USD')
                    expect(page.locator('#matrix-account-legend')).to_contain_text('ETH-USD')
                    page.locator('#matrix-adjustments').select_option('2')
                    expect(page.locator('#matrix-adjustment-detail')).to_contain_text('recorded quantity 1')
                    page.mouse.move(0,0);expect(page.locator('#control-help')).to_be_hidden()
                    page.locator('#matrix-account-chart').scroll_into_view_if_needed()
                    page.screenshot(path=str(output/f'fractal-{width}x{height}.png'))
                    page.locator('#matrix-singularity').click()
                    expect(page.locator('#matrix-singularity')).to_have_attribute('aria-pressed','true')
                    expect(page.locator('#matrix-account-title')).to_contain_text('SINGULARITY')
                    page.mouse.move(0,0);expect(page.locator('#control-help')).to_be_hidden()
                    page.screenshot(path=str(output/f'singularity-{width}x{height}.png'))
                    page.locator('#tab-accounts').click()
                    page.locator('#broker-request-fixture').click()
                    expect(page.locator('#broker-request-result')).to_contain_text('Retained partial quantity 1 BTC')
                    expect(page.locator('#broker-request-result')).to_contain_text('Fixture rate-limit wait 30 seconds')
                    expect(page.locator('#broker-request-result')).to_contain_text('Interrupted lookup review recorded')
                    expect(page.locator('#broker-request-result')).to_contain_text('Late reply rejected')
                    expect(page.locator('#broker-request-fixture')).to_be_enabled()
                    page.locator('#broker-fee-fixture').click()
                    expect(page.locator('#broker-fee-result')).to_contain_text('COVERED IN SUPPLIED HISTORY')
                    expect(page.locator('#broker-fee-result')).to_contain_text('spendable funds NOT verified')
                    expect(page.locator('#broker-fee-result')).to_contain_text('2 unlinked fee debits')
                    expect(page.locator('#broker-fee-result')).to_contain_text('coverage NOT verified')
                    expect(page.locator('#broker-fee-fixture')).to_be_enabled()
                    page.locator('#tab-research').click()
                    page.locator('#hub-refresh-status').click()
                    expect(page.locator('#hub-refresh-readout')).to_contain_text('STOPPED')
                    expect(page.locator('#hub-refresh-public')).to_be_checked()
                    page.locator('#hub-refresh-public').uncheck()
                    if writes:raise ValueError('PUBLIC_COLLECTION_CHECKBOX_STARTED_WORK')
                    page.locator('#hub-refresh-public').check()
                    page.locator('#feed-efficiency').click()
                    expect(page.locator('#feed-efficiency-result')).to_contain_text('12 reader calls / 4 transport attempts')
                    page.locator('#hub-status').click()
                    expect(page.locator('#hub-readout')).to_contain_text('300 unique bars')
                    page.locator('#hub-signal').click()
                    expect(page.locator('#hub-readout')).to_contain_text('ABSTAIN_STALE_OR_FUTURE_DATA')
                    panel=page.locator('#control-help');body=page.locator('#control-help-text')
                    tested=0;seen=set()
                    for workspace in ('portal','risk','research','ai','accounts','todo','help'):
                        page.locator('#tab-'+workspace).click()
                        expect(page.locator('#tab-'+workspace)).to_have_attribute('aria-selected','true')
                        expect(page.locator('#panel-'+workspace)).to_be_visible()
                        if workspace=='ai':
                            expect(page.locator('#aiconnectorstatus')).to_contain_text('have not been checked')
                            expect(page.locator('#aiconnectorstatus')).to_contain_text('No vault lookup')
                        if workspace=='todo':
                            expect(page.locator('#startup-summary')).to_contain_text('Checked ')
                            expect(page.get_by_role('heading',name='Your research engine, ready to explore')).to_be_visible()
                            expect(page.locator('#startup-tasks')).to_contain_text('Why:')
                            expect(page.locator('#startup-check-engine')).to_be_checked()
                            expect(page.locator('#startup-check-payment')).not_to_be_checked()
                            expect(page.locator('#startup-check-payment')).to_be_disabled()
                            page.locator('#release-roadmap-load').click()
                            expect(page.locator('#release-roadmap-content')).to_contain_text('D5 / Secure deposits / NOT_IMPLEMENTED')
                            expect(page.locator('#release-roadmap-content')).to_contain_text('D9 / 1.0 production release / BLOCKED')
                            page.mouse.move(0,0)
                            page.screenshot(path=str(output/f'startup-{width}x{height}.png'),full_page=True)
                        for control in page.locator('button,input,select,textarea').all():
                            if not control.is_visible():continue
                            identity=control.evaluate("e=>{if(!e.dataset.smokeId)e.dataset.smokeId=String(window.__smokeId=(window.__smokeId||0)+1);return e.dataset.smokeId}")
                            if identity in seen:continue
                            seen.add(identity)
                            current_control=dict(id=control.get_attribute('id'),tag=control.evaluate('e=>e.tagName'),workspace=workspace)
                            description=control.get_attribute('data-help')
                            if not description or len(description)<30:raise ValueError('HELP_BROWSER_CONTROL_MISSING_DESCRIPTION')
                            control.hover(force=True);expect(panel).to_be_visible();expect(body).to_have_text(description)
                            data=panel.evaluate('''e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return {position:s.position,background:s.backgroundColor,color:s.color,font_size:parseFloat(s.fontSize),pointer_events:s.pointerEvents,rect:{x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width},viewport_width:innerWidth,viewport_height:innerHeight,scroll_height:e.scrollHeight,client_height:e.clientHeight}}''')
                            validate_geometry(data);tested+=1
                            page.mouse.move(0,0);expect(panel).to_be_hidden()
                    if tested<90:raise ValueError('HELP_BROWSER_CONTROL_COVERAGE_INCOMPLETE')
                    if writes:raise ValueError('HELP_BROWSER_HOVER_SENT_WRITE')
                    page.locator('#tab-portal').click()
                    page.locator('#forward-tier').hover()
                    page.locator('#forward-tier').select_option('solar')
                    page.mouse.move(0,0);expect(panel).to_be_hidden()
                    page.locator('#tab-risk').click()
                    page.locator('#paperbudget').fill('12345')
                    page.locator('#tab-help').click()
                    page.locator('#tab-risk').click()
                    expect(page.locator('#paperbudget')).to_have_value('12345')
                    page.locator('#paperbudget').focus();expect(panel).to_be_visible()
                    page.keyboard.press('Escape');expect(panel).to_be_hidden()
                    page.locator('#tab-ai').click()
                    page.locator('#aikey').fill('PRIVATE-FIXTURE-NOT-A-REAL-KEY')
                    page.locator('#aikey').hover();expect(panel).to_be_visible()
                    if 'PRIVATE-FIXTURE' in body.inner_text():raise ValueError('HELP_BROWSER_INPUT_VALUE_EXPOSED')
                    page.locator('#aikey').fill('');page.mouse.move(0,0)
                    page.locator('#tab-portal').click()
                    page.locator('#forward-start').hover();expect(panel).to_be_visible()
                    page.screenshot(path=str(output/f'help-{width}x{height}.png'))
                    if writes:raise ValueError('HELP_BROWSER_INTERACTION_SENT_WRITE')
                    # All credential/API writes below are intercepted fixtures only.
                    page.locator('#tab-accounts').click()
                    page.locator('#paper-credentials-status').click()
                    expect(page.locator('#paper-credentials-result')).to_contain_text('No saved paper key pair')
                    for ident in ('paper-api-key','paper-api-secret'):
                        page.locator('#'+ident).fill('PRIVATE-PAPER-FIXTURE-12345')
                        page.locator('#'+ident).hover();expect(panel).to_be_visible()
                        if 'PRIVATE-PAPER' in body.inner_text():raise ValueError('HELP_BROWSER_PAPER_SECRET_EXPOSED')
                        page.mouse.move(0,0);expect(panel).to_be_hidden()
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.locator('#paper-credentials-save').click()
                    expect(page.locator('#paper-credentials-result')).to_contain_text('Paper key pair saved locally')
                    expect(page.locator('#paper-api-key')).to_have_value('')
                    expect(page.locator('#paper-api-secret')).to_have_value('')
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.locator('#paper-account-inspect').click()
                    expect(page.locator('#paper-credentials-result')).to_contain_text('Observed USD ceiling 900')
                    expect(page.locator('#paper-credentials-result')).to_contain_text('Reported cash USD 1000')
                    expect(page.locator('#paper-credentials-result')).to_contain_text('BTC total 2 / available 1.9 / Unavailable difference 0.1')
                    expect(page.locator('#paper-credentials-result')).to_contain_text('NOT a verified hold')
                    expect(page.locator('#paper-credentials-result')).to_contain_text('Buy limit principal cap USD 0 / Sell remainder BTC 0')
                    expect(page.locator('#paper-credentials-result')).to_contain_text('NOT verified holds or execution prices')
                    expect(page.locator('#paper-credentials-result')).to_contain_text('Spendable funds NOT verified')
                    expect(page.locator('#paper-observation-download')).to_be_enabled()
                    page.once('dialog',lambda dialog:dialog.accept())
                    with page.expect_download() as downloaded:
                        page.locator('#paper-observation-download').click()
                    download=downloaded.value
                    if download.suggested_filename!='Nexus-Paper-Observation.json':raise ValueError('PAPER_DOWNLOAD_FILENAME')
                    from nexus.portable_paper_observation import loads
                    saved=loads(Path(download.path()).read_bytes())
                    if saved!=paper_observation_fixture()['portable_observation']:raise ValueError('PAPER_DOWNLOAD_CONTENT')
                    if len(writes)!=3:raise ValueError('PAPER_DOWNLOAD_UNEXPECTED_WRITE')
                    page.locator('#paper-account-expected').fill(paper_observation_fixture()['account_id'])
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.locator('#paper-account-paced-inspect').click()
                    expect(page.locator('#paper-credentials-result')).to_contain_text('Local pacing retained')
                    expect(page.locator('#paper-observation-download')).to_be_enabled()
                    page.locator('#paper-pacing-status').click()
                    expect(page.locator('#paper-credentials-result')).to_contain_text('1 / 256 sessions')
                    expect(page.locator('#paper-credentials-result')).to_contain_text('No keys read or broker request')
                    expect(page.locator('#paper-observation-download')).to_be_disabled()
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.locator('#paper-credentials-remove').click()
                    expect(page.locator('#paper-credentials-result')).to_contain_text('No saved paper key pair')
                    expect(page.locator('#paper-observation-download')).to_be_disabled()
                    if writes!=['/api/execution/alpaca-paper/local-credentials/'+action for action in ('status','save','inspect','paced-inspect','pacing-status','remove')]:
                        raise ValueError('UNEXPECTED_PAPER_CONTROL_WRITES')
                    page.mouse.move(0,0);expect(panel).to_be_hidden()
                    page.screenshot(path=str(output/f'paper-credentials-{width}x{height}.png'))
                    if errors or blocked:raise ValueError('HELP_BROWSER_PAGE_ERRORS: '+repr(errors+blocked))
                    reports.append(dict(width=width,height=height,controls_hovered=tested,hover_sent_writes=False,keyboard_escape=True,private_value_isolated=True,matrix_modes=True,matrix_adjustment_inspection=True,hub_readonly_views=True,workspace_tabs=7,automatic_startup_checks=True,tab_switch_preserves_inputs=True,paper_credential_controls=True,managed_read_pacing_controls=True,portable_observation_download=True,offline_export_consistency_checked=True,intercepted_paper_reads_only=True))
                except Exception as exc:
                    diagnostic=dict(viewport=dict(width=width,height=height),control=current_control,error=str(exc),page_errors=errors,blocked=blocked,
                                    events=page.evaluate('window.__helpEvents||[]'))
                    (output/f'failure-{width}x{height}.json').write_text(json.dumps(diagnostic,indent=2),encoding='utf-8')
                    page.screenshot(path=str(output/f'failure-{width}x{height}.png'))
                    raise
                finally:context.close()
        finally:browser.close()
    report=dict(mode='INTERCEPTED_FIXTURE_BROWSER_ACCEPTANCE',passed=True,viewports=reports,
                playwright_version=importlib.metadata.version('playwright'),
                html_sha256=hashlib.sha256(html.encode()).hexdigest(),source_commit=os.environ.get('GITHUB_SHA'),
                broker_connected=False,live_orders_enabled=False,
                limitations='Headless browser and intercepted fixtures only; no real-feed session, native desktop window, clean-machine profile or release approval.')
    (output/'browser-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--channel',help='Optional installed Chromium channel, for example msedge')
    args=parser.parse_args();print(json.dumps(run(args.output,args.channel),indent=2))


if __name__=='__main__':main()
