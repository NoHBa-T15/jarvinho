"""Reconciliação: compara o hub com o que o Claude Code diz estar rodando.

Roda no boot (ou quando o Jarvinho abre) e sob demanda. Detecta agentes que caíram,
marca como `interrompida` e, com --resume, retoma do último checkpoint. Também dispara
agentes agendados cuja dependência já concluiu.
"""
from __future__ import annotations

from . import claude_bridge as cb
from . import db, ops

ACTIVE = ("ativa", "pendente", "interrompida")


def run(conn, do_resume: bool = False) -> list[dict]:
    report: list[dict] = []
    sessions = cb.sessions(include_done=True)
    rows = conn.execute(
        f"SELECT * FROM agents WHERE status IN ({','.join('?' * len(ACTIVE))})", ACTIVE
    ).fetchall()
    for row in rows:
        aid = row["id"]
        live = ops.live_session(aid, sessions)
        state = (live or {}).get("state")
        if live and live.get("sessionId") and live.get("sessionId") != row["session_id"]:
            conn.execute("UPDATE agents SET session_id=?, claude_id=? WHERE id=?",
                         (live["sessionId"], live.get("id"), aid))
        has_open = bool(db.open_decisions(conn, aid))

        if live is None or state in ("failed", "stopped"):
            reason = "sessão sumiu do agent view" if live is None else f"sessão {state}"
            changed = db.set_status(conn, aid, "interrompida", reason)
            item = {"agent": aid, "action": "interrompida", "reason": reason}
            if do_resume:
                conn.commit()
                try:
                    item["action"] = "retomada: " + ops.resume(conn, aid)
                except (ops.JvError, cb.ClaudeError) as e:
                    item["action"] = f"falhou ao retomar: {e}"
            if changed or do_resume or row["status"] != "interrompida":
                report.append(item)
        elif state == "working":
            if not has_open and db.set_status(conn, aid, "ativa", ""):
                report.append({"agent": aid, "action": "ativa"})
        elif state == "blocked":
            if db.set_status(conn, aid, "pendente", "aguardando permissão ou entrada"):
                report.append({"agent": aid, "action": "pendente"})
        elif state == "done":
            if row["status"] in ("ativa", "interrompida"):
                reason = "decisão aberta" if has_open else "turno encerrado sem concluir a missão"
                db.set_status(conn, aid, "pendente", reason)
                report.append({"agent": aid, "action": "pendente", "reason": reason})
    conn.commit()

    for row in conn.execute(
        """SELECT a.id FROM agents a JOIN agents d ON d.id=a.depends_on
           WHERE a.status='agendada' AND d.status='concluida'"""
    ).fetchall():
        try:
            ops.launch(conn, row["id"])
            report.append({"agent": row["id"], "action": "disparada (dependência concluída)"})
        except (ops.JvError, cb.ClaudeError) as e:
            report.append({"agent": row["id"], "action": f"falhou ao disparar: {e}"})
    db.backup(conn, tag="reconcile") if report else None
    return report
