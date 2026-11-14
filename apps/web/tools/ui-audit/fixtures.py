"""Configurable mock backend for the MAYA web UI audit harness.

A `Scenario` describes what the fake /api/v1/* backend returns. `STATES` maps a state
name to a Scenario factory. `Scenario.handle()` is called by harness.py for every
intercepted request and returns (status, json_body | None, delay_ms, content_type | None).

Wire shapes come from apps/web/src/lib/*-client.ts and hooks/*. Nothing here talks to a
real backend; the data is generated, deterministic (seeded) and clearly synthetic.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

NOW = datetime(2026, 11, 9, 9, 30, tzinfo=timezone.utc)

HINDI = "यह एक बहुत लंबा हिंदी शीर्षक है जो कई शब्दों से मिलकर बना है और पंक्ति में फिट नहीं होगा"
ARABIC = "هذا عنوان طويل جدا باللغة العربية لاختبار الاتجاه من اليمين إلى اليسار وكسر الأسطر"
LONG_URL = "https://example.com/" + "segment-" * 40 + "end?token=" + "a1b2c3d4" * 16
LONG_HASH = "sha256:" + "0123456789abcdef" * 8
LONG_TITLE = ("Quarterly reconciliation of vendor invoices against purchase orders for the "
              "regional offices, including exceptions, late fees and multi currency "
              "adjustments, with a summary for the finance review board. ") * 2
LONG_TITLE = LONG_TITLE[:300]
LONG_WORD = "Supercalifragilisticexpialidocious" * 9
LONG_FAILURE = ("Subtask failed after 3 retries: upstream tool 'sheets.append_rows' returned "
                "HTTP 502 with body " + ("{'error':'bad_gateway','detail':'" + "x" * 60 + "'} ") * 6
                + LONG_URL)
LONG_CODE_LINE = "const veryLongIdentifier = " + " + ".join(f"computeSomething{i}(argumentNumber{i})" for i in range(40)) + ";"

WIDE_TABLE = ("| " + " | ".join(f"Column {i}" for i in range(14)) + " |\n| "
              + " | ".join("---" for _ in range(14)) + " |\n"
              + "\n".join("| " + " | ".join(f"value-{r}-{c}-{'x' * 8}" for c in range(14)) + " |" for r in range(4)))

BIG_MESSAGE = (
    "## Report\n\n" + ("This paragraph is repeated to reach about twenty kilobytes of text. " * 6 + "\n\n") * 45
    + f"Unbroken URL: {LONG_URL}\n\nHash: `{LONG_HASH}`\n\n```ts\n{LONG_CODE_LINE}\n```\n\n{WIDE_TABLE}\n"
)


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


@dataclass
class Scenario:
    name: str
    counts: dict[str, int] = field(default_factory=dict)
    flavour: str = "normal"  # normal | long | rtl | partial
    readyz: str = "ok"  # ok | down | abort | 503
    auth: str = "ok"  # ok | signed_out | refresh500
    data_error: int | None = None  # force this status on data endpoints
    delay_ms: int = 0  # delay for data endpoints
    bootstrap_available: bool = False
    expire_after_boot: bool = False  # real expiry: first refresh ok, later refresh 401 and data 401
    refresh_calls: int = 0
    unmocked: list[str] = field(default_factory=list)  # filled during a run

    # ---- text helpers -----------------------------------------------------
    def title(self, i: int, kind: str = "Item") -> str:
        if self.flavour == "long":
            return f"{i:03d} " + (LONG_TITLE if i % 3 == 0 else LONG_WORD if i % 3 == 1 else LONG_URL)
        if self.flavour == "rtl":
            return f"{i:03d} " + (HINDI if i % 2 == 0 else ARABIC)
        return f"{kind} {i:03d} reconcile Q3 vendor invoices"

    def body(self, i: int) -> str:
        if self.flavour == "long":
            return (LONG_TITLE + " " + LONG_URL + " " + LONG_HASH + " ") * 2
        if self.flavour == "rtl":
            return (HINDI + " " + ARABIC + " ") * 3
        return f"Short note number {i} about a preference, project or fact."

    def n(self, key: str, default: int) -> int:
        return self.counts.get(key, default)

    # ---- dataset builders -------------------------------------------------
    def tasks(self) -> list[dict[str, Any]]:
        statuses = ["pending", "in_progress", "awaiting_approval", "completed", "failed", "cancelled"]
        out = []
        for i in range(self.n("tasks", 8)):
            st = statuses[i % len(statuses)]
            out.append({
                "id": f"task-{i:04d}", "workspace_id": "ws-1",
                "title": None if self.flavour == "partial" and i % 2 else self.title(i, "Task"),
                "instruction": self.body(i) if self.flavour != "normal" else f"Do task {i}: reconcile invoices",
                "status": st, "plan_status": "approved" if st != "pending" else "draft",
                "budget_usd": 5.0, "spent_usd": round(0.37 * (i % 9), 2),
                "created_at": iso(i * 7 + 3), "completed_at": iso(i * 7) if st in ("completed", "failed") else None,
                "subtask_count": 3 + i % 4,
            })
        return out

    def task_detail(self, task_id: str) -> dict[str, Any]:
        i = int(task_id.split("-")[-1]) if task_id.split("-")[-1].isdigit() else 0
        base = self.tasks()
        t = next((x for x in base if x["id"] == task_id), base[0] if base else {
            "id": task_id, "workspace_id": "ws-1", "title": "Task", "instruction": "x", "status": "completed",
            "plan_status": "approved", "budget_usd": 1, "spent_usd": 0, "created_at": iso(5), "subtask_count": 0})
        sub_statuses = ["completed", "in_progress", "failed", "pending", "completed"]
        subs = []
        for k in range(max(5, self.n("subtasks", 6))):
            st = sub_statuses[k % len(sub_statuses)]
            subs.append({
                "id": f"sub-{i}-{k}", "task_id": task_id, "assigned_agent": ["research", "documents", "email", "browser"][k % 4],
                "description": self.title(k, "Step") if self.flavour != "normal" else f"Step {k}: gather and compare data",
                "dependencies": [f"sub-{i}-{k - 1}"] if k else [], "status": st, "input_data": {},
                "output_result": None, "verification_proof": None,
                "error_message": (LONG_FAILURE if self.flavour in ("long", "failed") else "Tool timed out") if st == "failed" else None,
                "retry_count": k % 3, "created_at": iso(30), "updated_at": iso(10), "completed_at": None,
            })
        detail = {
            **t, "user_id": "user-1", "session_id": "sess-0000", "tool_turns": 4, "updated_at": iso(2),
            "active_plan": [], "subtasks": subs,
        }
        return detail

    def approvals(self) -> list[dict[str, Any]]:
        out = []
        for i in range(self.n("approvals", 4)):
            email = i % 3 == 0
            out.append({
                "id": f"appr-{i:04d}", "workspace_id": "ws-1", "user_id": "user-1", "task_id": f"task-{i:04d}",
                "subtask_id": None, "tool_name": "email.send" if email else ("git.push" if i % 3 == 1 else "files.delete"),
                "tool_args": ({"to": ["a@example.com"], "subject": self.title(i), "body": self.body(i)} if email
                              else {"path": LONG_URL if self.flavour == "long" else "/tmp/report.xlsx", "force": True}),
                "payload_hash": LONG_HASH, "tier": "APPROVAL_REQUIRED" if i % 4 else "RESTRICTED",
                "action_description": self.title(i, "Approve action"),
                "payload_diff": {"before": "line one\nline two", "after": "line one\nline 2\n" + LONG_CODE_LINE} if i % 3 == 1 else None,
                "status": "pending", "approval_token": None, "requested_at": iso(i * 3 + 1),
                "expires_at": (NOW + timedelta(minutes=30 - i)).isoformat().replace("+00:00", "Z"),
                "decided_at": None, "consumed_at": None, "decision_channel": None,
            })
        return out

    def memory(self) -> list[dict[str, Any]]:
        cats = ["preference", "project", "episodic", "knowledge"]
        return [{
            "id": f"mem-{i:04d}", "workspace_id": "ws-1", "user_id": "user-1", "category": cats[i % 4],
            "content": self.body(i), "metadata": {"source": LONG_URL} if self.flavour == "long" else {},
            "permanent": i % 5 == 0, "access_count": i % 7, "last_accessed_at": iso(i * 11) if i % 2 else None,
            "expires_at": None, "created_at": iso(i * 13 + 5),
        } for i in range(self.n("memory", 8))]

    def audit(self) -> list[dict[str, Any]]:
        statuses = ["success", "failed", "denied", "pending", "warning"]
        cats = ["auth", "task", "tool", "approval", "security"]
        out = []
        for i in range(self.n("audit", 10)):
            out.append({
                "id": f"aud-{i:05d}", "timestamp": iso(i * 2), "event_type": f"{cats[i % 5]}.event_{i % 9}",
                "category": cats[i % 5], "actor": LONG_URL if self.flavour == "long" else ("user-1" if i % 3 else "agent.research"),
                "details": self.body(i), "status": statuses[i % 5], "task_id": f"task-{i:04d}", "subtask_id": None,
                "approval_id": None, "server_name": "filesystem", "tool_name": "files.read", "permission_level": "READ",
                "prev_hash": LONG_HASH, "row_hash": LONG_HASH, "duration_ms": 120 + i,
                "input_payload": {"path": LONG_URL}, "output_payload": {"bytes": 1234},
            })
        return out

    def artifacts(self) -> list[dict[str, Any]]:
        types = ["pdf", "docx", "xlsx", "pptx", "md", "csv"]
        return [{
            "id": f"art-{i:04d}", "workspace_id": "ws-1", "task_id": f"task-{i:04d}", "user_id": "user-1",
            "file_name": (LONG_URL.split("/")[-1] + ".pdf" if self.flavour == "long" else
                          f"{HINDI[:20]}-{i}.docx" if self.flavour == "rtl" else f"report-{i:03d}.{types[i % 6]}"),
            "file_type": types[i % 6], "size_bytes": 1024 * (i + 1) * 37, "sha256": LONG_HASH[7:],
            "verification_status": ["verified", "failed", "unverified"][i % 3],
            "verification_report": {"checks": []}, "created_at": iso(i * 9 + 4),
        } for i in range(self.n("artifacts", 6))]

    def mcp(self) -> list[dict[str, Any]]:
        health = ["healthy", "degraded", "dead"]
        return [{
            "id": f"mcp-{i:03d}", "workspace_id": "ws-1", "user_id": "user-1",
            "name": self.title(i, "server") if self.flavour != "normal" else f"filesystem-{i}",
            "transport": ["stdio", "http", "streamable_http"][i % 3], "command": "npx" if i % 3 == 0 else None,
            "args": ["-y", "@modelcontextprotocol/server-filesystem", LONG_URL] if i % 3 == 0 else None,
            "url": LONG_URL if i % 3 else None, "pinned_version": "1.2.3", "schema_hash": LONG_HASH, "is_active": i % 4 != 3,
            "health_status": health[i % 3], "tool_count": 5 + i,
            "tools_manifest": [{"name": f"tool_{k}", "description": self.body(k)} for k in range(min(8, 3 + i % 6))],
            "last_ping_at": iso(i), "consecutive_failures": i % 3, "created_at": iso(900), "updated_at": iso(5),
        } for i in range(self.n("mcp", 4))]

    def sessions(self) -> list[dict[str, Any]]:
        return [{"id": f"sess-{i:04d}", "title": self.title(i, "Conversation") if self.flavour != "normal" or i else "Invoice reconciliation",
                 "channel": "web", "status": "active", "created_at": iso(i * 50 + 5)} for i in range(self.n("sessions", 6))]

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        out = []
        for i in range(self.n("messages", 8)):
            role = "user" if i % 2 == 0 else "assistant"
            if self.flavour == "long":
                content = BIG_MESSAGE if role == "assistant" else (LONG_TITLE + " " + LONG_URL)
            elif self.flavour == "rtl":
                content = (HINDI + "\n\n" + ARABIC + "\n\n- " + HINDI[:30] + "\n- " + ARABIC[:30]) if role == "assistant" else ARABIC
            else:
                content = ("Here is a summary.\n\n- point one\n- point two\n\n```python\nprint('hello')\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"
                           if role == "assistant" else "Please reconcile the vendor invoices.")
            m: dict[str, Any] = {"id": f"msg-{i:04d}", "role": role, "content": content, "created_at": iso(100 - i),
                                 "finish_reason": "stop" if role == "assistant" else None, "model_id": "gemini"}
            if role == "assistant" and i == 1:
                m["metadata"] = {"task": {"task_id": "task-0001", "title": self.title(1, "Task")}}
            out.append(m)
        return out

    # ---- request handling -------------------------------------------------
    def handle(self, method: str, url: str, body: Any) -> tuple[int, Any, int, str | None]:
        u = urlparse(url)
        path = u.path.split("/api/v1", 1)[-1] or "/"
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        D = self.delay_ms

        if path == "/readyz":
            if self.readyz == "abort":
                return (-1, None, 0, None)
            if self.readyz in ("down", "503"):
                return (503, {"status": "unavailable"}, 0, None)
            return (200, {"status": "ready"}, 0, None)
        if path in ("/auth/refresh",):
            self.refresh_calls += 1
            if self.expire_after_boot and self.refresh_calls > 1:
                return (401, {"error": "invalid_refresh_token"}, 0, None)
            if self.auth in ("signed_out",):
                return (401, {"error": "invalid_refresh_token"}, 0, None)
            if self.auth == "refresh500":
                return (500, {"error": "server_error"}, 0, None)
            return (200, {"access_token": "mock-token", "token_type": "bearer", "expires_in": 900}, 0, None)
        if path == "/auth/me":
            if self.auth != "ok":
                return (401, {"error": "unauthenticated"}, 0, None)
            return (200, {"user_id": "owner" if self.flavour == "normal" else "owner-" + "x" * 40, "workspace_id": "ws-1", "role": "owner"}, 0, None)
        if path == "/auth/bootstrap/status":
            return (200, {"available": self.bootstrap_available}, 0, None)
        if path.startswith("/auth/"):
            return (200, {"status": "ok", "next": "password", "exists": True}, 0, None)

        # data endpoints from here on
        if self.expire_after_boot:
            return (401, {"error": "token_expired"}, 0, None)
        if self.data_error is not None:
            return (self.data_error, {"error": "forced_error", "message": "Forced error for audit"}, D, None)

        def page(items: list[Any]) -> list[Any]:
            lim = int(q.get("limit", len(items) or 1))
            off = int(q.get("offset", 0))
            return items[off: off + lim]

        if path == "/chat/sessions" and method == "GET":
            items = self.sessions()
            lim, off = int(q.get("limit", 50)), int(q.get("cursor") or 0)
            nxt = str(off + lim) if off + lim < len(items) else None
            return (200, {"items": items[off: off + lim], "next_cursor": nxt}, D, None)
        if path.startswith("/chat/sessions/") and path.endswith("/messages"):
            items = self.messages(path.split("/")[3])
            lim, off = int(q.get("limit", 50)), int(q.get("cursor") or 0)
            nxt = str(off + lim) if off + lim < len(items) else None
            return (200, {"items": items[off: off + lim], "next_cursor": nxt}, D, None)
        if path.startswith("/chat/sessions/") and method == "DELETE":
            return (204, None, D, None)
        if path == "/chat/message":
            return (503, {"error": "mock_no_chat"}, D, None)
        if path == "/tasks" and method == "GET":
            items = self.tasks()
            if q.get("status"):
                items = [t for t in items if t["status"] == q["status"]]
            return (200, {"items": page(items), "total": len(items), "limit": int(q.get("limit", 20)), "offset": int(q.get("offset", 0))}, D, None)
        if path.startswith("/tasks/") and path.endswith("/events"):
            return (200, "", D, "text/event-stream")
        if path.startswith("/tasks/") and method == "GET":
            return (200, self.task_detail(path.split("/")[2]), D, None)
        if path == "/approvals":
            items = self.approvals()
            return (200, page(items), D, None)
        if path.startswith("/approvals/") and method == "GET":
            aid = path.split("/")[2]
            return (200, next((a for a in self.approvals() if a["id"] == aid), self.approvals()[0] if self.approvals() else {}), D, None)
        if path == "/artifacts":
            return (200, self.artifacts(), D, None)
        if path.startswith("/artifacts/") and path.endswith("/metadata"):
            return (200, self.artifacts()[0], D, None)
        if path.startswith("/artifacts/"):
            return (200, "mock artifact bytes", D, "text/plain")
        if path == "/memory/items" and method == "GET":
            items = self.memory()
            if q.get("category"):
                items = [m for m in items if m["category"] == q["category"]]
            return (200, page(items), D, None)
        if path == "/memory/search":
            res = [{"id": m["id"], "category": m["category"], "content": m["content"], "metadata": {}, "raw_similarity": 0.8,
                    "effective_score": 0.7, "source_id": None, "created_at": m["created_at"]} for m in self.memory()[:10]]
            return (200, {"query": "q", "results": res, "total_results": len(res)}, D, None)
        if path == "/audit":
            items = self.audit()
            if q.get("category"):
                items = [a for a in items if a["category"] == q["category"]]
            lim, off = int(q.get("limit", 50)), int(q.get("offset", 0))
            return (200, {"items": items[off: off + lim], "total": len(items), "limit": lim, "offset": off,
                          "chain_valid": True, "chain_first_bad_id": None, "chain_length": len(items),
                          "statuses": ["success", "failed", "denied", "pending", "warning"]}, D, None)
        if path == "/mcp/servers" and method == "GET":
            return (200, self.mcp(), D, None)
        if path.startswith("/mcp/servers/") and path.endswith("/health"):
            m = self.mcp()
            s = next((x for x in m if x["id"] == path.split("/")[3]), m[0] if m else None)
            return (200, {"server_id": s["id"], "name": s["name"], "health_status": s["health_status"], "last_ping_at": s["last_ping_at"],
                          "consecutive_failures": s["consecutive_failures"], "tool_count": s["tool_count"]} if s else {}, D, None)
        if path == "/settings/chat":
            return (200, {"intent_classifier": False, "classifier_available": True, "memory_in_chat": True}, D, None)
        self.unmocked.append(f"{method} {path}")
        return (404, {"error": "unmocked_endpoint", "path": path}, 0, None)


def _mk(**kw: Any) -> Scenario:
    return Scenario(**kw)


MANY = {"tasks": 600, "approvals": 60, "memory": 600, "audit": 600, "artifacts": 500, "mcp": 60, "sessions": 200, "messages": 300}
EMPTY = {"tasks": 0, "approvals": 0, "memory": 0, "audit": 0, "artifacts": 0, "mcp": 0, "sessions": 0, "messages": 0}

STATES: dict[str, Any] = {
    "normal": lambda: _mk(name="normal"),
    "empty": lambda: _mk(name="empty", counts=EMPTY, bootstrap_available=True),
    "many": lambda: _mk(name="many", counts=MANY),
    "one": lambda: _mk(name="one", counts={"memory": 1, "audit": 1, "artifacts": 1}),
    "long": lambda: _mk(name="long", flavour="long"),
    "rtl": lambda: _mk(name="rtl", flavour="rtl"),
    "partial": lambda: _mk(name="partial", flavour="partial"),
    "busy": lambda: _mk(name="busy", flavour="failed", counts={"tasks": 60, "subtasks": 12}),
    "error500": lambda: _mk(name="error500", data_error=500),
    "expired401": lambda: _mk(name="expired401", data_error=401),
    "expired": lambda: _mk(name="expired", expire_after_boot=True),
    "signedout": lambda: _mk(name="signedout", auth="signed_out"),
    "unreachable": lambda: _mk(name="unreachable", readyz="abort", auth="signed_out"),
    "slow": lambda: _mk(name="slow", delay_ms=1),
}

# Pages: name -> (path, kind). kind: app (needs auth), auth (public form), redirect, notfound, gate.
PAGES: dict[str, tuple[str, str]] = {
    "conversation": ("/", "app"),
    "tasks": ("/tasks/", "app"),
    "approvals": ("/approvals/", "app"),
    "artifacts": ("/artifacts/", "app"),
    "memory": ("/memory/", "app"),
    "connections": ("/connections/", "app"),
    "activity": ("/activity/", "app"),
    "settings": ("/settings/", "app"),
    # interaction variants (harness.prepare() opens the overlay): modals and detail panes
    "x-tasks-newtask": ("/tasks/", "app"),
    "x-approvals-detail": ("/approvals/", "app"),
    "x-memory-add": ("/memory/", "app"),
    "x-connections-register": ("/connections/", "app"),
    "x-conversation-approval": ("/", "app"),
    "login": ("/login/", "auth"),
    "register": ("/register/", "auth"),
    "forgot-password": ("/forgot-password/", "auth"),
    "reset-password": ("/reset-password/", "auth"),
    "verify": ("/verify/", "auth"),
    "notfound": ("/does-not-exist/", "notfound"),
    "legacy-agents": ("/agents/", "redirect"),
    "legacy-chat": ("/chat/", "redirect"),
    "legacy-deliverables": ("/deliverables/", "redirect"),
    "legacy-integrations": ("/integrations/", "redirect"),
    "legacy-workspace": ("/workspace/", "redirect"),
}


def make_state(name: str) -> Scenario:
    return STATES[name]()


if __name__ == "__main__":
    s = make_state("long")
    print(json.dumps({"tasks": len(s.tasks()), "sample": s.tasks()[0]}, ensure_ascii=False)[:300])
