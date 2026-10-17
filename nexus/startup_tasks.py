"""Read-only, evidence-scoped startup checklist. Never initializes an account.

Checkboxes describe configuration/capabilities, not strategy or release approval.
No network probe, payment, model training or automatic repair is performed.
"""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from nexus import market_hub as hub
from nexus.onboard_council import ENGINE_VERSION
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
        saved=dict(_rows(root/'portal_settings.sqlite3',"SELECT key,value FROM configuration WHERE key IN ('paper_budget','terrestrial_allocation','solar_allocation','universal_allocation') LIMIT 4"))
        values={k:json.loads(v) for k,v in saved.items()}
        budget=values.get('paper_budget');alloc=[values.get(k+'_allocation') for k in ('terrestrial','solar','universal')]
        valid=(type(budget) in (int,float) and math.isfinite(budget) and 0<budget<=1000000
               and all(type(x) is int and 0<=x<=100 for x in alloc) and sum(alloc)==100)
        state='COMPLETE' if valid else 'NEEDS_SETUP'
    except (OSError,sqlite3.Error,ValueError,TypeError):state='UNKNOWN'
    add('budget','Save your virtual budget and allocations','Before a paper session',state,
        'Choose virtual dollars and percentages, then Save Allocations. Only valid saved settings tick this box; no money is deposited.','risk','paperbudget')
    try:
        tiers=_rows(root/'portal_settings.sqlite3',"SELECT tier,enabled FROM tier_permissions LIMIT 4")
        engaged=any(t in ('terrestrial','solar','universal') and e==1 for t,e in tiers)
        state='COMPLETE' if engaged else 'NEEDS_SETUP'
    except (OSError,sqlite3.Error):state='UNKNOWN'
    add('tier','Choose an entry risk tier','Before a paper session',state,
        'Engage the tier you intend to use for experimental paper entries. This check observes saved switches and never changes them.','risk','risk-tier-controls')
    try:
        meta=dict(_rows(root/'paper_portfolio.sqlite3',"SELECT key,value FROM meta WHERE key IN ('initial','cash') LIMIT 2"))
        initial=float(meta.get('initial','nan'));cash=float(meta.get('cash','nan'))
        state='COMPLETE' if math.isfinite(initial) and 0<initial<=1000000 and math.isfinite(cash) and cash>=0 else 'NEEDS_SETUP'
    except (OSError,sqlite3.Error,ValueError,TypeError):state='UNKNOWN'
    add('portfolio','Create a virtual account, or review an existing one','Before a paper session',state,
        'A saved initial balance and cash tick this setup box. Reconcile and Audit Ledger are separate checks; this does not prove integrity or compatibility with the runner.','accounts','paperstart')
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
        'Research → choose BTC-USD and One hour → Build Hub & Learn. A current-engine, unchanged-generation, recent saved pack ticks this box. It does not approve a strategy or gate the fixed paper experiment.','research','hub-build')
    from nexus.hub_refresh import refresh_worker
    running=refresh_worker.status()['enabled']
    add('refresh','Run bounded learning with the research engine','Research setup','COMPLETE' if running else 'OPTIONAL',
        'Start Portal starts bounded learning with public history selected by default. Uncheck public history first for saved data only. Running does not prove a successful prediction. Stop Portal stops research; Emergency Stop cuts trading only.','research','hub-refresh-start')
    add('quotes','Fetch a public quote when you need current prices','Research setup','ON_DEMAND',
        'Use Fetch Quote in Research. This checklist makes no exchange request and does not claim an internet connection was tested.','research','publicrefresh')
    try:configured=ai_status()['configured'];state='COMPLETE' if configured else 'OPTIONAL'
    except Exception:state='UNKNOWN'
    add('external_ai','Connect external AI only if you want it','Optional connections',state,
        'A saved local configuration ticks this optional box, not a working provider test. Built-in AI works without it; explicit external requests may cost money.','ai','aistatus')
    add('payment','Link payment or a bank account','Future connections','UNAVAILABLE',
        'Not required for local paper research. Payment and bank linking are not implemented. Never enter bank or card details into an AI-key field.','accounts','preview')
    add('broker','Connect a brokerage for external paper orders','Future connections','UNAVAILABLE',
        'Broker adapters are still disconnected. Current trades are local simulations; viewing the connection plan does not link an account.','accounts','execution-connectors')
    add('cloud','Keep Nexus running while your computer is off','Future connections','PLANNED',
        'Cloud operation is on the roadmap and is not deployed. This installation needs its computer and application running.','todo','release-roadmap-load')
    return dict(mode='READ_ONLY_STARTUP_TASKS',checked_at=time.time(),items=items,
                summary='Checkboxes are automatic observations, not permissions or release approval. Optional and future connections do not block offline research.',
                live_orders_enabled=False,money_movement_enabled=False,starts_automatically=False,
                network_requests_made=False)
