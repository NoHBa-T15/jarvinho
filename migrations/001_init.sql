-- Hub do Jarvinho, v1 do esquema.

CREATE TABLE agents (
    id              TEXT PRIMARY KEY,          -- slug curto escolhido no spawn
    project_path    TEXT NOT NULL,
    objective       TEXT NOT NULL,
    done_criteria   TEXT NOT NULL DEFAULT '',
    permission_mode TEXT NOT NULL DEFAULT 'acceptEdits',
    -- ativa | pendente | pausada | concluida | arquivada | interrompida | agendada
    status          TEXT NOT NULL,
    status_reason   TEXT NOT NULL DEFAULT '',
    depends_on      TEXT REFERENCES agents(id),
    claude_id       TEXT,                      -- id curto do agent view (8 chars)
    session_id      TEXT,                      -- uuid da conversa
    transcript_path TEXT,
    last_message    TEXT NOT NULL DEFAULT '',  -- última fala do agente (hook Stop)
    summary         TEXT NOT NULL DEFAULT '',  -- resumo de jv complete
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    archived_at     TEXT,
    last_seen_at    TEXT                       -- último sinal de vida (qualquer hook)
);

CREATE TABLE events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id  TEXT NOT NULL REFERENCES agents(id),
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,                   -- spawn, hook:Stop, status, checkpoint, note, ...
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX events_agent_ts ON events(agent_id, ts);

CREATE TABLE decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL REFERENCES agents(id),
    question    TEXT NOT NULL,
    options     TEXT NOT NULL DEFAULT '[]',    -- JSON array
    impact      INTEGER NOT NULL DEFAULT 2,    -- 1 baixo, 2 médio, 3 alto
    status      TEXT NOT NULL DEFAULT 'aberta',-- aberta | respondida
    answer      TEXT,
    created_at  TEXT NOT NULL,
    answered_at TEXT
);
CREATE INDEX decisions_open ON decisions(status, impact, created_at);
