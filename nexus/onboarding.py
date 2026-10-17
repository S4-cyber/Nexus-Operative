"""Zero-configuration local capability/readiness inventory.

No outbound network requests, secrets, payment, account access, data writes,
trading or provider connection tests. A missing optional connector is informational
and does not block offline research.
"""
import os
from pathlib import Path
from nexus.provider_research import status as ai_status
from nexus.public_data import PRODUCTS
from nexus.model_adapters import ROLES

def inventory(data_path=None):
    if data_path is None:
        data_path=os.environ.get("NEXUS_PORTABLE_DATA")
    folder=Path(data_path).expanduser() if data_path else (Path.home()/".nexus-operative")
    # Do not create or modify storage during readiness checks.
    storage_exists=folder.is_dir()
    storage_writable=storage_exists and os.access(folder,os.W_OK)
    ai=ai_status()
    return {"mode":"LOCAL_ZERO_CONFIG_CAPABILITY_CHECK",
        "offline_research":{"available":True,"setup_required":False,
            "explanation":"Synthetic studies and deterministic paper research are built in; no account required."},
        "public_market_data":{"supported_instruments":sorted(PRODUCTS),
            "setup_required":False,"live_connection_tested":False,
            "explanation":"Public quote readers need internet access; this local check does not contact exchanges."},
        "paper_storage":{"configured_directory":storage_exists,
            "write_permission_indicated":bool(storage_writable),
            "health_verified":False,
            "explanation":"Directory/permission check only; database integrity and actual writes have not been tested."},
        "optional_ai":{"configured":ai["configured"],"provider_connected":False,
            "setup_required_if_used":True,
            "attempts_remaining":max(0,ai["max_requests_process"]-ai["requests_used_process"]),
            "explanation":"AI requires separate account, possible provider billing and explicit opt-in. No paid request sent."},
        "experimental_paper_forward":{"available":True,"starts_automatically":False,"strategy_validated":False,
            "product":"BTC-USD","requires_active_portal":True,"requires_engaged_tier":True,
            "explanation":"Explicitly started public-quote experiment with modeled liquidity/costs and linked paper accounting. No broker orders."},
        "optional_paper_account_reads":{"available_on_windows":os.name=="nt","starts_automatically":False,
            "credential_status_checked":False,"broker_connected":False,"ready_for_routing":False,
            "explanation":"Accounts offers separate Windows paper key storage and explicitly confirmed account reads. This inventory does not open the vault or contact a broker; no order adapter is connected."},
        "model_roles":list(ROLES),
        "defaults_safety":{"external_ai_enabled_automatically":False,
            "live_orders_enabled":False,"money_movement_enabled":False,
            "research_only":True},
        "onboarding_state":"READY_FOR_OFFLINE_RESEARCH",
        "note":"Capability inventory is not a live-connection test, security audit or launch certification."}

