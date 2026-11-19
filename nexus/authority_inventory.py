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


def snapshot(application):
    """Bind the declared authority routes to this exact application."""
    available = set()
    for route in application.routes:
        path = getattr(route, "path", None)
        for method in getattr(route, "methods", ()) or ():
            if method not in ("HEAD", "OPTIONS") and isinstance(path, str):
                available.add((method, path))
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
    return dict(
        mode="READ_ONLY_SOURCE_DECLARED_ADMISSION_AUTHORITY_INVENTORY",
        read_only=True, route_bindings_verified=True,
        source_declaration_complete=True, authorities=rows,
        authority_count=len(rows), authority_ids=[row["id"] for row in rows],
        runtime_engagement_default="OFF", external_ai_default="OFF",
        broker_order_method_present=False, bank_transfer_method_present=False,
        automatic_resume_present=False, learned_policy_promotion_present=False,
        **{flag: False for flag in AUTHORITY_FLAGS},
        limitations=("Source-declared route and gate inventory only. It does not prove runtime, "
            "broker, operating-system or independent acceptance and grants no permission."),
    )
