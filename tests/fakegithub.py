"""A small fake of the GitHub REST endpoints delivery.py uses (API version 2026-03-10 shapes)."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import uuid as uuidlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import unquote

TOKEN = "ghp_fake_" + "x" * 30  # gitleaks:allow (fake test token)


def parse_compare_route(path: str) -> tuple[str, str] | None:
    prefix = "/repos/o/r/compare/"
    if not path.startswith(prefix):
        return None
    refs = path[len(prefix):]
    if not refs or "\n" in refs:
        return None
    # The greedy match used the last separator leaving both groups nonempty.
    # Reserve one head character before rpartition, including for trailing dots.
    base, separator, head = refs[:-1].rpartition("...")
    return (base, head + refs[-1]) if separator and base else None


class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer resolves getfqdn(host), which can stall on macOS runners.
        # This fake only needs its bound numeric address, never reverse DNS.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict | None]] = []
        self.pulls: dict[int, dict] = {}
        self.issues: dict[int, dict] = {}
        self.merge_requests: dict[str, dict] = {}
        self.commits: dict[str, dict] = {"b" * 40: {"sha": "b" * 40, "parents": [], "commit": {"message": "base"}}}
        self.branches = {"main": "b" * 40}
        self.default_branch = "main"
        self.stacks: dict[int, dict] = {}
        self.before_request = None
        self.patch_mode = "ok"
        self.patch_after = None
        self.methods: dict[int, str] = {}
        self.check_runs: dict[str, list[dict]] = {}
        self.runs: dict[int, dict] = {}
        self.jobs: dict[int, list[dict]] = {}
        self.attempt_jobs: dict[tuple[int, int], list[dict]] = {}
        self.pending_deployments: dict[int, list[dict]] = {}
        self.artifacts: dict[int, dict] = {}
        self.repository_id = 4242
        self.workflow_ids = {"deploy.yml": 123}
        self.deployed_source = "9" * 40
        self.next_run_id = 9000
        # number -> path of a bare repository: the PR head follows that branch, as on GitHub after a push
        self.pr_remotes: dict[int, str] = {}
        # behaviour switches
        self.merge_mode = "async"  # async | enqueue | fail_500 | fail_500_but_merged | conflict_409_same | conflict_409_other
        self.dispatch_mode = "run_id"  # run_id | no_content | fail_500_but_started
        self.result_status = "merged"  # what GET merge-async/{uuid} reports first
        self.token = TOKEN  # the token GitHub currently accepts (rotate it to expire the connector's copy)
        # one-off answers, each used once by the first matching request: (method, path regex, status, headers, body)
        self.script: list[tuple[str, str, int, dict, dict]] = []
        # (method, path regex): handle the request normally, then cut the reply body short, once
        self.truncate: list[tuple[str, str]] = []
        self._server = LoopbackHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def add_pr(self, number: int, head: str, *, head_ref: str | None = None, fork: bool = False, repo_id: int = 4242,
               **fields) -> dict:
        repo = {"id": repo_id, "full_name": "o/r", "default_branch": "main"}
        head_repo = {"id": repo_id + 1, "full_name": "someone/r"} if fork else {"id": repo_id, "full_name": "o/r"}
        pr = {"number": number, "state": "open", "draft": False, "merged": False, "merge_commit_sha": None,
              "mergeable": True, "mergeable_state": "clean", "title": f"PR {number}",
              "updated_at": datetime.now(timezone.utc).isoformat(), "merged_at": None,
              "head": {"sha": head, "ref": head_ref or f"feature-{number}", "repo": head_repo},
              "base": {"sha": "b" * 40, "ref": "main", "repo": repo},
              "html_url": f"https://github.example/o/r/pull/{number}", **fields}
        self.commits.setdefault(head, {"sha": head, "parents": [{"sha": pr["base"]["sha"]}],
                                       "commit": {"message": f"PR {number}"}})
        self.pulls[number] = pr
        return pr

    def add_issue(self, number: int, **fields) -> dict:
        # REST issue fields and closure reasons, not Connector completion fields:
        # https://docs.github.com/en/rest/issues/issues#get-an-issue
        base = self.url + "/repos/o/r"
        now = datetime.now(timezone.utc).isoformat()
        issue = {"id": 2000 + number, "number": number, "title": f"Issue {number}", "body": "",
                 "state": "open", "state_reason": None, "closed_at": None,
                 "created_at": now, "updated_at": now,
                 "labels": [], "url": base + f"/issues/{number}", "repository_url": base,
                 "html_url": f"https://github.example/o/r/issues/{number}", **fields}
        self.issues[number] = issue
        return issue

    def track_remote(self, number: int, bare_repo: str) -> None:
        """Make PR ``number``'s head follow its head ref in a local bare repository (a stand-in for the remote)."""
        self.pr_remotes[number] = bare_repo

    def _live_head(self, pr: dict) -> None:
        bare = self.pr_remotes.get(pr["number"])
        if bare:
            out = subprocess.run(["git", "-C", bare, "rev-parse", "--verify", "-q", "refs/heads/" + pr["head"]["ref"]],
                                 capture_output=True, text=True).stdout.strip()
            if out:
                pr["head"]["sha"] = out

    def merge(self, number: int, sha: str = "9" * 40) -> None:
        pr = self.pulls[number]
        base = self.branches.get(pr["base"]["ref"], pr["base"]["sha"])
        if sha == base:
            sha = f"{number:040x}"
        parents = [{"sha": base}]
        if self.methods.get(number) == "rebase":
            count = len(self.comparison(pr["base"]["sha"], pr["head"]["sha"], 1)["commits"])
            for index in range(count - 1):
                rewritten = f"{7000 + number * 100 + index:040x}"
                self.commits[rewritten] = {"sha": rewritten, "parents": parents, "commit": {"message": "rebased"}}
                parents = [{"sha": rewritten}]
        if self.methods.get(number) == "merge":
            parents.append({"sha": pr["head"]["sha"]})
        self.commits[sha] = {"sha": sha, "parents": parents, "commit": {"message": "merged"}}
        self.branches[pr["base"]["ref"]] = sha
        now = datetime.now(timezone.utc).isoformat()
        pr.update(merged=True, state="closed", merge_commit_sha=sha, merged_at=now, closed_at=now, updated_at=now)

    def ancestors(self, sha):
        seen, pending = set(), [sha]
        while pending:
            c = pending.pop()
            if c not in seen:
                seen.add(c)
                pending.extend(p["sha"] for p in self.commits.get(c, {}).get("parents", []))
        return seen

    def comparison(self, base, head, page):
        base, head = self.branches.get(base, base), self.branches.get(head, head)
        ba, ha = self.ancestors(base), self.ancestors(head)
        common = ba & ha
        if not common:
            return None
        merge_base = next((s for s in [base, head, *self.commits] if s in common), None)
        shas = [s for s in self.commits if s in ha - ba]
        status = "identical" if base == head else "ahead" if base in ha else "behind" if head in ba else "diverged"
        return {"status": status, "merge_base_commit": {"sha": merge_base}, "total_commits": len(shas),
                "commits": [self.commits[s] for s in shas[(page-1)*100:page*100]], "files": []}

    def add_run(self, *, head_sha: str = "c" * 40, title: str = "deploy", status: str = "in_progress",
                conclusion: str | None = None, event: str = "workflow_dispatch", job_conclusion: str | None = None,
                branch="main", workflow_id=123, attempt=1, repository_id=4242):
        rid = self.next_run_id
        self.next_run_id += 1
        self.runs[rid] = {"id": rid, "head_sha": head_sha, "display_title": title, "name": "Deploy",
                          "status": status, "conclusion": conclusion, "event": event, "run_attempt": attempt,
                          "created_at": datetime.now(timezone.utc).isoformat(),
                          "head_branch": branch, "workflow_id": workflow_id, "repository": {"id": repository_id},
                          "html_url": f"https://github.example/o/r/actions/runs/{rid}"}
        self.jobs[rid] = [{"name": "build", "conclusion": "success"},
                          {"name": "deploy", "conclusion": job_conclusion}]
        return self.runs[rid]

    def count(self, method: str, pattern: str) -> int:
        return sum(1 for m, p, _ in self.requests if m == method and re.search(pattern, p))

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, status: int, body: dict | None = None, headers: dict | None = None) -> None:
                raw = json.dumps(body).encode() if body is not None else b""
                length = len(raw)
                for i, (m, pattern) in enumerate(fake.truncate):
                    if m == self.command and re.search(pattern, self.path):
                        del fake.truncate[i]
                        length += 50  # promise more than is sent: the client sees IncompleteRead
                        break
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(length))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def _route(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                path = self.path.split("?", 1)[0]
                query = dict(p.split("=", 1) for p in self.path.split("?", 1)[1].split("&")) if "?" in self.path else {}
                fake.requests.append((method, self.path, body))
                if fake.before_request:
                    fake.before_request(method, path, body)
                for i, (m, pattern, status, headers, answer) in enumerate(fake.script):
                    if m == method and re.search(pattern, path):
                        del fake.script[i]
                        return self._send(status, answer, headers)
                if self.headers.get("Authorization") != "Bearer " + fake.token:
                    return self._send(401, {"message": "Bad credentials"})
                if self.headers.get("X-GitHub-Api-Version") != "2026-03-10":
                    return self._send(400, {"message": "unsupported API version"})
                if path == "/repos/o/r" and method == "GET":
                    return self._send(200, {"id": fake.repository_id, "full_name": "o/r", "default_branch": fake.default_branch})
                if path == "/repos/o/r/issues" and method == "POST":
                    number = max([0, *fake.pulls, *fake.issues]) + 1
                    issue = fake.add_issue(number, title=body["title"], body=body.get("body", ""))
                    return self._send(201, issue)
                if path == "/repos/o/r/issues" and method == "GET":
                    issues = [i for i in fake.issues.values() if query.get("state") == "all"
                              or i["state"] == query.get("state", "open")]
                    page = int(query.get("page", 1))
                    return self._send(200, issues[(page-1)*100:page*100])
                m = re.fullmatch(r"/repos/o/r/issues/(\d+)", path)
                if m and method == "GET":
                    issue = fake.issues.get(int(m.group(1)))
                    return self._send(200, issue) if issue else self._send(404, {"message": "Not Found"})
                if path == "/repos/o/r/pulls" and method == "GET":
                    prs = [p for p in fake.pulls.values() if query.get("state") == "all"
                           or p["state"] == query.get("state", "open")]
                    if query.get("sort") == "updated":
                        prs.sort(key=lambda p: p["updated_at"], reverse=query.get("direction", "desc") == "desc")
                    page = int(query.get("page", 1))
                    return self._send(200, prs[(page-1)*100:page*100])
                if path == "/repos/o/r/stacks" and method == "GET":
                    stacks = [s for s in fake.stacks.values() if int(query.get("pull_request", 0)) in
                              [p["number"] for p in s["pull_requests"]]]
                    page = int(query.get("page", 1))
                    return self._send(200, stacks[(page-1)*100:page*100])
                m = re.fullmatch(r"/repos/o/r/stacks/(\d+)", path)
                if m and method == "GET":
                    return self._send(200, fake.stacks[int(m.group(1))])
                refs = parse_compare_route(path)
                if refs and method == "GET":
                    result = fake.comparison(unquote(refs[0]), unquote(refs[1]), int(query.get("page", 1)))
                    return self._send(200, result) if result else self._send(404, {"message": "unrelated commits"})
                m = re.fullmatch(r"/repos/o/r/pulls/(\d+)", path)
                if m and method == "PATCH":
                    pr = fake.pulls[int(m.group(1))]
                    mode = fake.patch_mode
                    if mode != "lost_before":
                        pr.update(body or {})
                        pr["updated_at"] = datetime.now(timezone.utc).isoformat()
                    if fake.patch_after:
                        fake.patch_after(pr)
                    if mode.startswith("lost"):
                        fake.patch_mode = "ok"
                        return self._send(502, {"message": "lost reply"})
                    return self._send(200, pr)
                if m and method == "GET":
                    pr = fake.pulls.get(int(m.group(1)))
                    if pr:
                        fake._live_head(pr)
                    return self._send(200, pr) if pr else self._send(404, {"message": "Not Found"})
                m = re.fullmatch(r"/repos/o/r/commits/([^/]+)", path)
                if m and method == "GET":  # a commit exists
                    sha = fake.branches.get(unquote(m.group(1)), m.group(1))
                    if sha in fake.commits:
                        return self._send(200, fake.commits[sha])
                    # a commit exists when any tracked bare remote has the object
                    for bare in fake.pr_remotes.values():
                        if subprocess.run(["git", "-C", bare, "cat-file", "-e", m.group(1) + "^{commit}"],
                                          capture_output=True).returncode == 0:
                            return self._send(200, {"sha": m.group(1)})
                    return self._send(422, {"message": "No commit found for SHA: " + m.group(1)})
                m = re.fullmatch(r"/repos/o/r/pulls/(\d+)/merge-async", path)
                if m and method == "PUT":
                    return self._merge_async(int(m.group(1)), body or {})
                m = re.fullmatch(r"/repos/o/r/pulls/(\d+)/merge-async/([0-9a-f-]+)", path)
                if m and method == "GET":
                    req = fake.merge_requests.get(m.group(2))
                    if not req:
                        return self._send(404, {"message": "Not Found"})
                    if fake.result_status == "merged":
                        fake.merge(int(m.group(1)))
                        return self._send(200, {"status": "merged", "details": {"message": "ok", "sha": "9" * 40}})
                    if fake.result_status == "failed":
                        return self._send(200, {"status": "failed", "details": {"message": "required review missing"}})
                    return self._send(200, {"status": "pending", "details": req})
                m = re.fullmatch(r"/repos/o/r/commits/([0-9a-f]+)/check-runs", path)
                if m and method == "GET":
                    return self._send(200, {"check_runs": fake.check_runs.get(m.group(1), [])})
                m = re.fullmatch(r"/repos/o/r/actions/workflows/([^/]+)/dispatches", path)
                if m and method == "POST":
                    return self._dispatch(body or {}, m.group(1))
                m = re.fullmatch(r"/repos/o/r/actions/workflows/([^/]+)/runs", path)
                if m and method == "GET":
                    runs = [r for r in fake.runs.values()
                            if (not query.get("head_sha") or r["head_sha"] == query["head_sha"])
                            and r["workflow_id"] == fake.workflow_ids.get(m.group(1), -1)
                            and (not query.get("branch") or r["head_branch"] == query["branch"])
                            and (not query.get("event") or r["event"] == query["event"])]
                    if query.get("created"):
                        cutoff = unquote(query["created"]).removeprefix(">=")
                        start = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
                        runs = [r for r in runs if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= start]
                    page, size = int(query.get("page", 1)), int(query.get("per_page", 50))
                    return self._send(200, {"total_count": len(runs), "workflow_runs": runs[(page-1)*size:page*size]})
                m = re.fullmatch(r"/repos/o/r/actions/workflows/([^/]+)", path)
                if m and method == "GET":
                    return self._send(200, {"id": fake.workflow_ids.get(m.group(1), 123), "path": m.group(1)})
                m = re.fullmatch(r"/repos/o/r/actions/runs/(\d+)/attempts/(\d+)/jobs", path)
                if m and method == "GET":
                    rid, attempt = int(m.group(1)), int(m.group(2))
                    jobs = fake.attempt_jobs.get((rid, attempt), fake.jobs.get(rid, []) if attempt == 1 else [])
                    jobs = [{"id": i + 1, "status": "completed" if j.get("conclusion") else "in_progress", **j}
                            for i, j in enumerate(jobs)]
                    page = int(query.get("page", 1))
                    return self._send(200, {"jobs": jobs[(page-1)*100:page*100]})
                m = re.fullmatch(r"/repos/o/r/actions/runs/(\d+)/pending_deployments", path)
                if m and method == "GET":
                    return self._send(200, fake.pending_deployments.get(int(m.group(1)), []))
                m = re.fullmatch(r"/repos/o/r/actions/artifacts/(\d+)", path)
                if m and method == "GET":
                    artifact = fake.artifacts.get(int(m.group(1)))
                    return self._send(200, artifact) if artifact else self._send(404, {"message": "Not Found"})
                m = re.fullmatch(r"/repos/o/r/actions/runs/(\d+)", path)
                if m and method == "GET":
                    run = fake.runs.get(int(m.group(1)))
                    return self._send(200, run) if run else self._send(404, {"message": "Not Found"})
                m = re.fullmatch(r"/repos/o/r/actions/runs/(\d+)/jobs", path)
                if m and method == "GET":
                    return self._send(200, {"jobs": fake.jobs.get(int(m.group(1)), [])})
                return self._send(404, {"message": "no route " + path})

            def _merge_async(self, number: int, body: dict) -> None:
                pr = fake.pulls[number]
                fake.methods[number] = body.get("merge_method", "squash")
                if pr["merged"]:
                    return self._send(200, {"status": "merged", "details": {"message": "ok",
                                                                            "sha": pr["merge_commit_sha"]}})
                mode = fake.merge_mode
                if mode == "fail_500":
                    fake.merge_mode = "async"
                    return self._send(502, {"message": "Bad Gateway"})
                if mode == "fail_500_but_merged":
                    fake.merge_mode = "async"
                    fake.merge(number)
                    return self._send(502, {"message": "Bad Gateway"})
                if mode == "enqueue":
                    return self._send(200, {"status": "enqueued", "details": {"message": "added to queue"}})
                if mode.startswith("conflict_409"):
                    sha = body.get("sha") if mode == "conflict_409_same" else "f" * 40
                    req = {"message": "pending", "uuid": "11111111-2222-3333-4444-555555555555",
                           "merge_method": body.get("merge_method"), "merge_action": "default",
                           "expected_head_sha": sha}
                    fake.merge_requests[req["uuid"]] = req
                    return self._send(409, {"status": "pending", "details": req})
                if pr["state"] != "open" or pr["draft"]:
                    return self._send(400, {"status": "failed", "details": {"message": "not mergeable"}})
                uid = str(uuidlib.uuid4())
                req = {"message": "accepted", "uuid": uid, "merge_method": body.get("merge_method"),
                       "merge_action": body.get("merge_action"), "expected_head_sha": body.get("sha")}
                fake.merge_requests[uid] = req
                return self._send(202, {"status": "pending", "details": req})

            def _dispatch(self, body: dict, workflow: str) -> None:
                inputs = body.get("inputs") or {}
                title = "deploy " + str(inputs.get("operation_id", ""))
                fake.deployed_source = inputs.get("source_sha", fake.deployed_source)
                run = fake.add_run(head_sha="d" * 40, title=title, branch=body["ref"],
                                   workflow_id=fake.workflow_ids.get(workflow, 123))
                if fake.dispatch_mode == "fail_500_but_started":
                    fake.dispatch_mode = "run_id"
                    return self._send(502, {"message": "Bad Gateway"})
                if fake.dispatch_mode == "no_content":
                    return self._send(204)
                return self._send(200, {"workflow_run_id": run["id"], "run_url": "x", "html_url": run["html_url"]})

            def do_GET(self):
                self._route("GET")

            def do_PATCH(self):
                self._route("PATCH")

            def do_PUT(self):
                self._route("PUT")

            def do_POST(self):
                self._route("POST")

        return Handler
