"""Comando `jv`: a porta única para o hub do Jarvinho."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import claude_bridge as cb
from . import db, hooks, ops, reconcile

ICON = {"ativa": "●", "pendente": "◐", "pausada": "Ⅱ", "concluida": "✓",
        "arquivada": "▪", "interrompida": "✕", "agendada": "◷"}
ORDER = ["interrompida", "pendente", "ativa", "agendada", "pausada", "concluida", "arquivada"]


def _age(ts: str | None) -> str:
    if not ts:
        return "-"
    secs = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 5400:
        return f"{int(secs // 60)}min"
    if secs < 172800:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"


def _out(args, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str) if getattr(args, "json", False) else text)


def agent_dict(conn, row) -> dict:
    d = dict(row)
    d["open_decisions"] = len(db.open_decisions(conn, row["id"]))
    return d


# ---------- comandos ----------

def cmd_init(args, conn):
    link = Path.home() / ".local" / "bin" / "jv"
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists():
        link.symlink_to(ops.JV_BIN)
    print(f"hub pronto em {db.db_path()} (esquema v{conn.execute('PRAGMA user_version').fetchone()[0]})")
    print(f"comando jv em {link}")
    if str(link.parent) not in os.environ.get("PATH", ""):
        print(f"atenção: {link.parent} não está no PATH")


def cmd_spawn(args, conn):
    st = ops.spawn(conn, args.id, args.project, args.objective, args.done or "", args.mode, args.after)
    row = db.get_agent(conn, args.id)
    _out(args, agent_dict(conn, row),
         f"{args.id}: {st}" + (f" (sessão {ops.session_name(args.id)}, {row['claude_id']})" if row["claude_id"] else ""))


def cmd_list(args, conn):
    sql = "SELECT * FROM agents" + ("" if args.all else " WHERE status != 'arquivada'")
    rows = sorted(conn.execute(sql).fetchall(), key=lambda r: (ORDER.index(r["status"]), r["updated_at"]))
    data = [agent_dict(conn, r) for r in rows]
    lines = [f"{ICON[r['status']]} {r['id']:<22} {r['status']:<12} {_age(r['updated_at']):>6}  "
             f"{(r['status_reason'] or r['objective'])[:70]}" for r in rows]
    _out(args, data, "\n".join(lines) or "nenhum agente")


def cmd_status(args, conn):
    rows = conn.execute("SELECT status, COUNT(*) n FROM agents GROUP BY status").fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    decisions = [dict(d) for d in db.open_decisions(conn)]
    attention = [dict(r) for r in conn.execute(
        "SELECT id, status, status_reason, last_message FROM agents WHERE status IN ('interrompida','pendente') ORDER BY updated_at"
    ).fetchall()]
    done_recent = [dict(r) for r in conn.execute(
        "SELECT id, summary, completed_at FROM agents WHERE status='concluida' ORDER BY completed_at DESC LIMIT 5"
    ).fetchall()]
    data = {"counts": counts, "decisions": decisions, "attention": attention, "recently_done": done_recent}
    parts = [" · ".join(f"{ICON[s]} {counts[s]} {s}" for s in ORDER if counts.get(s))]
    if decisions:
        parts.append("\nDecisões (impacto, depois espera):")
        for d in decisions:
            opts = " / ".join(json.loads(d["options"])) or "resposta livre"
            parts.append(f"  #{d['id']} [{'!' * d['impact']}] {d['agent_id']}: {d['question']}  ({opts}) · {_age(d['created_at'])}")
    others = [a for a in attention if a["status"] == "interrompida" or a["status_reason"] != "decisão aberta"]
    if others:
        parts.append("\nPrecisam de atenção:")
        for a in others:
            parts.append(f"  {ICON[a['status']]} {a['id']}: {a['status_reason']}")
    _out(args, data, "\n".join(p for p in parts if p) or "nenhum agente ainda")


def cmd_show(args, conn):
    row = ops._need(conn, args.id)
    plan = db.plan_path(args.id)
    data = agent_dict(conn, row)
    data["plan"] = plan.read_text() if plan.exists() else ""
    data["decisions"] = [dict(d) for d in conn.execute(
        "SELECT * FROM decisions WHERE agent_id=? ORDER BY id DESC LIMIT 10", (args.id,)).fetchall()]
    data["events"] = [dict(e) for e in conn.execute(
        "SELECT ts, kind, detail FROM events WHERE agent_id=? ORDER BY id DESC LIMIT ?", (args.id, args.events)).fetchall()]
    data["tail"] = cb.transcript_tail(row["transcript_path"], args.tail)
    text = [f"{ICON[row['status']]} {row['id']} · {row['status']} {('· ' + row['status_reason']) if row['status_reason'] else ''}",
            f"projeto: {row['project_path']}", f"missão: {row['objective']}",
            f"pronto quando: {row['done_criteria'] or '-'}",
            f"sessão: {ops.session_name(row['id'])} ({row['claude_id'] or '-'}) · criada {row['created_at']} · visto há {_age(row['last_seen_at'])}"]
    if data["plan"]:
        text += ["", "plano:", data["plan"].strip()]
    if data["tail"]:
        text += ["", "últimas falas:"] + [f"  [{t['role']}] {t['text'][:200]}" for t in data["tail"]]
    _out(args, data, "\n".join(text))


def cmd_pause(args, conn):
    ops.pause(conn, args.id)
    print(f"{args.id}: pausada")


def cmd_resume(args, conn):
    print(f"{args.id}: {ops.resume(conn, args.id, args.message, args.force)}")


def cmd_archive(args, conn):
    ops.archive(conn, args.id)
    print(f"{args.id}: arquivada")


def cmd_complete(args, conn):
    started = ops.complete(conn, args.id, args.summary)
    print(f"{args.id}: concluida" + (f"; disparados: {', '.join(started)}" if started else ""))


def cmd_checkpoint(args, conn):
    if args.text is not None:
        text = args.text
    elif args.file is not None:
        text = Path(args.file).read_text()
    else:
        text = sys.stdin.read()
    if not text.strip():
        raise ops.JvError("checkpoint vazio: mande o plano pela entrada padrão")
    print(f"checkpoint salvo em {ops.checkpoint(conn, args.id, text)}")


def cmd_plan(args, conn):
    ops._need(conn, args.id)
    p = db.plan_path(args.id)
    print(p.read_text() if p.exists() else "(sem checkpoint ainda)")


def cmd_note(args, conn):
    ops.note(conn, args.id, args.kind, args.text)
    print("anotado")


def cmd_decision(args, conn):
    if args.action == "add":
        did = ops.add_decision(conn, args.id, args.question, args.option or [], args.impact)
        print(f"decisão #{did} registrada; aguarde a resposta do Jarvinho e encerre o turno")
    elif args.action == "list":
        ds = [dict(d) for d in db.open_decisions(conn, args.id)]
        _out(args, ds, "\n".join(f"#{d['id']} [{'!' * d['impact']}] {d['agent_id']}: {d['question']}" for d in ds) or "nenhuma decisão aberta")
    elif args.action == "answer":
        res = ops.answer_decision(conn, args.decision_id, args.answer, deliver=not args.no_deliver)
        if res["delivery"] == "sendmessage":
            res["next"] = f"envie por SendMessage para {res['session']}: {res['message']}"
        _out(args, res, res.get("next") or f"#{args.decision_id}: {res['delivery']}")


def cmd_reconcile(args, conn):
    rep = reconcile.run(conn, do_resume=args.resume)
    _out(args, rep, "\n".join(f"{r['agent']}: {r['action']}" + (f" ({r['reason']})" if r.get("reason") else "") for r in rep) or "tudo em ordem")


def cmd_panel(args, conn):
    from . import panel
    if args.action == "serve":
        panel.serve(args.host, args.port)
    else:
        out = Path(args.out)
        out.write_text(panel.build_static())
        print(f"painel salvo em {out}")


def cmd_backup(args, conn):
    print(db.backup(conn))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jv", description="Jarvinho: orquestrador de agentes Claude Code")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, aliases=()):
        sp = sub.add_parser(name, help=help_, aliases=list(aliases))
        sp.set_defaults(fn=fn)
        return sp

    add("init", cmd_init, "cria o hub e instala o comando jv")
    sp = add("spawn", cmd_spawn, "cria e dispara um agente")
    sp.add_argument("id"); sp.add_argument("--project", required=True)
    sp.add_argument("--objective", required=True); sp.add_argument("--done")
    sp.add_argument("--mode", default="acceptEdits",
                    choices=["default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"])
    sp.add_argument("--after", help="só começa quando este agente concluir")
    sp.add_argument("--json", action="store_true")
    sp = add("list", cmd_list, "lista agentes"); sp.add_argument("--all", action="store_true"); sp.add_argument("--json", action="store_true")
    sp = add("status", cmd_status, "resumo e fila de decisões"); sp.add_argument("--json", action="store_true")
    sp = add("show", cmd_show, "detalhe de um agente")
    sp.add_argument("id"); sp.add_argument("--tail", type=int, default=12); sp.add_argument("--events", type=int, default=15)
    sp.add_argument("--json", action="store_true")
    add("pause", cmd_pause, "pausa um agente").add_argument("id")
    sp = add("resume", cmd_resume, "retoma um agente parado")
    sp.add_argument("id"); sp.add_argument("--message"); sp.add_argument("--force", action="store_true")
    add("archive", cmd_archive, "arquiva um agente").add_argument("id")
    # "complete" é um builtin do bash e o guarda de worktree do Claude Code bloqueia; o nome é finish
    sp = add("finish", cmd_complete, "(agente) marca a missão como concluída", aliases=["complete"])
    sp.add_argument("id"); sp.add_argument("--summary", required=True)
    sp = add("checkpoint", cmd_checkpoint, "(agente) salva o plano a partir da entrada padrão")
    sp.add_argument("id"); sp.add_argument("--text"); sp.add_argument("--file")
    add("plan", cmd_plan, "mostra o último checkpoint").add_argument("id")
    sp = add("note", cmd_note, "(agente) registra uma nota ou bloqueio")
    sp.add_argument("id"); sp.add_argument("text"); sp.add_argument("--kind", default="info", choices=["info", "bloqueio"])
    sp = add("decision", cmd_decision, "decisões: add | list | answer")
    dsub = sp.add_subparsers(dest="action", required=True)
    d = dsub.add_parser("add"); d.add_argument("id"); d.add_argument("question")
    d.add_argument("--option", action="append"); d.add_argument("--impact", type=int, default=2)
    d = dsub.add_parser("list"); d.add_argument("id", nargs="?"); d.add_argument("--json", action="store_true")
    d = dsub.add_parser("answer"); d.add_argument("decision_id", type=int); d.add_argument("answer")
    d.add_argument("--no-deliver", action="store_true"); d.add_argument("--json", action="store_true")
    sp = add("reconcile", cmd_reconcile, "detecta quedas e dispara agendados")
    sp.add_argument("--resume", action="store_true", help="retoma os interrompidos"); sp.add_argument("--json", action="store_true")
    sp = add("panel", cmd_panel, "painel: serve | build")
    psub = sp.add_subparsers(dest="action", required=True)
    s = psub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8765)
    s = psub.add_parser("build"); s.add_argument("out")
    add("backup", cmd_backup, "copia o hub para ~/.jarvinho/backups")
    sp = add("hook", None, "(interno) entrada dos hooks dos agentes"); sp.add_argument("event")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "hook":
        return hooks.main(args.event)
    conn = db.connect()
    try:
        args.fn(args, conn)
        return 0
    except (ops.JvError, cb.ClaudeError, KeyError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
