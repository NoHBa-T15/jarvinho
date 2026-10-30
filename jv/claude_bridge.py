"""Único ponto do Jarvinho que chama o binário `claude`.

A interface de agent view está em research preview; se ela mudar, o conserto fica aqui.
`JV_CLAUDE_BIN` troca o binário (os testes usam um Claude falso).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

BG_LINE = re.compile(r"backgrounded\s*·\s*([0-9a-f]{6,})\s*·\s*(\S+)")


class ClaudeError(RuntimeError):
    pass


def _bin() -> str:
    return os.environ.get("JV_CLAUDE_BIN", "claude")


def _run(args: list[str], cwd: str | None = None, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(
            [_bin(), *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise ClaudeError(f"binário do Claude não encontrado: {_bin()}") from e
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ClaudeError(f"claude {' '.join(args[:2])} falhou ({proc.returncode}): {out.strip()[:500]}")
    return out


def _config_path() -> Path:
    return Path(os.environ.get("JV_CLAUDE_CONFIG", Path.home() / ".claude.json"))


def trust(path: str) -> None:
    """Marca a pasta como confiável, senão `claude --bg` recusa rodar nela."""
    cfg = _config_path()
    data = json.loads(cfg.read_text()) if cfg.exists() else {}
    proj = data.setdefault("projects", {}).setdefault(str(Path(path).resolve()), {})
    if proj.get("hasTrustDialogAccepted"):
        return
    proj["hasTrustDialogAccepted"] = True
    # escrita atômica: o próprio Claude lê esse arquivo o tempo todo
    fd, tmp = tempfile.mkstemp(dir=cfg.parent, prefix=".claude.json.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, cfg)


def _common(name, settings, system_prompt, mode) -> list[str]:
    args = ["--bg", "--name", name, "--permission-mode", mode]
    if settings:
        args += ["--settings", str(settings)]
    if system_prompt:
        args += ["--append-system-prompt", system_prompt]
    return args


def spawn(name: str, cwd: str, prompt: str, settings=None, system_prompt=None,
          mode: str = "acceptEdits") -> str:
    """Dispara uma sessão em segundo plano. Devolve o id curto do agent view."""
    out = _run([*_common(name, settings, system_prompt, mode), prompt], cwd=cwd)
    m = BG_LINE.search(out)
    if not m:
        raise ClaudeError(f"não entendi a saída do --bg: {out.strip()[:300]}")
    return m.group(1)


def resume(session_id: str, name: str, cwd: str, prompt: str, settings=None,
           system_prompt=None, mode: str = "acceptEdits") -> str:
    """Retoma uma conversa parada em segundo plano, com uma nova mensagem."""
    out = _run([*_common(name, settings, system_prompt, mode), "--resume", session_id, prompt], cwd=cwd)
    m = BG_LINE.search(out)
    if not m:
        raise ClaudeError(f"não entendi a saída do --bg --resume: {out.strip()[:300]}")
    return m.group(1)


def sessions(include_done: bool = True) -> list[dict]:
    args = ["agents", "--json"] + (["--all"] if include_done else [])
    out = _run(args, timeout=30)
    start = out.find("[")
    if start < 0:
        raise ClaudeError(f"saída inesperada de claude agents: {out[:200]}")
    return [s for s in json.loads(out[start:]) if s.get("kind") == "background"]


def stop(claude_id: str) -> None:
    _run(["stop", claude_id], timeout=30)


def transcript_tail(path: str | None, n: int = 12) -> list[dict]:
    """Últimas falas da conversa, para o detalhe em tempo real do painel."""
    if not path or not Path(path).exists():
        return []
    lines = Path(path).read_text(errors="replace").splitlines()[-400:]
    out: list[dict] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        role = msg.get("role") or rec.get("type")
        content = msg.get("content")
        if role not in ("user", "assistant") or content is None:
            continue
        if isinstance(content, str):
            out.append({"ts": rec.get("timestamp"), "role": role, "text": content[:2000]})
            continue
        for block in content:
            t = block.get("type")
            if t == "text" and block.get("text", "").strip():
                out.append({"ts": rec.get("timestamp"), "role": role, "text": block["text"][:2000]})
            elif t == "tool_use":
                inp = block.get("input") or {}
                hint = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                out.append({"ts": rec.get("timestamp"), "role": "tool",
                            "text": f"{block.get('name')}: {str(hint)[:300]}"})
    return out[-n:]
