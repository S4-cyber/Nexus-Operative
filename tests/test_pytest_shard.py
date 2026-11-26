"""Exact-once and fail-closed tests for the hosted pytest shard policy."""
import pytest

from ops.pytest_shard import shard_number, shard_options


def test_each_node_has_exactly_one_stable_shard():
    nodeids = [
        f"tests/test_module_{module}.py::TestGroup::test_case[{case}]"
        for module in range(17) for case in range(23)
    ]
    for count in (2, 3, 7, 16):
        first = {nodeid: shard_number(nodeid, count) for nodeid in nodeids}
        second = {nodeid: shard_number(nodeid, count) for nodeid in reversed(nodeids)}
        assert first == second
        assert all(0 <= value < count for value in first.values())
        assert sum(sum(value == index for index in range(count))
            for value in first.values()) == len(nodeids)
        assert set(first.values()) == set(range(count))


@pytest.mark.parametrize("nodeid,count", (
    (None, 2), ("", 2), ("test_x", True), ("test_x", 1),
    ("test_x", 17), ("test_x", "2")))
def test_node_assignment_rejects_malformed_scope(nodeid, count):
    with pytest.raises(ValueError, match="PYTEST_SHARD_ARGUMENT_INVALID"):
        shard_number(nodeid, count)


@pytest.mark.parametrize("index,count", (
    (None, None), (0, None), (None, 2), (-1, 2), (2, 2),
    (0, 1), (0, 17), (False, 2), (0, True)))
def test_shard_options_require_one_bounded_complete_pair(index, count):
    with pytest.raises(ValueError, match="PYTEST_SHARD_ARGUMENT_INVALID"):
        shard_options(index, count)


def test_shard_options_preserve_exact_integer_pair():
    assert shard_options(0, 2) == (0, 2)
    assert shard_options(15, 16) == (15, 16)
