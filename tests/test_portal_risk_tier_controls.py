"""Regression coverage for main-PORTAL tier engagement mirrors.

The buttons on Portal and Risk pages share one backend permission record.
Neither page may bypass entry gating. Disengaging must not force liquidation.
"""
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.tier_permissions import current,configure,require_entry
import pytest

def test_portal_and_risk_views_share_real_backend_controls():
    html=TestClient(app).get("/portal").text
    assert html.count('id="tier-engagement"')==1
    assert html.count('id="portal-tier-engagement"')==1
    assert html.index('id="portal-tier-engagement"')<html.index('id="tier-engagement"')
    assert 'id="portal-tier-permission-status"' in html
    assert "const riskTargets=[['tier-engagement','tier-permission-status'],['portal-tier-engagement','portal-tier-permission-status']]" in html
    assert "await api('/api/tiers/'+policy.tier+'/permission'" in html
    assert "button.setAttribute('aria-pressed',String(policy.enabled))" in html
    assert 'risk-reducing SELL exits' in html
    assert html.count("<script>")==html.count("</script>")==1

def test_independent_engagement_changes_entry_decisions(tmp_path,monkeypatch):
    monkeypatch.setenv("NEXUS_PORTABLE_DATA",str(tmp_path))
    current()
    # Unused-install default: only Terrestrial active.
    require_entry("terrestrial",20)
    with pytest.raises(ValueError,match="RISK_TIER_DISENGAGED"):
        require_entry("solar",20)
    configure("solar",True)
    require_entry("solar",20)
    configure("terrestrial",False)
    with pytest.raises(ValueError,match="RISK_TIER_DISENGAGED"):
        require_entry("terrestrial",20)
    require_entry("solar",20)
    with pytest.raises(ValueError,match="RISK_TIER_DISENGAGED"):
        require_entry("universal",20)
    configure("solar",False)
    assert all(not x["enabled"] for x in current()["tiers"])
