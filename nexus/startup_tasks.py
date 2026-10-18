"""Read-only, evidence-scoped startup checklist. Never initializes an account.

Checkboxes describe configuration/capabilities, not strategy or release approval.
No network probe, payment, model training or automatic repair is performed.
"""
from contextlib import closing
import math
import os
from pathlib import Path
import sqlite3
import time
from nexus import market_hub as hub
from nexus.onboard_council import ENGINE_VERSION
from nexus.state import read_effective_settings
from nexus.provider_research import status as ai_status


def _rows(path, sql):
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=.25)) as db:
        db.set_progress_handler(lambda: 1, 100000)
        try:return db.execute(sql).fetchall()
        except sqlite3.OperationalError as exc:
            if str(exc).startswith('no such table:'):return []
            raise


def checklist(*,portal_active=False):
    root=Path(os.environ.get('NEXUS_PORTABLE_DATA', str(Path.home()/'.nexus-operative')))
    items=[]
    def add(key,title,group,status,detail,tab,target):
        items.append(dict(id=key,title=title,group=group,status=status,checked=status=='COMPLETE',
                          detail=('Nexus could not read this setup state. Try again or review recovery. ' if status=='UNKNOWN' else '')+detail,tab=tab,target=target))
    add('portal','Start Portal before engaging','Engine foundation','COMPLETE' if portal_active else 'NEEDS_SETUP',
        'Start Portal turns on observation and the engine services. It does not engage trades. Once the engine is on, choose Engage Paper separately; stopping Portal stops the paper session.', 'portal','activate')
    add('engine','Built-in intelligence is included','Engine foundation','COMPLETE',
        'Numerical learning models and offline studies are included. No AI account or payment is needed. This checks availability, not investing skill.','ai','builtin-ai')
    add('storage','Save the engine’s memory locally','Engine foundation',
        'COMPLETE' if root.is_dir() and os.access(root,os.W_OK) else 'NEEDS_SETUP',
        'Folder access appears available; this is not a database health check or a test write.' if root.is_dir() and os.access(root,os.W_OK) else 'A writable data folder has not been confirmed. Review System Readiness below.', 'todo','readinesscheck')
    # Read only saved values: load_settings/current/state initialize databases.
    try:
        path=root/'portal_settings.sqlite3'
        if path.exists():
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
                db.set_progress_handler(lambda:1,100000)
                db.execute('BEGIN')
                read_effective_settings(db)
        state='COMPLETE'
    except (OSError,sqlite3.Error,ValueError,TypeError):state='UNKNOWN'
    add('budget','Virtual budget and allocations','Set the engine’s paper risk allowance',state,
        'The built-in starting budget is $10,000 virtual dollars, split 50% / 35% / 15%. Valid saved choices override these defaults. No save is needed to use defaults; change them in Risk if you want. No money is deposited.','risk','paperbudget')
    try:
        tiers=_rows(root/'portal_settings.sqlite3',"SELECT tier,enabled FROM tier_permissions LIMIT 4")
        engaged=any(t in ('terrestrial','solar','universal') and e==1 for t,e in tiers)
        state='COMPLETE' if engaged else 'NEEDS_SETUP'
    except (OSError,sqlite3.Error):state='UNKNOWN'
    add('tier','Paper entry allowance is selected','Set the engine’s paper risk allowance',state,
        'A new unused installation selects Terrestrial automatically; Solar and Universal stay off because the fixed BTC experiment needs just one tier. Saved choices, including OFF, are preserved. Change the selection in Risk only if needed; this box never starts trading.','risk','risk-tier-controls')
    try:
        meta=dict(_rows(root/'paper_portfolio.sqlite3',"SELECT key,value FROM meta WHERE key IN ('initial','cash') LIMIT 2"))
        initial=float(meta.get('initial','nan'));cash=float(meta.get('cash','nan'))
        state='COMPLETE' if math.isfinite(initial) and 0<initial<=1000000 and math.isfinite(cash) and cash>=0 else 'ON_DEMAND'
    except (OSError,sqlite3.Error,ValueError,TypeError):state='UNKNOWN'
    add('portfolio','Virtual account is created when you engage','Set the engine’s paper risk allowance',state,
        'Usually no separate setup is needed: confirming Engage Paper creates and links compatible unused virtual accounting. Existing traded or incompatible accounts require review and are never reset automatically. A saved initial balance and cash tick this box; it does not prove integrity. Manual Initialize Once is optional.','accounts','paperstart')
    try:
        sha=hub.latest_model('BTC-USD',3600);model=hub.read(sha)
        current=(model.get('schema')=='nexus.hub-momentum-model.v1' and model.get('product')=='BTC-USD'
                 and model.get('granularity')==3600 and model.get('dataset_generation')==hub.generation('BTC-USD',3600)
                 and model.get('onboard_council',{}).get('engine_version')==ENGINE_VERSION)
        age=time.time()-model['forecast_as_of']
        state='COMPLETE' if current and 0<=age<=7200 else 'NEEDS_REFRESH'
    except ValueError as exc:
        state={'HUB_NO_TRAINED_MODEL':'NEEDS_SETUP','HUB_MODEL_REQUIRES_REFRESH':'NEEDS_REFRESH'}.get(str(exc),'UNKNOWN')
    except (OSError,sqlite3.Error,KeyError,TypeError,OverflowError,AttributeError):state='UNKNOWN'
    add('learning','Prepare recent BTC hourly history for the built-in AI','Feed the intelligence and trading engine',state,
        'Start Portal uses BTC hourly history and bounded built-in learning automatically. A recent saved model pack ticks this box. Manual Build Hub & Learn is optional; a missing research pack does not block the fixed paper experiment or prove strategy quality.','research','hub-build')
    from nexus.hub_refresh import refresh_worker
    running=refresh_worker.status()['enabled']
    add('refresh','Run bounded learning with the research engine','Feed the intelligence and trading engine','COMPLETE' if running else 'OPTIONAL',
        'Start Portal starts bounded learning with public history selected by default. Uncheck public history first for saved data only. Running does not prove a successful prediction. Stop Portal stops research; Emergency Stop cuts trading only.','research','hub-refresh-start')
    add('quotes','Fetch a public quote when you need current prices','Feed the intelligence and trading engine','ON_DEMAND',
        'Use Fetch Quote in Research. This checklist makes no exchange request and does not claim an internet connection was tested.','research','publicrefresh')
    try:configured=ai_status()['configured'];state='COMPLETE' if configured else 'OPTIONAL'
    except Exception:state='UNKNOWN'
    add('external_ai','Connect external AI only if you want it','Optional intelligence and account checks',state,
        'An explicit configuration observation in this process ticks this optional box; it can be stale. After restart saved vault presence is unknown until an explicit action. This is not a working provider test. This checklist never opens the vault. Built-in AI works without it; explicit external requests may cost money.','ai','aistatus')
    add('paper_read','Optionally inspect an Alpaca paper account','Optional intelligence and account checks','ON_DEMAND',
        'Windows-only local key storage, separately confirmed account reads and a paced BTC asset-field read are available in Accounts. This checklist never opens the vault or contacts Alpaca. An inspection is not broker trading or spendable-funds approval.','accounts','paper-credentials-status')
    add('payment','Securely link funding through the financial provider','Connected trading and continuity — in development','UNAVAILABLE',
        'The production plan uses the financial provider’s secure authorization and funding flow, including bank linking where needed. Bank/payment linking and transfers are not implemented here. Local paper trading uses virtual cash; never enter bank passwords or card details into an AI-key field.','accounts','preview')
    add('broker','Connect a brokerage for external paper orders','Connected trading and continuity — in development','UNAVAILABLE',
        'Broker adapters are still disconnected. Current trades are local simulations; viewing the connection plan does not link an account.','accounts','execution-connectors')
    add('cloud','Keep Nexus running while your computer is off','Connected trading and continuity — in development','PLANNED',
        'Cloud operation is on the roadmap and is not deployed. This installation needs its computer and application running.','todo','release-roadmap-load')
    from nexus.found_markets import worker as found_worker
    found=found_worker.status()
    add('found_markets','Found Markets: optional automatic opportunity research','Discovery and research',
        'COMPLETE' if found['enabled'] else 'OPTIONAL',
        'When selected, Start Portal runs bounded public discovery in the background. A rule-based council compares 24-hour, 30-day and 180-day history and selects up to six fresh research prospects. It cannot add trade permissions or change the fixed BTC policy. Turning it off preserves baseline paper use.',
        'todo','found-markets-enabled')
    explanations={
        'portal':('Required before Engage','Engine running','The current paper executor requires an active Portal. This box reflects the local engine switch only; actual Engage checks accounting, risk and fresh observations again.'),
        'found_markets':('Optional','Opportunity discovery','A capped discovery council narrows research attention as the catalog grows. Historical trends are clues to investigate, not validated future success; missing or stale data removes eligibility.'),
        'engine':('Included','Intelligence','Built-in specialists study market history and compare ideas for the trading engine. Today their output is research; the fixed paper policy makes trade decisions.'),
        'storage':('Needed for saving','Memory and recovery','Saved settings, observations and trade records let the engine remember its state and recover without guessing past trades.'),
        'budget':('Paper configuration','Spending limits','The budget sets the virtual money and allocation limits available to automated paper entries. Live trading will need separately verified spendable funds.'),
        'tier':('Paper configuration','Risk permission','The selected allowance tells the engine which new paper buys it may attempt. Every trade still faces the remaining risk checks; selecting a tier does not engage.'),
        'portfolio':('Created on Engage','Trade accounting','The account ledger tracks cash, holdings and fills so the engine can decide what it owns and what it can safely change during an automated session.'),
        'learning':('Research progress','Market understanding','Saved price history gives the built-in intelligence context for testing patterns. Validated policy integration is still needed before learned signals can direct trades.'),
        'refresh':('Starts with Portal','Automatic updates','Bounded updates collect new public history and refresh research as data changes, reducing manual work while keeping resource use controlled.'),
        'quotes':('When needed','Current prices','Fresh public prices let the paper engine value positions and model entries and exits. Stale or unavailable prices can veto an automated action.'),
        'external_ai':('Optional','Additional intelligence','An optional external AI service can add research commentary. It may cost money and cannot bypass risk limits or directly control current orders.'),
        'paper_read':('Optional','Provider observation','An explicit paper-account inspection helps develop the future broker connection. Reading a balance does not connect order routing or approve spending.'),
        'payment':('Future feature','Account funding','The planned secure funding connection makes provider-held money available for a verified trading account. It is not needed for today’s virtual paper engine.'),
        'broker':('Future feature','Trade execution','The future broker adapter will carry approved buys and sells to a provider and reconcile results. Current automatic trades are local simulations.'),
        'cloud':('Future feature','Unattended continuity','A secure remote worker is needed for automated trading after this computer turns off. Current sessions require the local app and computer to stay running.'),
    }
    for item in items:
        item['setup_scope'],item['trading_role'],item['why']=explanations[item['id']]
        item['necessity']='BASELINE' if item['id'] in ('portal','storage','budget','tier','portfolio','quotes') else 'FUTURE' if item['id'] in ('payment','broker','cloud') else 'OPTIONAL'
        item['required_for_baseline']=item['necessity']=='BASELINE'
    return dict(mode='READ_ONLY_STARTUP_TASKS',product_goal='AUTONOMOUS_TRADING_ENGINE',live_workflow_available=False,paper_workflow_available=True,checked_at=time.time(),items=items,
                summary='Set the risk allowance, Start Portal, then confirm Engage Paper for the automated virtual BTC experiment. The production goal is install, secure account/funding connection, choose risk and Engage. Defaults need no editing on a new installation. Saved choices and recovery blockers are preserved. Optional and future boxes can stay unticked; checks are not trading or release approval.',
                live_orders_enabled=False,money_movement_enabled=False,starts_automatically=False,
                network_requests_made=False)
