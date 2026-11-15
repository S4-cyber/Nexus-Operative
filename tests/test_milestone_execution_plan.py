"""Read-only milestone task execution plan invariants."""
from nexus.milestone_execution_plan import execution_plan, next_work_batches, markdown_lines
from nexus.release_roadmap import snapshot, markdown, progress_estimate


def test_all_nine_milestones_have_five_distinct_tasks_and_evidence():
    groups = execution_plan()
    assert [group["id"] for group in groups] == [f"M{i}" for i in range(1, 10)]
    ids = [task["id"] for group in groups for task in group["tasks"]]
    assert len(ids) == len(set(ids)) == 45
    for group in groups:
        assert len(group["tasks"]) == 5
        assert group["status"] == "REVIEW_REQUIRED"
        for task in group["tasks"]:
            assert task["state"] == "REVIEW_REQUIRED"
            assert task["required_evidence"]
            assert task["mode"] in ("EXTERNAL_OR_NATIVE_GATE", "OFFLINE_CANDIDATE")
            assert task["effort_estimate"]


def test_snapshot_plan_read_only_and_no_authority():
    data = snapshot()
    assert data["read_only"] is True
    assert len(data["milestone_execution_plan"]) == 9
    assert len(data["milestone_work_batches"]) == 9
    assert data["progress_estimate"]["fully_accepted_stages"] == 0
    assert data["progress_estimate"]["weighted_index_percent"] == 38
    assert data["progress_estimate"]["release_approved"] is False
    assert data["progress_estimate"]["live_orders_enabled"] is False


def test_task_plan_returns_independent_copy():
    first = execution_plan()
    first[0]["tasks"][0]["state"] = "ACCEPTED"
    assert execution_plan()[0]["tasks"][0]["state"] == "REVIEW_REQUIRED"


def test_batch_order_and_offline_external_partition():
    groups = {g["id"]: g for g in execution_plan()}
    batches = next_work_batches()
    assert batches[0]["milestone"] == "M8"
    assert {entry["milestone"] for entry in batches} == set(groups)
    for entry in batches:
        all_ids = {t["id"] for t in groups[entry["milestone"]]["tasks"]}
        offline = set(entry["offline_candidates"])
        external = set(entry["external_or_native_gates"])
        assert offline.isdisjoint(external)
        assert offline | external == all_ids


def test_generated_markdown_contains_each_task():
    lines = markdown_lines()
    rendered = markdown()
    assert "## Engineering milestone execution queue" in rendered
    for group in execution_plan():
        for task in group["tasks"]:
            assert any(task["id"] + " " + task["title"] in line for line in lines)
            assert task["id"] + " " + task["title"] in rendered


def test_progress_baseline_unchanged():
    progress = progress_estimate()
    assert progress["weighted_index_percent"] == 38
    assert progress["uncertainty_low_percent"] == 30
    assert progress["uncertainty_high_percent"] == 45
    assert progress["fully_accepted_stages"] == 0
