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


def checklist():
    root=Path(os.environ.get('NEXUS_PORTABLE_DATA', str(Path.home()/'.nexus-operative')))
    items=[]
    def add(key,title,group,status,detail,tab,target):
        items.append(dict(id=key,title=title,group=group,status=status,checked=status=='COMPLETE',
                          detail=('Nexus could not read this setup state. Try again or review recovery. ' if status=='UNKNOWN' else '')+detail,tab=tab,target=target))
    add('engine','Use built-in research and AI','Start here','COMPLETE',
        'Numerical learning models and offline studies are included. No AI account or payment is needed. This checks availability, not investing skill.','ai','builtin-ai')
    add('storage','Check local storage access','Start here',
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
    add('budget','Virtual budget and allocations','Before a paper session',state,
        'The built-in starting budget is $10,000 virtual dollars, split 50% / 35% / 15%. Valid saved choices override these defaults. No save is needed to use defaults; change them in Risk if you want. No money is deposited.','risk','paperbudget')
    try:
        tiers=_rows(root/'portal_settings.sqlite3',"SELECT tier,enabled FROM tier_permissions LIMIT 4")
        engaged=any(t in ('terrestrial','solar','universal') and e==1 for t,e in tiers)
        state='COMPLETE' if engaged else 'NEEDS_SETUP'
    except (OSError,sqlite3.Error):state='UNKNOWN'
    add('tier','Paper entry allowance is selected','Before a paper session',state,
        'A new unused installation selects Terrestrial automatically; Solar and Universal stay off because the fixed BTC experiment needs just one tier. Saved choices, including OFF, are preserved. Change the selection in Risk only if needed; this box never starts trading.','risk','risk-tier-controls')
    try:
        meta=dict(_rows(root/'paper_portfolio.sqlite3',"SELECT key,value FROM meta WHERE key IN ('initial','cash') LIMIT 2"))
        initial=float(meta.get('initial','nan'));cash=float(meta.get('cash','nan'))
        state='COMPLETE' if math.isfinite(initial) and 0<initial<=1000000 and math.isfinite(cash) and cash>=0 else 'ON_DEMAND'
    except (OSError,sqlite3.Error,ValueError,TypeError):state='UNKNOWN'
    add('portfolio','Virtual account is created when you engage','Before a paper session',state,
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
    add('learning','Prepare recent BTC hourly history for the built-in AI','Research setup',state,
        'Start Portal uses BTC hourly history and bounded built-in learning automatically. A recent saved model pack ticks this box. Manual Build Hub & Learn is optional; a missing research pack does not block the fixed paper experiment or prove strategy quality.','research','hub-build')
    from nexus.hub_refresh import refresh_worker
    running=refresh_worker.status()['enabled']
    add('refresh','Run bounded learning with the research engine','Research setup','COMPLETE' if running else 'OPTIONAL',
        'Start Portal starts bounded learning with public history selected by default. Uncheck public history first for saved data only. Running does not prove a successful prediction. Stop Portal stops research; Emergency Stop cuts trading only.','research','hub-refresh-start')
    add('quotes','Fetch a public quote when you need current prices','Research setup','ON_DEMAND',
        'Use Fetch Quote in Research. This checklist makes no exchange request and does not claim an internet connection was tested.','research','publicrefresh')
    try:configured=ai_status()['configured'];state='COMPLETE' if configured else 'OPTIONAL'
    except Exception:state='UNKNOWN'
    add('external_ai','Connect external AI only if you want it','Optional connections',state,
        'An explicit configuration observation in this process ticks this optional box; it can be stale. After restart saved vault presence is unknown until an explicit action. This is not a working provider test. This checklist never opens the vault. Built-in AI works without it; explicit external requests may cost money.','ai','aistatus')
    add('paper_read','Optionally inspect an Alpaca paper account','Optional connections','ON_DEMAND',
        'Windows-only local key storage and separately confirmed account reads are available in Accounts. This checklist never opens the vault or contacts Alpaca. An inspection is not broker trading or spendable-funds approval.','accounts','paper-credentials-status')
    add('payment','Link payment or a bank account','Future connections','UNAVAILABLE',
        'Not required for local paper research. Payment and bank linking are not implemented. Never enter bank or card details into an AI-key field.','accounts','preview')
    add('broker','Connect a brokerage for external paper orders','Future connections','UNAVAILABLE',
        'Broker adapters are still disconnected. Current trades are local simulations; viewing the connection plan does not link an account.','accounts','execution-connectors')
    add('cloud','Keep Nexus running while your computer is off','Future connections','PLANNED',
        'Cloud operation is on the roadmap and is not deployed. This installation needs its computer and application running.','todo','release-roadmap-load')
    explanations={
        'engine':('Included','The local research tools work without buying AI access.'),
        'storage':('Needed for saving','Nexus needs a writable folder to retain settings and paper history.'),
        'budget':('Paper configuration','Virtual cash gives the simulator a starting balance and spending limits.'),
        'tier':('Paper configuration','A selected tier limits new paper buys; selecting one does not start a session.'),
        'portfolio':('Created on Engage','The virtual ledger records simulated cash, holdings and fills.'),
        'learning':('Research progress','Saved history lets the built-in specialists compare patterns; it is separate from trade permission.'),
        'refresh':('Starts with Portal','Bounded refresh keeps research current without repeated manual builds.'),
        'quotes':('When needed','Current public prices let the simulator value holdings and model fills.'),
        'external_ai':('Optional','External AI adds a separate research service and may cost money; built-in tools need no key.'),
        'paper_read':('Optional','Inspecting a provider paper account is separate from the local simulator.'),
        'payment':('Future feature','Local practice uses virtual cash, so no payment setup is needed.'),
        'broker':('Future feature','An external order adapter will need its own setup and acceptance before routing.'),
        'cloud':('Future feature','A secure remote worker is required to operate after this computer turns off.'),
    }
    for item in items:
        item['setup_scope'],item['why']=explanations[item['id']]
    return dict(mode='READ_ONLY_STARTUP_TASKS',checked_at=time.time(),items=items,
                summary='Use Start Portal for research, then separately confirm Engage Paper for the virtual BTC experiment. Defaults need no editing on a new installation. Saved choices and recovery blockers are preserved. Optional and future boxes can stay unticked; checks are not trading or release approval.',
                live_orders_enabled=False,money_movement_enabled=False,starts_automatically=False,
                network_requests_made=False)
