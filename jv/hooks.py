"""Entrada dos hooks que rodam dentro de cada agente.

Regra de ouro: um hook nunca pode travar nem derrubar o agente. Qualquer erro vai para
~/.jarvinho/hook-errors.log e o hook sai com código 0.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import db

PERMISSION_WORDS = ("permission", "permissão", "approve", "aprovar")


def handle(event: str, payload: dict) -> str | None:
    conn = db.connect()
    try:
        row = db.find_agent_by_session(conn, payload.get("session_id"), payload.get("session_title"))
        if row is None:
            return None  # sessão que não é do Jarvinho
        aid = row["id"]
        conn.execute(
            """UPDATE agents SET last_seen_at=:now, updated_at=:now,
               session_id=COALESCE(:sid, session_id),
               transcript_path=COALESCE(:tp, transcript_path) WHERE id=:id""",
            {"now": db.now(), "sid": payload.get("session_id"),
             "tp": payload.get("transcript_path"), "id": aid},
        )
        has_open = bool(db.open_decisions(conn, aid))

        if event in ("SessionStart", "UserPromptSubmit"):
            db.add_event(conn, aid, f"hook:{event}", payload.get("source", ""))
            if not has_open:
                db.set_status(conn, aid, "ativa", "")
        elif event == "PostToolUse":
            # sinal de vida; sem evento para não inchar o hub
            if row["status"] in ("interrompida", "pendente") and not has_open:
                db.set_status(conn, aid, "ativa", "")
        elif event == "Notification":
            msg = payload.get("message", "")
            db.add_event(conn, aid, "hook:Notification", msg)
            if any(w in msg.lower() for w in PERMISSION_WORDS):
                db.set_status(conn, aid, "pendente", f"aguardando permissão: {msg[:160]}")
        elif event == "Stop":
            last = payload.get("last_assistant_message") or ""
            conn.execute("UPDATE agents SET last_message=? WHERE id=?", (last[:4000], aid))
            db.add_event(conn, aid, "hook:Stop", last[:500])
            if has_open:
                db.set_status(conn, aid, "pendente", "decisão aberta")
            elif db.get_agent(conn, aid)["status"] == "ativa":
                db.set_status(conn, aid, "pendente", "turno encerrado sem concluir a missão")
        elif event == "SessionEnd":
            # processo encerrou (inclusive por ociosidade); quedas são tratadas pelo reconcile
            db.add_event(conn, aid, "hook:SessionEnd", payload.get("reason", ""))
        conn.commit()
        return aid
    finally:
        conn.close()


def main(event: str) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        handle(event, payload)
    except Exception:  # noqa: BLE001 - hook nunca pode falhar
        try:
            db.home().mkdir(parents=True, exist_ok=True)
            with open(db.home() / "hook-errors.log", "a") as f:
                f.write(f"--- {db.now()} {event}\n{traceback.format_exc()}\n")
        except Exception:  # noqa: BLE001
            pass
    return 0
