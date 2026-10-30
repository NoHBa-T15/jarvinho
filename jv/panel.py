"""Painel do Jarvinho: servidor local só-leitura (ao vivo) ou um HTML estático (foto)."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from . import claude_bridge as cb
from . import db, ops

INDEX = db.REPO_ROOT / "panel" / "index.html"


def state(conn) -> dict:
    agents = []
    for r in conn.execute("SELECT * FROM agents ORDER BY updated_at DESC").fetchall():
        a = dict(r)
        a["session_name"] = ops.session_name(r["id"])
        a["open_decisions"] = len(db.open_decisions(conn, r["id"]))
        agents.append(a)
    return {
        "generated_at": db.now(),
        "agents": agents,
        "decisions": [dict(d) for d in db.open_decisions(conn)],
    }


def agent_detail(conn, agent_id: str) -> dict | None:
    row = db.get_agent(conn, agent_id)
    if row is None:
        return None
    plan = db.plan_path(agent_id)
    return {
        "agent": dict(row),
        "plan": plan.read_text() if plan.exists() else "",
        "decisions": [dict(d) for d in conn.execute(
            "SELECT * FROM decisions WHERE agent_id=? ORDER BY id DESC LIMIT 20", (agent_id,)).fetchall()],
        "events": [dict(e) for e in conn.execute(
            "SELECT ts, kind, detail FROM events WHERE agent_id=? ORDER BY id DESC LIMIT 40", (agent_id,)).fetchall()],
        "tail": cb.transcript_tail(row["transcript_path"], 20),
    }


def build_static() -> str:
    conn = db.connect()
    try:
        st = state(conn)
        st["details"] = {a["id"]: agent_detail(conn, a["id"]) for a in st["agents"]}
    finally:
        conn.close()
    blob = json.dumps(st, ensure_ascii=False, default=str).replace("</", "<\\/")
    return INDEX.read_text().replace("/*JV_STATIC*/null", blob)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = unquote(self.path.split("?", 1)[0])
        if path in ("/", "/index.html"):
            return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        conn = db.connect()
        try:
            if path == "/api/state":
                data = state(conn)
            elif path.startswith("/api/agent/"):
                data = agent_detail(conn, path.rsplit("/", 1)[-1])
                if data is None:
                    return self._send(404, b'{"error":"not found"}', "application/json")
            else:
                return self._send(404, b"not found", "text/plain")
        finally:
            conn.close()
        self._send(200, json.dumps(data, ensure_ascii=False, default=str).encode(), "application/json")

    def log_message(self, *a):  # silencioso
        pass


def serve(host: str, port: int) -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"painel em http://{host}:{port}  (Ctrl+C para sair)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
