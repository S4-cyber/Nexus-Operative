"""Read-only admission-authority map for independent release review.

The inventory describes source-controlled gates; it grants no permission and
performs no credential, provider, storage or broker I/O.
"""
from __future__ import annotations


AUTHORITY_FLAGS = (
    "broker_connected", "live_orders_enabled", "money_movement_enabled",
    "release_approved", "spendable_funds_verified",
)


AUTHORITIES = (
    dict(id="TIER_PERMISSION", scope="LOCAL_SIMULATION_ENTRY_ONLY",
         routes=(("PUT", "/api/tiers/{tier}/permission"),), default="OFF",
         gates=("EXPLICIT_BOOLEAN_SELECTION", "PERSISTED_SETTINGS_REVIEW"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=False),
    dict(id="LOCAL_PAPER_ENTRY", scope="LOCAL_SYNTHETIC_PORTFOLIO",
         routes=(("POST", "/api/paper/portfolio/buy"),), default="DENIED",
         gates=("PORTAL_NOT_PAUSED", "EMERGENCY_CUT_CLEAR",
                "TIER_PERMISSION_ON", "TIER_NOTIONAL_CAP", "FRESH_PUBLIC_QUOTE",
                "POST_LOCK_GATE_RECHECK"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=False),
    dict(id="LOCAL_PAPER_REDUCING_EXIT", scope="LOCAL_SYNTHETIC_PORTFOLIO",
         routes=(("POST", "/api/paper/portfolio/sell"),), default="NO_POSITION_NOOP",
         gates=("EXISTING_LONG_ONLY", "FRESH_PUBLIC_QUOTE", "ATOMIC_LEDGER_REDUCTION"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=True),
    dict(id="EXPERIMENTAL_FORWARD_SESSION", scope="PUBLIC_QUOTE_LOCAL_PAPER",
         routes=(("POST", "/api/paper/forward/start"),
                 ("POST", "/api/paper/forward/stop")), default="STOPPED",
         gates=("EXPLICIT_START", "TIER_PERMISSION_ON", "IMMUTABLE_POLICY",
                "EMERGENCY_CUT_CLEAR", "SINGLE_LOCAL_OWNER", "BOUNDED_STORAGE"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=True),
    dict(id="SIMULATED_ORDER_INTENT", scope="LOCAL_DISCONNECTED_SIMULATION",
         routes=(("POST", "/api/execution/intent/simulate"),
                 ("POST", "/api/execution/intent/{client_order_id}/cancel")),
         default="NO_INTENT", gates=("EXPLICIT_REQUEST", "LOCAL_RISK_SCREEN",
                "EMERGENCY_CUT_CLEAR", "DURABLE_IDEMPOTENT_JOURNAL"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=True),
    dict(id="OPTIONAL_AI_ADVISORY", scope="UNTRUSTED_RESEARCH_TEXT_ONLY",
         routes=(("POST", "/api/paper/ai-provider/{role}/research"),
                 ("POST", "/api/paper/ai-provider/public-preview/send")),
         default="OFF", gates=("EXPLICIT_REQUEST", "EXACT_CREDENTIAL_SOURCE",
                "BOUNDED_PROCESS_CALLS", "STRICT_RESPONSE_SCHEMA", "NO_TOOLS"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=False),
    dict(id="BROKER_PAPER_READ", scope="ACCOUNT_READS_ONLY_NO_ORDER_METHOD",
         routes=(("POST", "/api/execution/alpaca-paper/local-credentials/inspect"),
                 ("POST", "/api/execution/alpaca-paper/local-credentials/paced-inspect"),
                 ("POST", "/api/execution/alpaca-paper/local-credentials/asset-rules")),
         default="OFF", gates=("WINDOWS_VAULT", "EXPLICIT_CONFIRMATION",
                "EXPECTED_PAPER_ACCOUNT", "LOCAL_PACING", "READ_ONLY_METHODS"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=False),
    dict(id="PAPER_SETTINGS", scope="LOCAL_SIMULATION_LIMITS",
         routes=(("PUT", "/api/paper/settings"),), default="SAFE_DEFAULTS",
         gates=("EXPLICIT_SAVE", "BOUNDED_EXACT_SCHEMA", "DURABLE_READBACK"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=False),
    dict(id="EMERGENCY_CUT", scope="LOCAL_PROCESS_AND_DURABLE_ORDER_VETO",
         routes=(("POST", "/api/simulate/halt"),
                 ("POST", "/api/simulate/halt/reset")), default="CLEAR",
         gates=("CUT_CAN_ONLY_RESTRICT", "INDEPENDENT_CONTROL_LANE",
                "RESET_EXPLICIT_CONFIRMATION", "RESET_STAYS_DISENGAGED"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=True),
    dict(id="FUNDING_PREVIEW", scope="VALIDATION_ONLY_NO_BANK_CONNECTOR",
         routes=(("POST", "/api/funding/preview"),), default="PREVIEW_ONLY",
         gates=("EXACT_BOUNDED_INPUT", "NO_BANK_LINK", "NO_TRANSFER_METHOD"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=False),
    dict(id="LEARNED_MODEL_OUTPUT", scope="ADVISORY_RESEARCH_ONLY",
         routes=(("POST", "/api/paper/model-council"),
                 ("POST", "/api/paper/council")), default="NO_PROMOTION_AUTHORITY",
         gates=("ADVISORY_ONLY", "NO_POLICY_WRITE", "NO_EXECUTION_OWNER"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=False),
    dict(id="PRODUCTION_RELEASE", scope="OWNER_AND_INDEPENDENT_REVIEW_OUTSIDE_APP",
         routes=(), default="NOT_APPROVED",
         gates=("ALL_NINE_STAGES_ACCEPTED", "EXPLICIT_OWNER_DECISION"),
         automatic_start=False, automatic_resume=False,
         reducing_exit_available_during_cut=False),
)


# Complete reviewed inventory of every mutating HTTP boundary.  A new POST,
# PUT, PATCH or DELETE route is a release-relevant authority change even when
# it is not intended to trade or move funds, so additions must fail closed
# until this exact declaration is reviewed with them.
MUTATING_ROUTES = (
    ("PUT", "/api/paper/settings", "PAPER_CONFIGURATION", "LOCAL_ONLY"),
    ("POST", "/api/paper/scenario", "PAPER_RESEARCH", "LOCAL_ONLY"),
    ("POST", "/api/paper/replay", "PAPER_RESEARCH", "LOCAL_ONLY"),
    ("POST", "/api/execution/alpaca-paper/local-credentials/save", "PAPER_CREDENTIAL_VAULT", "LOCAL_SECRET_STORAGE_ONLY"),
    ("POST", "/api/execution/alpaca-paper/local-credentials/remove", "PAPER_CREDENTIAL_VAULT", "LOCAL_SECRET_STORAGE_ONLY"),
    ("POST", "/api/execution/alpaca-paper/local-credentials/status", "PAPER_CREDENTIAL_VAULT", "LOCAL_SECRET_STORAGE_ONLY"),
    ("POST", "/api/execution/alpaca-paper/local-credentials/inspect", "BROKER_PAPER_READ", "BROKER_PAPER_READ_ONLY"),
    ("POST", "/api/execution/alpaca-paper/local-credentials/asset-rules", "BROKER_PAPER_READ", "BROKER_PAPER_READ_ONLY"),
    ("POST", "/api/execution/alpaca-paper/local-credentials/paced-inspect", "BROKER_PAPER_READ", "BROKER_PAPER_READ_ONLY"),
    ("POST", "/api/execution/alpaca-paper/local-credentials/pacing-status", "BROKER_PAPER_READ", "LOCAL_ONLY"),
    ("POST", "/api/paper/ai-provider/vault/connect", "AI_CREDENTIAL_VAULT", "LOCAL_SECRET_STORAGE_ONLY"),
    ("POST", "/api/paper/ai-provider/vault/disconnect", "AI_CREDENTIAL_VAULT", "LOCAL_SECRET_STORAGE_ONLY"),
    ("POST", "/api/paper/ai-provider/public-preview/send", "AI_ADVISORY", "ADVISORY_ONLY"),
    ("POST", "/api/paper/ai-provider/{role}/research", "AI_ADVISORY", "ADVISORY_ONLY"),
    ("POST", "/api/paper/model-council", "MODEL_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/paper/evidence-audit", "MODEL_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/paper/council", "MODEL_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/paper/tournament", "MODEL_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/paper/walk-forward", "MODEL_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/paper/replay/record", "MODEL_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/paper/risk-screen", "MODEL_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/paper/stress", "MODEL_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/candle-research/{product}/regime-stress", "MARKET_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/candle-research/{product}/execution-stress", "MARKET_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/candle-research/{product}/cost-sensitivity", "MARKET_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/candle-research/{product}/evaluate", "MARKET_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/historical-candles/{product}/collect", "PUBLIC_DATA_RESEARCH", "PUBLIC_READ_ONLY"),
    ("POST", "/api/research-ticks/{product}/capture", "PUBLIC_DATA_RESEARCH", "PUBLIC_READ_ONLY"),
    ("POST", "/api/market-evidence/{product}/capture", "PUBLIC_DATA_RESEARCH", "PUBLIC_READ_ONLY"),
    ("POST", "/api/paper/portfolio/snapshot", "LOCAL_PAPER_PORTFOLIO", "LOCAL_ONLY"),
    ("POST", "/api/paper/portfolio/init", "LOCAL_PAPER_PORTFOLIO", "LOCAL_ONLY"),
    ("POST", "/api/paper/portfolio/buy", "LOCAL_PAPER_PORTFOLIO", "LOCAL_PAPER_ORDER"),
    ("POST", "/api/paper/portfolio/sell", "LOCAL_PAPER_PORTFOLIO", "LOCAL_PAPER_ORDER"),
    ("POST", "/api/expansion/{avenue}/screen", "MARKET_RESEARCH", "ADVISORY_ONLY"),
    ("POST", "/api/paper/forward/start", "LOCAL_PAPER_FORWARD", "LOCAL_PAPER_ORDER"),
    ("POST", "/api/paper/forward/stop", "LOCAL_PAPER_FORWARD", "LOCAL_PAPER_ORDER"),
    ("POST", "/api/execution/operation-drill", "LOCAL_OPERATIONS", "LOCAL_ONLY"),
    ("POST", "/api/execution/durable-benchmark", "LOCAL_OPERATIONS", "LOCAL_ONLY"),
    ("POST", "/api/execution/archive-terminal", "LOCAL_OPERATIONS", "LOCAL_ONLY"),
    ("POST", "/api/execution/intent/simulate", "LOCAL_SIMULATED_EXECUTION", "LOCAL_PAPER_ORDER"),
    ("POST", "/api/execution/intent/{client_order_id}/cancel", "LOCAL_SIMULATED_EXECUTION", "LOCAL_PAPER_ORDER"),
    ("POST", "/api/matrix/hub/refresh/start", "LOCAL_MATRIX", "LOCAL_ONLY"),
    ("POST", "/api/matrix/hub/refresh/stop", "LOCAL_MATRIX", "LOCAL_ONLY"),
    ("POST", "/api/matrix/hub/prepare", "LOCAL_MATRIX", "LOCAL_ONLY"),
    ("POST", "/api/matrix/hub/build", "LOCAL_MATRIX", "LOCAL_ONLY"),
    ("POST", "/api/matrix/account/observe", "LOCAL_MATRIX", "LOCAL_ONLY"),
    ("PUT", "/api/tiers/{tier}/permission", "RISK_PERMISSION", "LOCAL_ONLY"),
    ("POST", "/api/research/found-markets/control", "PUBLIC_DATA_RESEARCH", "PUBLIC_READ_ONLY"),
    ("POST", "/api/portal", "PORTAL_CONTROL", "LOCAL_ONLY"),
    ("POST", "/api/simulate/halt", "EMERGENCY_CONTROL", "LOCAL_EMERGENCY_ONLY"),
    ("POST", "/api/simulate/halt/reset", "EMERGENCY_CONTROL", "LOCAL_EMERGENCY_ONLY"),
    ("POST", "/api/funding/preview", "FUNDING_PREVIEW", "FUNDING_PREVIEW_ONLY"),
)

MUTATING_METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))
EXTERNAL_CAPABILITIES = frozenset(("BANK_TRANSFER", "BROKER_ORDER", "CLOUD_CONTROL"))
AUTHORITY_CLASSES = frozenset((
    "AI_ADVISORY", "AI_CREDENTIAL_VAULT", "BANK_TRANSFER",
    "BROKER_ORDER", "BROKER_PAPER_READ", "CLOUD_CONTROL",
    "EMERGENCY_CONTROL", "FUNDING_PREVIEW", "LOCAL_MATRIX",
    "LOCAL_OPERATIONS", "LOCAL_PAPER_FORWARD", "LOCAL_PAPER_PORTFOLIO",
    "LOCAL_SIMULATED_EXECUTION", "MARKET_RESEARCH", "MODEL_RESEARCH",
    "PAPER_CONFIGURATION", "PAPER_CREDENTIAL_VAULT", "PAPER_RESEARCH",
    "PORTAL_CONTROL", "PUBLIC_DATA_RESEARCH", "RISK_PERMISSION",
))
CAPABILITIES = frozenset((
    "ADVISORY_ONLY", "BANK_TRANSFER", "BROKER_ORDER",
    "BROKER_PAPER_READ_ONLY", "CLOUD_CONTROL", "FUNDING_PREVIEW_ONLY",
    "LOCAL_EMERGENCY_ONLY", "LOCAL_ONLY", "LOCAL_PAPER_ORDER",
    "LOCAL_SECRET_STORAGE_ONLY", "PUBLIC_READ_ONLY",
))
EXTERNAL_TRANSPORT_CAPABILITIES = frozenset((
    "AI_ADVISORY_POST", "BROKER_PAPER_ORDER_POST",
))
EXTERNAL_TRANSPORT_BOUNDARIES = (
    dict(id="ALPACA_PAPER_SINGLE_ATTEMPT_ORDER_POST",
         module="nexus.alpaca_paper_submission", method="POST",
         target="https://paper-api.alpaca.markets/v2/orders",
         capability="BROKER_PAPER_ORDER_POST", default="OFF_UNROUTED",
         app_route_bound=False, explicit_confirmation_required=True,
         automatic_start=False, automatic_resume=False),
    dict(id="OPENAI_RESEARCH_ADVISORY_POST",
         module="nexus.provider_research", method="POST",
         target="https://api.openai.com/v1/chat/completions",
         capability="AI_ADVISORY_POST", default="OFF",
         app_route_bound=True, explicit_confirmation_required=True,
         automatic_start=False, automatic_resume=False),
)
MUTATING_ROUTE_PUBLIC_KEYS = frozenset((
    "method", "path", "authority_class", "capability"))
EXTERNAL_TRANSPORT_BOUNDARY_PUBLIC_KEYS = frozenset((
    "id", "module", "method", "target", "capability", "default",
    "app_route_bound", "explicit_confirmation_required",
    "automatic_start", "automatic_resume", *AUTHORITY_FLAGS))
AUTHORITY_PUBLIC_KEYS = frozenset((
    "id", "scope", "routes", "default", "gates", "automatic_start",
    "automatic_resume", "reducing_exit_available_during_cut",
    "learned_policy_promotion", *AUTHORITY_FLAGS))
REPORT_PUBLIC_KEYS = frozenset((
    "mode", "read_only", "route_bindings_verified",
    "source_declaration_complete", "authorities", "authority_count",
    "authority_ids", "mutating_routes", "mutating_route_count",
    "external_transport_boundaries_verified", "external_transport_boundaries",
    "external_transport_boundary_count",
    "runtime_engagement_default", "external_ai_default",
    "broker_order_route_present", "bank_transfer_route_present",
    "cloud_control_route_present", "broker_order_transport_present",
    "bank_transfer_transport_present", "cloud_control_transport_present",
    "ai_advisory_transport_present", "automatic_resume_present",
    "learned_policy_promotion_present", "limitations", *AUTHORITY_FLAGS))


def _reviewed_mutating_routes():
    rows=[];bindings=[]
    for declaration in MUTATING_ROUTES:
        if (type(declaration) is not tuple or len(declaration)!=4 or
                any(type(value) is not str for value in declaration)):
            raise ValueError("ADMISSION_AUTHORITY_MUTATING_ROUTE_DECLARATION_INVALID")
        method,path,authority_class,capability=declaration
        if (method not in MUTATING_METHODS or not path.startswith("/api/") or
                authority_class not in AUTHORITY_CLASSES or
                capability not in CAPABILITIES):
            raise ValueError("ADMISSION_AUTHORITY_MUTATING_ROUTE_DECLARATION_INVALID")
        binding=(method,path)
        if binding in bindings:
            raise ValueError("ADMISSION_AUTHORITY_MUTATING_ROUTE_DECLARATION_DUPLICATE")
        bindings.append(binding)
        rows.append(dict(method=method,path=path,
            authority_class=authority_class,capability=capability))
    return rows,set(bindings)


def _reviewed_external_boundaries():
    """Bind closed declarations to the two credentialed POST targets in source."""
    from nexus import alpaca_paper_submission, provider_research
    expected = {
        "ALPACA_PAPER_SINGLE_ATTEMPT_ORDER_POST": (
            "nexus.alpaca_paper_submission", "POST",
            alpaca_paper_submission.ORDER_URL, "BROKER_PAPER_ORDER_POST"),
        "OPENAI_RESEARCH_ADVISORY_POST": (
            "nexus.provider_research", "POST",
            provider_research.ENDPOINT, "AI_ADVISORY_POST"),
    }
    rows=[];identifiers=[]
    for declaration in EXTERNAL_TRANSPORT_BOUNDARIES:
        if type(declaration) is not dict or frozenset(declaration)!=(
                EXTERNAL_TRANSPORT_BOUNDARY_PUBLIC_KEYS-set(AUTHORITY_FLAGS)):
            raise ValueError("ADMISSION_AUTHORITY_EXTERNAL_BOUNDARY_DECLARATION_INVALID")
        identifier=declaration.get("id")
        if identifier in identifiers:
            raise ValueError("ADMISSION_AUTHORITY_EXTERNAL_BOUNDARY_DECLARATION_DUPLICATE")
        identifiers.append(identifier)
        actual=(declaration.get("module"),declaration.get("method"),
            declaration.get("target"),declaration.get("capability"))
        if (expected.get(identifier)!=actual or
                declaration.get("capability") not in EXTERNAL_TRANSPORT_CAPABILITIES or
                declaration.get("default") not in ("OFF", "OFF_UNROUTED") or
                type(declaration.get("app_route_bound")) is not bool or
                declaration.get("explicit_confirmation_required") is not True or
                declaration.get("automatic_start") is not False or
                declaration.get("automatic_resume") is not False):
            raise ValueError("ADMISSION_AUTHORITY_EXTERNAL_BOUNDARY_DECLARATION_INVALID")
        rows.append({**declaration,**{flag:False for flag in AUTHORITY_FLAGS}})
    if set(identifiers)!=set(expected):
        raise ValueError("ADMISSION_AUTHORITY_EXTERNAL_BOUNDARY_INVENTORY_MISMATCH")
    return rows


def snapshot(application):
    """Bind the declared authority routes to this exact application."""
    available = set()
    for route in application.routes:
        path = getattr(route, "path", None)
        for method in getattr(route, "methods", ()) or ():
            if method not in ("HEAD", "OPTIONS") and isinstance(path, str):
                available.add((method, path))
    mutating_rows,reviewed_mutating=_reviewed_mutating_routes()
    external_rows=_reviewed_external_boundaries()
    runtime_mutating={binding for binding in available if binding[0] in MUTATING_METHODS}
    if runtime_mutating != reviewed_mutating:
        raise ValueError("ADMISSION_AUTHORITY_MUTATING_ROUTE_INVENTORY_MISMATCH")
    declared = {route for authority in AUTHORITIES for route in authority["routes"]}
    missing = declared - available
    if missing:
        raise ValueError("ADMISSION_AUTHORITY_ROUTE_INVENTORY_MISMATCH")
    rows = []
    for authority in AUTHORITIES:
        row = dict(authority)
        row["routes"] = [dict(method=method, path=path)
                         for method, path in authority["routes"]]
        row.update({flag: False for flag in AUTHORITY_FLAGS})
        row["learned_policy_promotion"] = False
        rows.append(row)
    capabilities={row["capability"] for row in mutating_rows}
    external_capabilities={row["capability"] for row in external_rows}
    return dict(
        mode="READ_ONLY_SOURCE_DECLARED_ADMISSION_AUTHORITY_INVENTORY",
        read_only=True, route_bindings_verified=True,
        source_declaration_complete=True, authorities=rows,
        authority_count=len(rows), authority_ids=[row["id"] for row in rows],
        mutating_routes=mutating_rows,mutating_route_count=len(mutating_rows),
        external_transport_boundaries_verified=True,
        external_transport_boundaries=external_rows,
        external_transport_boundary_count=len(external_rows),
        runtime_engagement_default="OFF", external_ai_default="OFF",
        broker_order_route_present="BROKER_ORDER" in capabilities,
        bank_transfer_route_present="BANK_TRANSFER" in capabilities,
        cloud_control_route_present="CLOUD_CONTROL" in capabilities,
        broker_order_transport_present="BROKER_PAPER_ORDER_POST" in external_capabilities,
        bank_transfer_transport_present="BANK_TRANSFER" in external_capabilities,
        cloud_control_transport_present="CLOUD_CONTROL" in external_capabilities,
        ai_advisory_transport_present="AI_ADVISORY_POST" in external_capabilities,
        automatic_resume_present=False, learned_policy_promotion_present=False,
        **{flag: False for flag in AUTHORITY_FLAGS},
        limitations=("Source-declared route, gate and credentialed external-POST inventory only. "
            "Transport presence is not route exposure, configuration, provider/account acceptance, "
            "funding/live/cloud readiness or permission. The dormant broker-paper boundary remains "
            "unrouted from the application and every external authority flag remains false."),
    )
