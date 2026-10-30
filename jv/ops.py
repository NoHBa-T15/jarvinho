"""Operações do Jarvinho sobre agentes: criar, retomar, pausar, arquivar, concluir, decidir."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import claude_bridge as cb
from . import db

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")
LIVE_STATES = {"working", "blocked", "done"}  # processo vivo no agent view
JV_BIN = str(db.REPO_ROOT / "bin" / "jv")

KICKOFF = (
    "Comece agora. Primeiro registre seu plano com `jv checkpoint {id}`, "
    "depois execute a missão seguindo o protocolo do Jarvinho."
)
RESUME_DEFAULT = (
    "Você foi retomado pelo Jarvinho depois de uma interrupção. Rode `jv plan {id}` para ler "
    "seu último checkpoint, confira o que já aconteceu de fato antes de repetir qualquer passo "
    "com efeito externo, e continue de onde parou."
)


class JvError(RuntimeError):
    pass


def session_name(agent_id: str) -> str:
    return f"jv-{agent_id}"


def agent_settings_path(agent_id: str) -> Path:
    d = db.home() / "agents"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{agent_id}.settings.json"


def write_agent_settings(agent_id: str) -> Path:
    tpl = (db.REPO_ROOT / "agent" / "settings.template.json").read_text()
    p = agent_settings_path(agent_id)
    p.write_text(tpl.replace("{jv}", JV_BIN))
    return p


def system_prompt(row) -> str:
    tpl = (db.REPO_ROOT / "agent" / "AGENT_PROTOCOL.md").read_text()
    return (
        tpl.replace("{agent_id}", row["id"])
        .replace("{project_path}", row["project_path"])
        .replace("{objective}", row["objective"])
        .replace("{done_criteria}", row["done_criteria"] or "definido por você no plano e confirmado com o usuário")
        .replace("{jv}", JV_BIN)
    )


def live_session(agent_id: str, sessions: list[dict] | None = None) -> dict | None:
    """Sessão do agent view com o nome do agente (a mais recente, se houver várias)."""
    sessions = cb.sessions() if sessions is None else sessions
    mine = [s for s in sessions if s.get("name") == session_name(agent_id)]
    if not mine:
        return None
    return max(mine, key=lambda s: s.get("startedAt", 0))


def _sync_ids(conn, agent_id: str, claude_id: str) -> None:
    sess = next((s for s in cb.sessions() if s.get("id") == claude_id), None)
    conn.execute(
        "UPDATE agents SET claude_id=?, session_id=COALESCE(?, session_id), updated_at=? WHERE id=?",
        (claude_id, sess.get("sessionId") if sess else None, db.now(), agent_id),
    )


def spawn(conn, agent_id: str, project: str, objective: str, done: str = "",
          mode: str = "acceptEdits", after: str | None = None) -> str:
    if not SLUG.match(agent_id):
        raise JvError("id inválido: use letras minúsculas, números e hífen (até 30)")
    if db.get_agent(conn, agent_id):
        raise JvError(f"já existe um agente '{agent_id}'")
    path = Path(project).expanduser().resolve()
    if not path.is_dir():
        raise JvError(f"pasta do projeto não existe: {path}")
    if after and not db.get_agent(conn, after):
        raise JvError(f"dependência desconhecida: {after}")
    ts = db.now()
    conn.execute(
        """INSERT INTO agents(id, project_path, objective, done_criteria, permission_mode,
           status, depends_on, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (agent_id, str(path), objective, done, mode, "agendada", after, ts, ts),
    )
    db.add_event(conn, agent_id, "spawn", objective)
    conn.commit()
    if after and db.get_agent(conn, after)["status"] != "concluida":
        db.set_status(conn, agent_id, "agendada", f"espera {after} concluir")
        conn.commit()
        return "agendada"
    launch(conn, agent_id)
    return "ativa"


def launch(conn, agent_id: str) -> None:
    row = db.get_agent(conn, agent_id)
    cb.trust(row["project_path"])
    settings = write_agent_settings(agent_id)
    claude_id = cb.spawn(
        session_name(agent_id), row["project_path"], KICKOFF.format(id=agent_id),
        settings=settings, system_prompt=system_prompt(row), mode=row["permission_mode"],
    )
    conn.execute("UPDATE agents SET started_at=COALESCE(started_at, ?) WHERE id=?", (db.now(), agent_id))
    _sync_ids(conn, agent_id, claude_id)
    db.set_status(conn, agent_id, "ativa", "", force=True)
    db.add_event(conn, agent_id, "launch", claude_id)
    conn.commit()


