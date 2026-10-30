#!/usr/bin/env python3
"""Claude falso: imita as partes da CLI que o Jarvinho usa, sem gastar tokens.

Estado num JSON em $FAKE_CLAUDE_STATE. Os testes manipulam esse JSON para simular quedas.
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

STATE = Path(os.environ["FAKE_CLAUDE_STATE"])


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {"sessions": [], "calls": []}


def save(st):
    STATE.write_text(json.dumps(st, indent=2))


def main(argv):
    st = load()
    st["calls"].append(argv)
    if argv[:1] == ["agents"]:
        save(st)
        print(json.dumps([{"kind": "interactive", "name": "eu", "status": "busy"}] + st["sessions"]))
        return 0
    if argv[:1] == ["stop"]:
        for s in st["sessions"]:
            if s["id"] == argv[1]:
                s["state"] = "stopped"
        save(st)
        print(f"stopped {argv[1]}")
        return 0
    if "--bg" in argv:
        opts, prompt, i = {}, None, 0
        while i < len(argv):
            a = argv[i]
            if a in ("--name", "--permission-mode", "--settings", "--append-system-prompt", "--resume"):
                opts[a] = argv[i + 1]; i += 2; continue
            if a == "--bg":
                i += 1; continue
            prompt = a; i += 1
        sid = opts.get("--resume") or str(uuid.uuid4())
        short = uuid.uuid4().hex[:8]
        st["sessions"].append({
            "kind": "background", "id": short, "sessionId": sid, "name": opts["--name"],
            "cwd": os.getcwd(), "startedAt": int(time.time() * 1000), "status": "busy",
            "state": "working", "prompt": prompt, "settings": opts.get("--settings"),
            "mode": opts.get("--permission-mode"),
        })
        save(st)
        print(f"backgrounded · {short} · {opts['--name']}")
        return 0
    print(f"fake claude: comando não suportado {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
