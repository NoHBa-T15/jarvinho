"""Testes do Jarvinho com o Claude falso. Rodar: python3 -m unittest discover -s tests"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jv import cli, db, hooks, ops, panel, reconcile  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.project = t / "proj"
        self.project.mkdir()
        self.state = t / "fake_state.json"
        self.env = {
            "JARVINHO_HOME": str(t / "home"),
            "JV_CLAUDE_BIN": str(ROOT / "tests" / "fake_claude.py"),
            "JV_CLAUDE_CONFIG": str(t / "claude.json"),
            "FAKE_CLAUDE_STATE": str(self.state),
        }
        self._old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # utilidades
    def fake(self):
        return json.loads(self.state.read_text())

    def set_fake_state(self, name, state=None, remove=False):
        st = self.fake()
        if remove:
            st["sessions"] = [s for s in st["sessions"] if s["name"] != name]
        else:
            for s in st["sessions"]:
                if s["name"] == name:
                    s["state"] = state
        self.state.write_text(json.dumps(st))

    def spawn(self, aid="a1", **kw):
        return ops.spawn(self.conn, aid, str(self.project), kw.pop("objective", "fazer X"), **kw)

    def hook(self, event, aid="a1", **payload):
        row = db.get_agent(self.conn, aid)
        payload.setdefault("session_id", row["session_id"])
        payload.setdefault("session_title", f"jv-{aid}")
        hooks.handle(event, payload)
        self.conn.commit()
        return db.get_agent(self.conn, aid)

    def status(self, aid="a1"):
        return db.get_agent(self.conn, aid)["status"]


class TestSpawn(Base):
    def test_spawn_dispara_e_registra(self):
        self.assertEqual(self.spawn(done="teste passa"), "ativa")
        row = db.get_agent(self.conn, "a1")
        sess = self.fake()["sessions"][0]
        self.assertEqual(sess["name"], "jv-a1")
        self.assertEqual(row["claude_id"], sess["id"])
        self.assertEqual(row["session_id"], sess["sessionId"])
        settings = json.loads(Path(sess["settings"]).read_text())
        self.assertIn("Stop", settings["hooks"])
        self.assertIn("jv", settings["hooks"]["Stop"][0]["hooks"][0]["command"])
        cfg = json.loads(Path(os.environ["JV_CLAUDE_CONFIG"]).read_text())
        self.assertTrue(cfg["projects"][str(self.project.resolve())]["hasTrustDialogAccepted"])

    def test_id_invalido_e_duplicado(self):
        with self.assertRaises(ops.JvError):
            ops.spawn(self.conn, "A B", str(self.project), "x")
        self.spawn()
        with self.assertRaises(ops.JvError):
            self.spawn()

    def test_protocolo_vai_no_system_prompt(self):
        self.spawn(done="critério Z")
        row = db.get_agent(self.conn, "a1")
        sp = ops.system_prompt(row)
        self.assertIn("a1", sp)
        self.assertIn("critério Z", sp)
        self.assertNotIn("{agent_id}", sp)


class TestHooks(Base):
    def test_stop_sem_conclusao_vira_pendente(self):
        self.spawn()
        row = self.hook("Stop", last_assistant_message="parei aqui")
        self.assertEqual(row["status"], "pendente")
        self.assertEqual(row["last_message"], "parei aqui")

    def test_decisao_segura_pendente_ate_resposta(self):
        self.spawn()
        ops.add_decision(self.conn, "a1", "A ou B?", ["A", "B"], 3)
        self.assertEqual(self.hook("PostToolUse")["status"], "pendente")
        self.assertEqual(self.hook("Stop")["status"], "pendente")
        res = ops.answer_decision(self.conn, 1, "A")
        self.assertEqual(res["delivery"], "sendmessage")  # sessão viva
        self.assertEqual(self.hook("UserPromptSubmit")["status"], "ativa")

    def test_hook_mapeia_por_nome_e_atualiza_ids(self):
        self.spawn()
        self.conn.execute("UPDATE agents SET session_id=NULL WHERE id='a1'")
        self.conn.commit()
        hooks.handle("SessionStart", {"session_id": "novo-uuid", "session_title": "jv-a1",
                                      "transcript_path": "/tmp/x.jsonl"})
        row = db.get_agent(self.conn, "a1")
        self.assertEqual(row["session_id"], "novo-uuid")
        self.assertEqual(row["transcript_path"], "/tmp/x.jsonl")
        self.assertIsNotNone(row["last_seen_at"])

    def test_hook_de_sessao_alheia_e_lixo_nao_quebram(self):
        self.assertIsNone(hooks.handle("Stop", {"session_id": "x", "session_title": "outra"}))
        sys.stdin = io.StringIO("{nao e json")
        try:
            self.assertEqual(hooks.main("Stop"), 0)
        finally:
            sys.stdin = sys.__stdin__
        self.assertTrue((db.home() / "hook-errors.log").exists())

    def test_permissao_vira_pendente(self):
        self.spawn()
        row = self.hook("Notification", message="Claude needs your permission to use Bash")
        self.assertEqual(row["status"], "pendente")
        self.assertIn("permissão", row["status_reason"])

    def test_concluida_nao_e_sobrescrita(self):
        self.spawn()
        ops.complete(self.conn, "a1", "feito")
        self.assertEqual(self.hook("Stop")["status"], "concluida")
        self.assertEqual(self.hook("UserPromptSubmit")["status"], "concluida")


class TestDecisions(Base):
    def test_fila_por_impacto_depois_espera(self):
        self.spawn("a1"); self.spawn("a2")
        ops.add_decision(self.conn, "a1", "baixa antiga", [], 1)
        ops.add_decision(self.conn, "a2", "alta", [], 3)
        ops.add_decision(self.conn, "a1", "media", [], 2)
        qs = [d["question"] for d in db.open_decisions(self.conn)]
        self.assertEqual(qs, ["alta", "media", "baixa antiga"])

    def test_resposta_com_agente_parado_retoma_com_a_resposta(self):
        self.spawn()
        ops.add_decision(self.conn, "a1", "A ou B?", ["A", "B"], 2)
        self.set_fake_state("jv-a1", remove=True)
        res = ops.answer_decision(self.conn, 1, "B")
        self.assertEqual(res["delivery"], "retomado com a resposta")
        last = self.fake()["sessions"][-1]
        self.assertIn("B", last["prompt"])
        self.assertEqual(last["sessionId"], db.get_agent(self.conn, "a1")["session_id"])


class TestReconcile(Base):
    def test_queda_detectada_e_retomada(self):
        self.spawn()
        sid = db.get_agent(self.conn, "a1")["session_id"]
        self.set_fake_state("jv-a1", remove=True)  # kill -9 / reboot
        rep = reconcile.run(self.conn)
        self.assertEqual(self.status(), "interrompida")
        self.assertEqual(rep[0]["action"], "interrompida")
        rep = reconcile.run(self.conn, do_resume=True)
        self.assertEqual(self.status(), "ativa")
        last = self.fake()["sessions"][-1]
        self.assertEqual(last["sessionId"], sid)  # mesma conversa
        self.assertIn("jv plan a1", last["prompt"])

    def test_failed_vira_interrompida_e_blocked_vira_pendente(self):
        self.spawn("a1"); self.spawn("a2")
        self.set_fake_state("jv-a1", "failed")
        self.set_fake_state("jv-a2", "blocked")
        reconcile.run(self.conn)
        self.assertEqual(self.status("a1"), "interrompida")
        self.assertEqual(self.status("a2"), "pendente")

    def test_pausada_e_ignorada(self):
        self.spawn()
        ops.pause(self.conn, "a1")
        self.assertEqual(self.fake()["sessions"][0]["state"], "stopped")
        reconcile.run(self.conn, do_resume=True)
        self.assertEqual(self.status(), "pausada")
        ops.resume(self.conn, "a1")
        self.assertEqual(self.status(), "ativa")

    def test_resume_recusa_sessao_viva_trabalhando(self):
        self.spawn()
        with self.assertRaises(ops.JvError):
            ops.resume(self.conn, "a1")

    def test_dependencia_dispara_quando_concluir(self):
        self.spawn("a1")
        self.assertEqual(self.spawn("a2", after="a1"), "agendada")
        self.assertEqual(len(self.fake()["sessions"]), 1)
        started = ops.complete(self.conn, "a1", "pronto")
        self.assertEqual(started, ["a2"])
        self.assertEqual(self.status("a2"), "ativa")


class TestHubEPainel(Base):
    def test_checkpoint_atomico_e_plano(self):
        self.spawn()
        ops.checkpoint(self.conn, "a1", "1. [x] ler\n2. [>] escrever")
        self.assertIn("[>] escrever", db.plan_path("a1").read_text())

    def test_migracao_idempotente_e_backup(self):
        self.assertEqual(db.migrate(self.conn), 1)
        dest = db.backup(self.conn)
        self.assertTrue(dest.exists())
        self.assertEqual(sqlite3.connect(dest).execute("PRAGMA user_version").fetchone()[0], 1)

    def test_painel_estatico_embute_dados(self):
        self.spawn(objective="missão <b>especial</b>")
        html = panel.build_static()
        self.assertNotIn("/*JV_STATIC*/null", html)
        self.assertIn("missão <b>especial<\\/b>", html)

    def test_cli_status_e_list(self):
        self.spawn()
        ops.add_decision(self.conn, "a1", "Qual banco?", ["Postgres", "SQLite"], 3)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["status"]), 0)
            self.assertEqual(cli.main(["list", "--json"]), 0)
        out = buf.getvalue()
        self.assertIn("#1 [!!!] a1: Qual banco?", out)
        self.assertIn('"open_decisions": 1', out)


if __name__ == "__main__":
    unittest.main()
