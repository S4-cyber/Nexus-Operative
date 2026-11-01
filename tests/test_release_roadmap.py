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
    assert (root/'ROADMAP.md').read_text(encoding='utf-8')==markdown()
    assert 'release-roadmap-load' in c_html()
    assert "api('/api/roadmap')" in c_html()


def c_html():
    return TestClient(app).get('/portal').text


def test_product_roadmap_explicitly_tracks_funding_autonomy_and_final_acceptance():
    data=snapshot();stages={s['id']:s for s in data['delivery_stages']}
    assert len(stages)==9
    assert stages['D3']['status']=='EXPERIMENTAL_IMPLEMENTED'
    assert stages['D5']['status']=='NOT_IMPLEMENTED' and 'Funding is preview-only' in stages['D5']['progress']
    assert stages['D6']['status']=='BLOCKED' and 'Separate owner authorization' in stages['D6']['remaining']
    assert stages['D9']['status']=='BLOCKED' and 'security/risk review' in stages['D9']['remaining']
    assert set(stages['D9']['depends_on'])==set(stages)-{'D9'}
    prior=set()
    for stage in stages.values():
        assert set(stage['depends_on'])<=prior
        prior.add(stage['id'])
    stages['D9']['depends_on'].clear()
    assert len(snapshot()['delivery_stages'][-1]['depends_on'])==8
    assert 'Secure deposits and available-funds verification' in markdown()


def test_minimal_first_run_is_a_bundled_goal_with_saved_choices_and_separate_engage():
    setup=snapshot()['first_run_setup']
    assert setup['virtual_budget_usd']==10000 and setup['default_entry_tier']=='terrestrial'
    assert setup['collect_public_history'] and setup['built_in_models_included'] and setup['existing_choices_preserved']
    assert not setup['paid_ai_required'] and not setup['external_account_required'] and not setup['starts_trading_automatically']
    assert 'Minimal first-run setup' in markdown() and 'Optional and future boxes may stay unticked' in markdown()
    setup['default_entry_tier']='universal';assert snapshot()['first_run_setup']['default_entry_tier']=='terrestrial'


def test_rough_progress_is_weighted_scoped_reproducible_and_separate_from_acceptance():
    data=snapshot()['progress_estimate'];rows=data['stages']
    assert {r['id'] for r in rows}=={r['id'] for r in snapshot()['delivery_stages']}
    assert sum(r['weight_percent'] for r in rows)==100
    weighted=sum(r['weight_percent']*r['implementation_estimate_percent'] for r in rows)/100
    assert weighted==data['weighted_index_percent']==38
    assert data['implementation_estimate_percent']==40 and data['uncertainty_low_percent']<=40<=data['uncertainty_high_percent']
    assert data['fully_accepted_stages']==0 and data['total_stages']==9 and all(r['acceptance_status']=='OPEN' for r in rows)
    assert all(data[k] is False for k in ('release_ready','release_approved','live_orders_enabled','money_movement_enabled'))
    assert data['estimated_release_date'] is None and data['confidence']=='LOW'
    rows[0]['implementation_estimate_percent']=100;rows.clear()
    assert snapshot()['progress_estimate']['stages'][0]['implementation_estimate_percent']==85
    assert 'Rough implementation progress' in markdown() and 'about 40%' in markdown()


def test_acceptance_plan_covers_stages_and_preserves_separate_evidence_gates():
    data = snapshot()
    plan = data['acceptance_plan']
    stages = {stage['id']: stage for stage in data['delivery_stages']}
    assert plan['read_only'] and plan['next_major_gate'] == 'D4'
    assert [stage['stage_id'] for stage in plan['stages']] == list(stages)
    identities = []
    for stage in plan['stages']:
        expected = [dependency for dependency in stages[stage['stage_id']]['depends_on']
            if stages[dependency]['status'] != 'ACCEPTED']
        assert stage['unaccepted_dependencies'] == expected
        assert len(stage['steps']) >= 3
        for step in stage['steps']:
            identities.append(step['id'])
            assert step['state'] == 'REVIEW_REQUIRED'
            assert step['title'] and step['required_evidence']
            assert f"- [ ] {step['id']} — {step['title']}: {step['required_evidence']}" in markdown()
    assert len(set(identities)) == len(identities)
    assert all(plan[key] is False for key in
        ('release_approved', 'live_orders_enabled', 'money_movement_enabled'))
    paper = next(stage for stage in plan['stages'] if stage['stage_id'] == 'D4')
    assert 'read-only inventory first' in paper['steps'][0]['required_evidence']
    assert 'separately scoped saved write credentials' in paper['steps'][2]['required_evidence']


def test_acceptance_plan_public_view_cannot_mutate_gates_or_later_responses():
    client = TestClient(app)
    first = client.get('/api/roadmap').json()['acceptance_plan']
    first['live_orders_enabled'] = True
    first['stages'][0]['steps'][0]['state'] = 'ACCEPTED'
    first['stages'][-1]['unaccepted_dependencies'].clear()
    first['stages'][0]['steps'].clear()
    later = client.get('/api/roadmap').json()['acceptance_plan']
    assert not later['live_orders_enabled']
    assert later['stages'][0]['steps'][0]['state'] == 'REVIEW_REQUIRED'
    assert len(later['stages'][-1]['unaccepted_dependencies']) == 8
    for method in ('post', 'put', 'patch', 'delete'):
        assert client.request(method, '/api/roadmap', json={'acceptance_plan': first}).status_code == 405
    assert snapshot()['progress_estimate']['fully_accepted_stages'] == 0


def test_discovery_roadmap_scopes_durable_budget_without_provider_claim():
    discovery=snapshot()['opportunity_discovery']
    assert discovery['requests_per_hour_shared_local_data_root']==36
    assert discovery['durable_request_admissions'] is True
    assert discovery['provider_or_ip_wide_budget_verified'] is False
    assert discovery['execution_integration'] is False


def test_actual_progress_controller():
    import shutil,subprocess,pytest
    node=shutil.which('node')
    if not node:pytest.skip('Node required for actual controller')
    result=subprocess.run([node,str(Path(__file__).resolve().parents[1]/'tools/release_progress_smoke.cjs')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