def resume(conn, agent_id: str, message: str | None = None, force: bool = False) -> str:
    row = _need(conn, agent_id)
    if row["status"] in ("concluida", "arquivada") and not force:
        raise JvError(f"{agent_id} está {row['status']}; use --force para reabrir")
    if row["status"] == "agendada":
        raise JvError(f"{agent_id} ainda espera {row['depends_on']} concluir")
    msg = message or RESUME_DEFAULT.format(id=agent_id)
    live = live_session(agent_id)
    if live and live.get("state") in ("working", "blocked"):
        raise JvError(
            f"{agent_id} está vivo ({live.get('state')}); fale com ele por SendMessage "
            f"para a sessão {session_name(agent_id)}"
        )
    if live and live.get("state") == "done":
        cb.stop(live["id"])  # evita duas cópias da mesma conversa
    if not row["session_id"]:
        launch(conn, agent_id)
        return "relançado do zero (sem conversa salva)"
    cb.trust(row["project_path"])
    claude_id = cb.resume(
        row["session_id"], session_name(agent_id), row["project_path"], msg,
        settings=write_agent_settings(agent_id), system_prompt=system_prompt(row),
        mode=row["permission_mode"],
    )
    _sync_ids(conn, agent_id, claude_id)
    db.set_status(conn, agent_id, "ativa", "", force=True)
    db.add_event(conn, agent_id, "resume", msg[:500])
    conn.commit()
    return "retomado"


def _stop_if_live(agent_id: str) -> None:
    live = live_session(agent_id)
    if live and live.get("state") in LIVE_STATES:
        cb.stop(live["id"])


def pause(conn, agent_id: str) -> None:
    row = _need(conn, agent_id)
    if row["status"] in ("concluida", "arquivada"):
        raise JvError(f"{agent_id} já está {row['status']}")
    _stop_if_live(agent_id)
    db.set_status(conn, agent_id, "pausada", "pausada pelo usuário", force=True)
    conn.commit()


def archive(conn, agent_id: str) -> None:
    _need(conn, agent_id)
    _stop_if_live(agent_id)
    db.set_status(conn, agent_id, "arquivada", "", force=True)
    conn.commit()


def complete(conn, agent_id: str, summary: str) -> list[str]:
    _need(conn, agent_id)
    conn.execute("UPDATE agents SET summary=? WHERE id=?", (summary, agent_id))
    db.set_status(conn, agent_id, "concluida", "", force=True)
    db.add_event(conn, agent_id, "complete", summary)
    conn.commit()
    return start_dependents(conn, agent_id)


def start_dependents(conn, agent_id: str) -> list[str]:
    started = []
    for dep in conn.execute(
        "SELECT id FROM agents WHERE depends_on=? AND status='agendada'", (agent_id,)
    ).fetchall():
        launch(conn, dep["id"])
        started.append(dep["id"])
    return started


def checkpoint(conn, agent_id: str, text: str) -> Path:
    _need(conn, agent_id)
    p = db.plan_path(agent_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(p)  # atômico: uma queda no meio não corrompe o plano
    db.add_event(conn, agent_id, "checkpoint", text[:500])
    conn.execute("UPDATE agents SET updated_at=?, last_seen_at=? WHERE id=?", (db.now(), db.now(), agent_id))
    conn.commit()
    return p


def note(conn, agent_id: str, kind: str, text: str) -> None:
    _need(conn, agent_id)
    db.add_event(conn, agent_id, f"note:{kind}", text)
    if kind == "bloqueio":
        db.set_status(conn, agent_id, "pendente", f"bloqueio: {text[:200]}")
    conn.commit()


def add_decision(conn, agent_id: str, question: str, options: list[str], impact: int) -> int:
    _need(conn, agent_id)
    impact = max(1, min(3, impact))
    cur = conn.execute(
        "INSERT INTO decisions(agent_id, question, options, impact, created_at) VALUES (?,?,?,?,?)",
        (agent_id, question, json.dumps(options, ensure_ascii=False), impact, db.now()),
    )
    db.add_event(conn, agent_id, "decision", question)
    db.set_status(conn, agent_id, "pendente", "decisão aberta")
    conn.commit()
    return cur.lastrowid


def answer_decision(conn, decision_id: int, answer: str, deliver: bool = True) -> dict:
    d = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if d is None:
        raise JvError(f"decisão #{decision_id} não existe")
    if d["status"] != "aberta":
        raise JvError(f"decisão #{decision_id} já foi respondida")
    conn.execute(
        "UPDATE decisions SET status='respondida', answer=?, answered_at=? WHERE id=?",
        (answer, db.now(), decision_id),
    )
    db.add_event(conn, d["agent_id"], "answer", f"#{decision_id}: {answer}")
    conn.commit()
    agent_id = d["agent_id"]
    msg = f"Resposta do usuário à decisão #{decision_id} ({d['question']}): {answer}"
    result = {"agent": agent_id, "session": session_name(agent_id), "message": msg}
    if not deliver:
        result["delivery"] = "não entregue"
        return result
    live = live_session(agent_id)
    if live and live.get("state") in LIVE_STATES:
        # sessão viva: quem entrega é o Jarvinho, via SendMessage
        result["delivery"] = "sendmessage"
    else:
        resume(conn, agent_id, message=msg)
        result["delivery"] = "retomado com a resposta"
    return result


def _need(conn, agent_id: str):
    row = db.get_agent(conn, agent_id)
    if row is None:
        raise JvError(f"agente desconhecido: {agent_id}")
    return row
