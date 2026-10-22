"""Stable operation enumeration through HTTP and the disk journal."""

import pytest

from tests import test_setup_connections as connections
from tests.test_api_v1 import token

installed = connections.installed
served_setup = connections.served_setup
source = connections.source


async def create(request, source, index, **kwargs):
    body = connections.intent({**source, "label": f"Connection {index}"})
    _, ident = await connections.accept(request, body, key=f"cursor-{index}", **kwargs)
    return ident


async def test_paired_cursor_keeps_equal_timestamp_operations_across_pages(installed, served_setup, source):
    daemon, _, _ = installed
    ids = [await create(served_setup, source, index) for index in range(3)]
    daemon.journal.db.execute("UPDATE operations SET created_at=42")
    status, first = await served_setup("GET", "/api/v1/operations?limit=2")
    assert status == 200
    expected = sorted(ids, reverse=True)
    assert [row["operation_id"] for row in first["operations"]] == expected[:2]
    assert first["next_before"] == 42 and first["next_before_id"] == expected[1]
    status, second = await served_setup(
        "GET", f"/api/v1/operations?limit=2&before={first['next_before']}&before_id={first['next_before_id']}"
    )
    assert status == 200
    assert [row["operation_id"] for row in second["operations"]] == expected[2:]
    assert second["next_before"] is None and second["next_before_id"] is None
    # Timestamp-only callers retain their original strict-less-than boundary.
    status, legacy = await served_setup("GET", "/api/v1/operations?before=42&limit=2")
    assert status == 200 and legacy["operations"] == [] and legacy["next_before"] is None


async def test_empty_visibility_filtered_page_still_has_paired_continuation(installed, served_setup, source):
    daemon, _, _ = installed
    foreign = token(daemon, "another-reader", "observe")
    hidden = await create(served_setup, source, 0, credential=foreign)
    visible = await create(served_setup, source, 1)
    daemon.journal.db.execute("UPDATE operations SET created_at=100 WHERE operation_id=?", (hidden,))
    daemon.journal.db.execute("UPDATE operations SET created_at=80 WHERE operation_id=?", (visible,))
    status, first = await served_setup("GET", "/api/v1/operations?limit=1")
    assert status == 200 and first["operations"] == []
    assert first["next_before"] == 100 and first["next_before_id"] == hidden
    status, second = await served_setup(
        "GET", f"/api/v1/operations?limit=1&before={first['next_before']}&before_id={first['next_before_id']}"
    )
    assert status == 200 and [row["operation_id"] for row in second["operations"]] == [visible]
    assert second["next_before"] is None and second["next_before_id"] is None


@pytest.mark.parametrize("query", ["before_id=op_" + "a" * 32, "before=42&before_id=invalid"])
async def test_operation_cursor_requires_a_bounded_pair(installed, served_setup, query):
    status, response = await served_setup("GET", "/api/v1/operations?" + query)
    assert status == 422 and response["error"]["code"] == "INVALID_FILTER"
