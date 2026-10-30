"""Hub do Jarvinho: SQLite com migrações versionadas e backup antes de migrar."""
from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"

STATUSES = ("ativa", "pendente", "pausada", "concluida", "arquivada", "interrompida", "agendada")
# Estados que o usuário escolheu ou que encerram a missão: hooks e reconcile não os sobrescrevem.
STICKY = {"pausada", "concluida", "arquivada"}


def home() -> Path:
    return Path(os.environ.get("JARVINHO_HOME", Path.home() / ".jarvinho"))


def db_path() -> Path:
    return home() / "hub.db"


def plans_dir() -> Path:
    return home() / "plans"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    plans_dir().mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL sobrevive melhor a quedas e deixa o painel ler enquanto hooks escrevem.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def _migrations() -> list[tuple[int, Path]]:
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        out.append((int(p.name.split("_", 1)[0]), p))
    return out


def migrate(conn: sqlite3.Connection) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = [(v, p) for v, p in _migrations() if v > current]
    if not pending:
        return current
    if current > 0:  # há dados do usuário: backup antes de mexer no esquema
        backup(conn, tag=f"pre-migration-v{current}")
    for version, path in pending:
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={version}")
        conn.commit()
    return pending[-1][0]


def backup(conn: sqlite3.Connection, tag: str = "manual") -> Path:
    bdir = home() / "backups"
    bdir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = bdir / f"hub-{stamp}-{tag}.db"
    target = sqlite3.connect(dest)
    with target:
        conn.backup(target)
    target.close()
    # mantém só os 20 backups mais recentes
    for old in sorted(bdir.glob("hub-*.db"))[:-20]:
        old.unlink(missing_ok=True)
    return dest


# ---------- agentes ----------

def get_agent(conn, agent_id: str):
    return conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()


def find_agent_by_session(conn, session_id: str | None, name: str | None):
    if session_id:
        row = conn.execute("SELECT * FROM agents WHERE session_id=?", (session_id,)).fetchone()
        if row:
            return row
    if name and name.startswith("jv-"):
        return get_agent(conn, name[3:])
    return None


def add_event(conn, agent_id: str, kind: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO events(agent_id, ts, kind, detail) VALUES (?,?,?,?)",
        (agent_id, now(), kind, detail[:4000]),
    )


def set_status(conn, agent_id: str, status: str, reason: str = "", force: bool = False) -> bool:
    """Muda o status respeitando os estados fixos. Devolve True se mudou."""
    assert status in STATUSES, status
    row = get_agent(conn, agent_id)
    if row is None:
        raise KeyError(agent_id)
    if not force and row["status"] in STICKY and status != row["status"]:
        return False
    if row["status"] == status and row["status_reason"] == reason:
        return False
    extra = ""
    if status == "concluida":
        extra = ", completed_at=:now"
    elif status == "arquivada":
        extra = ", archived_at=:now"
    conn.execute(
        f"UPDATE agents SET status=:s, status_reason=:r, updated_at=:now{extra} WHERE id=:id",
        {"s": status, "r": reason, "now": now(), "id": agent_id},
    )
    add_event(conn, agent_id, "status", f"{row['status']} -> {status}" + (f" ({reason})" if reason else ""))
    return True


def open_decisions(conn, agent_id: str | None = None):
    sql = """SELECT d.*, a.project_path FROM decisions d JOIN agents a ON a.id=d.agent_id
             WHERE d.status='aberta' {f} ORDER BY d.impact DESC, d.created_at ASC"""
    if agent_id:
        return conn.execute(sql.format(f="AND d.agent_id=?"), (agent_id,)).fetchall()
    return conn.execute(sql.format(f="")).fetchall()


def plan_path(agent_id: str) -> Path:
    return plans_dir() / f"{agent_id}.md"
