"""Read-only release contract and conversation recovery reference.

Change through reviewed source commits. There is no runtime update operation.
ROADMAP.md is generated from this module; LAUNCH_DRIVE.md contains implementation history.
"""
from dataclasses import dataclass, asdict

REVISION = '0.54.0'
BRANCH = 'development/windows-portal-foundation'


@dataclass(frozen=True)
class Milestone:
    id: str
    title: str
    status: str
    evidence: str
    remaining: str


MILESTONES = (
    Milestone('M1','Windows packaging and recovery','PARTIAL','Windows test, EXE startup and installer workflows; durable order recovery.','Record clean-machine install, upgrade, uninstall and recovery evidence.'),
    Milestone('M2','Market data and provenance','PARTIAL','Public observation, timestamp/gap checks, historical admission and source diagnostics.','Independent provenance review; missing/revised/conflicting data and outage evidence.'),
    Milestone('M3','Decision speed and risk authority','PARTIAL','Bounded local decision queue; individual Terrestrial/Solar/Universal entry permissions.','Measure full durable path under bursts; verify expiry, overload and permissions throughout autonomy.'),
    Milestone('M4','Order lifecycle and portfolio reconciliation','PARTIAL','Partial/full priced fills, fees, idempotency, cancellation, durable recovery; atomic opt-in paper-ledger linking, partial-position reconstruction, adverse quote-cost assumptions and durable expiry.','Extend interruption/quote-path stress; independently review execution assumptions, existing-account migration and reconciliation.'),
    Milestone('M5','Durable latency and capacity','OPEN','Pure decision benchmark exists; this does not measure broker fill speed.','Validated checkpoints/retention, durable p50/p95/p99, queue pressure and interruption drills at scale.'),
    Milestone('M6','Scientific strategy evidence','PARTIAL','Chronological research, holdout, regime and cost-stress modules.','Reproducible forward evidence, delisting/survivorship review and independent performance audit.'),
    Milestone('M7','Unattended paper operation','OPEN','Observer, risk gates and paper execution components exist.','Integrate monitored buyer/hold/seller loop; restart/network drills and sustained unattended runs.'),
    Milestone('M8','Broker paper integration and operations','OPEN','Provider-neutral design; external adapters remain disconnected.','Alpaca paper first after explicit paper-connection authorization; broker IDs, pacing, eligibility and reconciliation. IBKR paper then Coinbase Advanced expansion.'),
    Milestone('M9','Release acceptance and authorization','OPEN','0.x numbering and release gates enforced.','Independent security/risk review, operator runbook, complete acceptance evidence and explicit owner approval for 1.0. Live-money activation requires separate authorization.'),
)

SETTINGS = (
    ('broad_refresh_seconds',5),('ranking_seconds',15),('ai_candidate_count',30),
    ('adaptive_acceleration',True),('autonomous_paper_first',True),
)
NEXT = (
    'Validate linked-ledger recovery, risk vetoes and reconciliation under repeated interruption.',
    'Extend linked quote-model stress and independently review cost, liquidity and expiry assumptions.',
    'Implement validated durable checkpoints and retention; measure the complete paper path.',
    'Integrate unattended paper buyer/hold/seller operation with all tier and global vetoes.',
    'Finish provider-paper integration, forward evidence, packaging and independent release acceptance.',
)


def snapshot():
    return dict(read_only=True, revision=REVISION, project='Nexus Operative', repository='S4-cyber/Nexus-Operative',
                branch=BRANCH, release_target='1.0.0', current_mode='OFFLINE_PAPER_PRERELEASE',
                settings_scope='TARGET_DESIGN_NOT_EFFECTIVE_RUNTIME', settings=dict(SETTINGS), milestones=[asdict(m) for m in MILESTONES], next_steps=list(NEXT),
                supporting_track='Global opportunity expansion stays secondary to launch blockers.',
                speed_target='Aim for roughly 30+ market moves/second in the long term; internal throughput is not broker acknowledgments or fills.',
                resume='Read AGENTS.md, ROADMAP.md, LAUNCH_DRIVE.md and VERSIONING.md; verify remote branch head, CI and current source before editing.')


def markdown():
    d=snapshot()
    lines=['# Nexus Operative — read-only 1.0 roadmap','',f"Revision: {REVISION}. Branch: `{BRANCH}`.",'',
           'Generated from `nexus/release_roadmap.py`. View in PORTAL or `GET /api/roadmap`. Changes require source review; the application has no roadmap write endpoint.','',
           '| Milestone | Status | Evidence | Remaining acceptance |','| --- | --- | --- | --- |']
    lines += [f'| {m.id}: {m.title} | {m.status} | {m.evidence} | {m.remaining} |' for m in MILESTONES]
    lines += ['','## Standing target settings (verify actual runtime separately)','']+[f'- {key}: {value}' for key,value in SETTINGS]
    lines += ['','## Next sequence','']+[f'{i}. {step}' for i,step in enumerate(NEXT,1)]
    lines += ['','Global expansion is a supporting track. '+d['speed_target'],'','## Resume after a conversation cutoff','',d['resume'],
              '', 'Run `python -m pytest -q`, Python compilation and PORTAL JavaScript validation. Record exact commits and CI outcomes. Keep 0.x until release gates pass and owner approval is explicit. Never describe an unverified gate as complete.','']
    return '\n'.join(lines)


if __name__=='__main__':
    print(markdown(),end='')
