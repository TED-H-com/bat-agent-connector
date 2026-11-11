"""Accepted connection identities from the real server projection, shared with Rust."""
import json
import os
import re
from pathlib import Path

from bat_agent_connector.connection_handoff import canonical
from tests import test_setup_connections as connections
from tests.operation_helpers import settle_operations

installed = connections.installed
served_setup = connections.served_setup
source = connections.source
FIXTURE = Path("tests/fixtures/connection-association-targets.json")


def identity_shape(rows):
    # Preserve equality across creation, update target and result without replacing
    # the real UUID producer. The checked-in fixture retains its original IDs.
    identities = {}
    def replace(match):
        suffix = identities.setdefault(match[2], str(len(identities)))
        return match[1] + suffix
    return re.sub(r"(op_|conn_)([0-9a-f]{32})", replace, canonical(rows).decode())


async def test_connection_association_targets_from_real_projection(installed, served_setup, source):
    daemon = installed[0]
    rows = []
    for revision in (0, 1):
        body = connections.intent(source, connection=rows[0]["connection_id"] if rows else None)
        body["preconditions"]["expected_revision"] = revision
        _, ident = await connections.accept(served_setup, body, key=f"association-{revision}")
        await settle_operations(daemon.ops)
        operation = daemon.ops.get(ident)
        assert operation["status"] == "succeeded", operation
        rows.append({"operation_id": operation["operation_id"], "target": operation["target"],
                     "connection_id": operation["result"]["connection_id"]})
    if os.environ.get("BATC_UPDATE_ASSOCIATION_FIXTURE") == "1":
        FIXTURE.write_bytes(canonical(rows) + b"\n")
    expected = json.loads(FIXTURE.read_bytes())
    assert identity_shape(rows) == identity_shape(expected)
    assert FIXTURE.read_bytes() == canonical(expected) + b"\n"
