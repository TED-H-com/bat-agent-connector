"""Serve the real Dashboard/API with synthetic GitHub, BAT and runtime evidence.

Run from the repository root: uv run python tests/manual/delivery_dashboard_fixture.py.
The separate loopback control listener belongs only to this browser fixture.
"""

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bat_agent_connector import deployment
from bat_agent_connector import deployment_store as store
from bat_agent_connector.task_daemon import TaskDaemon
from tests.fakegithub import TOKEN as GH_TOKEN
from tests.fakegithub import FakeGitHub
from tests.fakeverifier import FakeVerifier
from tests.mockbat import TOKEN as BAT_TOKEN
from tests.mockbat import MockBat
from tests.test_api_v1 import token
from tests.test_delivery import HEAD, MERGED, OPERATOR, config
from tests.test_deployment_rollback import allow
from tests.test_deployments import deployed, source_on_main


async def main():
    folder = Path(tempfile.mkdtemp(prefix="delivery-card-"))
    os.environ.update(
        FAKE_GH_TOKEN=GH_TOKEN,
        BATC_TEST_TOKEN=BAT_TOKEN,
        BATC_CONFIG_DIR=str(folder / "cfg"),
        BATC_STATE_DIR=str(folder / "state"),
    )
    gh = FakeGitHub()
    gh.start()
    mock = MockBat()
    await mock.start()
    d = TaskDaemon(config(mock, gh), folder / "journal.db")
    d.ops.context["deployment_verifier"] = FakeVerifier(gh)
    source_on_main(gh)
    gh.add_pr(7, HEAD)
    allow(d)
    cfg = d.ops.context["github_config"]
    cfg.recipes["unsupported"] = replace(
        cfg.recipes["prod"],
        name="unsupported",
        environment="preview",
        rollback=replace(cfg.recipes["prod"].rollback, supported=False),
    )
    cfg.recipes["alias"] = replace(cfg.recipes["prod"], name="alias")
    cfg.recipes["artifact"] = replace(
        cfg.recipes["prod"],
        name="artifact",
        inputs=cfg.recipes["prod"].inputs
        + (("artifact_id", "artifact_id"), ("artifact_digest", "artifact_digest")),
        rollback=replace(cfg.recipes["prod"].rollback, identity="artifact"),
    )
    done = await deployed(d, gh)
    current = deployment.get(d.ops, done["result"]["deployment_id"])
    template = dict(
        d.ops.db.execute(
            "SELECT * FROM deployments WHERE deployment_id=?", (current["deployment_id"],)
        ).fetchone()
    )
    from tests.legacy_deployment_fixture import legacy_deployment

    for n in range(10):
        name = "unsupported" if n == 9 else "artifact" if n in (3, 4) else "prod"
        old = legacy_deployment(
            d.journal, OPERATOR,
            action="deployment.start",
            target={"recipe": name},
            params={"source_sha": MERGED},
            idempotency_key=f"fixture-{n}",
        )
        d.ops.db.execute(
            "UPDATE operations SET status='failed',error_code='DEPLOY_FAILED' WHERE operation_id=?",
            (old["operation_id"],),
        )
        document = json.loads(template["document"])
        identity = {
            "source_sha": MERGED,
            "artifact_id": 100 + n,
            "artifact_digest": "sha256:" + "c" * 64,
            "artifact_run_id": 1000 + n,
            "artifact_expires_at": "2099-01-01T00:00:00Z",
        }
        if n == 3:
            identity["artifact_expires_at"] = "2000-01-01T00:00:00Z"
        if n == 4:
            identity.pop("artifact_digest")
        state = "failed" if n == 0 else "superseded" if n == 1 else "unverified" if n == 2 else "succeeded"
        document.update(
            verified=n != 2,
            evidence=None if n == 2 else document["evidence"],
            error_code="DEPLOY_FAILED" if n == 0 else None,
            html_url=f"https://github.example/o/r/actions/runs/{1000 + n}",
        )
        row = {
            **template,
            "deployment_id": "dep_" + old["operation_id"][3:],
            "operation_id": old["operation_id"],
            "generation": -n,
            "state": state,
            "identity": store.encode(identity),
            "document": store.encode(document),
            # Anchor history to the saved current deployment; slow asynchronous
            # setup must not reorder the pagination fixture.
            "created_at": template["created_at"] - n - 1,
            "run_id": 1000 + n,
        }
        if name != "prod":
            snapshot = {
                **asdict(cfg.recipes[name]),
                "provider_origin": gh.url,
                "repository_id": gh.repository_id,
                "workflow_id": 17,
            }
            row.update(
                recipe=name,
                recipe_snapshot=store.encode(snapshot),
                environment_key=store.environment_key(
                    gh.url, gh.repository_id, cfg.recipes[name].environment
                ),
            )
        if n == 8:
            legacy_key = store.environment_key("legacy", 0, "prod")
            d.ops.db.execute(
                "INSERT INTO deployment_environments "
                "(environment_key,provider_origin,repository_id,repository,environment,updated_at) "
                "VALUES(?,'legacy',0,'','',0)",
                (legacy_key,),
            )
            document.update(legacy=True, verified=False, evidence=None)
            row.update(
                recipe_digest="legacy",
                state="failed",
                environment_key=legacy_key,
                identity=store.encode({"source_sha": None}),
                document=store.encode(document),
                recipe_snapshot=store.encode(
                    {
                        "name": "prod",
                        "repository": "",
                        "environment": None,
                        "workflow": None,
                        "mode": None,
                        "legacy": True,
                    }
                ),
            )
        d.ops.db.execute(
            "INSERT INTO deployments(" + ",".join(row) + ")VALUES(" + ",".join("?" for _ in row) + ")",
            tuple(row.values()),
        )
    await deployment.preview(d.ops, "unsupported")
    # Synthetic desired differs from observed, retaining the verified current history.
    history = d.ops.db.execute(
        "SELECT deployment_id FROM deployments WHERE state='failed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()[0]
    d.ops.db.execute(
        "UPDATE deployment_environments SET desired_deployment_id=?,desired_generation=2,current_deployment_id=NULL,attention='ENVIRONMENT_VERSION_DRIFT',observed=? WHERE environment_key=?",
        (
            history,
            store.encode(
                {
                    "source_sha": "1" * 40,
                    "artifact_id": 77,
                    "artifact_digest": "sha256:" + "d" * 64,
                    "observed_at": time.time(),
                    "repository_id": 4242,
                    "environment": "production",
                }
            ),
            current["environment_key"],
        ),
    )
    seed_environments = [dict(row) for row in d.ops.db.execute("SELECT * FROM deployment_environments")]
    seed_ids = {row[0] for row in d.ops.db.execute("SELECT deployment_id FROM deployments")}
    own = token(d, "delivery-browser", "observe", "deploy", "merge", "integrate")
    viewer = token(d, "delivery-viewer", "observe")
    worker = asyncio.create_task(d.ops.loop(interval_s=0.05))
    server = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    # Test controls on a separate loopback listener, absent from the product router.
    async def control(reader, writer):
        nonlocal worker
        raw = await reader.readuntil(b"\r\n\r\n")
        path = raw.split(b" ")[1].decode()
        if path == "/pause-operations":
            await d.ops.drain()
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        if path == "/reset":
            await d.ops.drain()
            d.ops.db.execute(
                "UPDATE operations SET status='cancelled',cancel_requested=1 WHERE actor='delivery-browser'"
            )
            for row in d.ops.db.execute("SELECT deployment_id FROM deployments").fetchall():
                if row[0] not in seed_ids:
                    d.ops.db.execute("DELETE FROM deployments WHERE deployment_id=?", (row[0],))
            for env in seed_environments:
                assignments = ",".join(key + "=?" for key in env if key != "environment_key")
                d.ops.db.execute(
                    "UPDATE deployment_environments SET " + assignments + " WHERE environment_key=?",
                    (
                        *[value for key, value in env.items() if key != "environment_key"],
                        env["environment_key"],
                    ),
                )
            if worker.done():
                worker = asyncio.create_task(d.ops.loop(interval_s=0.05))
        if path == "/bump":
            d.ops.db.execute(
                "UPDATE deployment_environments SET desired_generation=desired_generation+1 WHERE environment_key=?",
                (current["environment_key"],),
            )
        if path == "/event":
            d.journal.api_event(
                "deployment_environment", current["environment_key"], "deployment.fixture", {}
            )
        body = json.dumps(
            {
                "operations": d.ops.db.execute(
                    "SELECT count(*) FROM operations WHERE action='deployment.rollback'"
                ).fetchone()[0],
                "browser_operations": d.ops.db.execute(
                    "SELECT count(*) FROM operations WHERE actor='delivery-browser'"
                ).fetchone()[0],
            }
        ).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    controls = await asyncio.start_server(control, "127.0.0.1", 0)
    info = {
        "url": f"http://127.0.0.1:{port}/dashboard/",
        "control": f"http://127.0.0.1:{controls.sockets[0].getsockname()[1]}",
        "token": own,
        "viewer": viewer,
        "folder": str(folder),
    }
    Path(os.environ.get("BATC_BROWSER_FIXTURE", "/tmp/delivery-card-fixture.json")).write_text(json.dumps(info))
    print("fixture ready", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        server.close()
        controls.close()
        await d.fleet.close()
        await mock.stop()
        gh.stop()
        d.journal.close()


if __name__ == "__main__":
    asyncio.run(main())
