from pathlib import Path
from fastapi.testclient import TestClient
from nexus.app import app
from nexus.release_roadmap import snapshot, markdown


def test_bundled_reference_is_read_only_and_independent():
    c=TestClient(app)
    data=c.get('/api/roadmap').json()
    assert data['read_only'] is True
    assert data['settings']['broad_refresh_seconds']==5
    assert data['settings']['ranking_seconds']==15
    assert data['settings']['ai_candidate_count']==30
    assert len(data['milestones'])==9
    for method in ('post','put','patch','delete'):
        assert getattr(c,method)('/api/roadmap').status_code==405
    data['milestones'][0]['status']='COMPLETE'
    assert snapshot()['milestones'][0]['status']=='PARTIAL'


def test_checked_in_roadmap_matches_code_and_portal_links_it():
    root=Path(__file__).resolve().parents[1]
    assert (root/'ROADMAP.md').read_text()==markdown()
    assert 'release-roadmap-load' in c_html()
    assert "api('/api/roadmap')" in c_html()


def c_html():
    return TestClient(app).get('/portal').text
