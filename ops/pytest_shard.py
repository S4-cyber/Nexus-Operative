"""Deterministic exact-once pytest collection sharding for hosted CI.

Each shard still performs the complete collection. Only execution is divided,
so a collection/import failure remains visible in every shard. This plugin is
an execution-time optimization, not permission to omit tests or release.
"""
from hashlib import sha256

import pytest


def shard_number(nodeid, count):
    """Return the sole stable shard assigned to one exact pytest node id."""
    if (type(nodeid) is not str or not nodeid or
            type(count) is not int or not 2 <= count <= 16):
        raise ValueError("PYTEST_SHARD_ARGUMENT_INVALID")
    return int.from_bytes(sha256(nodeid.encode("utf-8")).digest()[:8], "big") % count


def shard_options(index, count):
    if (type(index) is not int or type(count) is not int or
            not 2 <= count <= 16 or not 0 <= index < count):
        raise ValueError("PYTEST_SHARD_ARGUMENT_INVALID")
    return index, count


def pytest_addoption(parser):
    group = parser.getgroup("nexus-ci-shard")
    group.addoption("--nexus-shard-index", type=int, default=None)
    group.addoption("--nexus-shard-count", type=int, default=None)


def pytest_collection_modifyitems(config, items):
    index = config.getoption("--nexus-shard-index")
    count = config.getoption("--nexus-shard-count")
    if index is None and count is None:
        return
    try:
        index, count = shard_options(index, count)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    assigned = [(item, shard_number(item.nodeid, count)) for item in items]
    selected = [item for item, shard in assigned if shard == index]
    deselected = [item for item, shard in assigned if shard != index]
    if not selected:
        raise pytest.UsageError("PYTEST_SHARD_EMPTY")
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
