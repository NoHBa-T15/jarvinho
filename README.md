# Jarvinho

Orquestrador pessoal de agentes Claude Code. Você conversa só com o Jarvinho; ele despacha
agentes (sessões em segundo plano, uma por missão, cada uma no repo do seu projeto), guarda o
estado de tudo num hub local e te devolve **só decisões e o essencial**.

> v0: o núcleo funciona de ponta a ponta (disparar, acompanhar, decidir, cair, retomar, encadear).
> Memória que aprende, reflexão diária, revisão independente e orçamentos ficam para a v1.

## Requisitos

- Linux, macOS ou WSL 2 (tudo do Jarvinho e dos agentes no mesmo lado: sessões no WSL e no
  Windows nativo não se enxergam).
- Python 3.10+ (só biblioteca padrão).
- Claude Code ≥ 2.1.224 com **agent view** (`claude agents`) e mensagens entre sessões.

## Instalação

```bash
git clone https://github.com/gabriel-carvalho-t15/jarvinho ~/jarvinho
~/jarvinho/bin/jv init          # cria ~/.jarvinho/hub.db e o link ~/.local/bin/jv
cd ~/jarvinho && claude         # esta sessão é o Jarvinho
```

Ao abrir, um hook roda `jv reconcile --resume` e `jv status`: o Jarvinho já começa sabendo o
que caiu e o que espera por você.

## Uso

Fale normalmente com a sessão do Jarvinho ("põe um agente para corrigir o bug X no repo Y",
"o que precisa de mim?", "vai de Postgres na #3", "pausa o api-auth"). As skills `jv-spawn`,
`jv-status`, `jv-control` e `jv-panel` traduzem isso para o comando `jv`:

| Comando | O que faz |
|---|---|
| `jv spawn <id> --project <pasta> --objective "..." --done "..." [--after <id>]` | cria e dispara um agente (ou agenda, se depende de outro) |
| `jv status` | contagem por status, fila de decisões (impacto, depois espera) e o que precisa de atenção |
| `jv list [--all]` / `jv show <id>` | lista agentes / detalhe com plano, últimas falas, decisões e eventos |
| `jv decision answer <n> "resposta"` | responde; entrega por mensagem se o agente está vivo, ou o retoma com a resposta |
| `jv pause` / `jv resume` / `jv archive <id>` | ciclo de vida |
| `jv reconcile [--resume]` | detecta quedas e dispara agendados cuja dependência concluiu |
| `jv panel serve` / `jv panel build <arquivo>` | painel ao vivo em `http://127.0.0.1:8765` / foto estática |

Os agentes usam `checkpoint`, `decision add`, `note` e `finish` (ver `agent/AGENT_PROTOCOL.md`).

## Status de um agente

| Status | Quando |
|---|---|
| ativa | trabalhando |
| pendente | esperando você: decisão aberta, permissão, bloqueio ou turno encerrado sem concluir |
| interrompida | a sessão caiu (reboot, WSL, processo morto) e ainda não foi retomada |
| agendada | espera outro agente concluir |
| pausada | você pausou |
| concluida | o agente cumpriu o critério de pronto (`finish`) |
| arquivada | fora da visão principal |

Hooks e reconcile nunca sobrescrevem `pausada`, `concluida` e `arquivada`.

## Arquitetura

```
você ──conversa──▶ Jarvinho (sessão Claude Code nesta pasta)
                     │  jv spawn/resume ──▶ claude --bg  (um agente por missão, no repo do projeto)
                     │  SendMessage ──────▶ agentes vivos
                     ▼
                 ~/.jarvinho/hub.db ◀── hooks dos agentes (status, sinal de vida, última fala)
                     │             ◀── jv checkpoint/decision/finish (chamados pelos agentes)
                     ▼
                 painel (servidor local só-leitura)
```

| Arquivo | Papel |
|---|---|
| `CLAUDE.md` | quem o Jarvinho é, o que decide sozinho e o que é do usuário |
| `.claude/settings.json` | permissões e o hook de abertura (reconcile + status) |
| `.claude/skills/jv-*` | spawn, status, control, panel |
| `agent/AGENT_PROTOCOL.md` | contrato que cada agente recebe no prompt de sistema |
| `agent/settings.template.json` | hooks e permissões injetados em cada agente via `--settings` |
| `jv/cli.py` | comando `jv` |
| `jv/ops.py` | operações: spawn, resume, pause, archive, finish, decisões, checkpoint |
| `jv/hooks.py` | recebe os hooks dos agentes; nunca falha nem trava o agente |
| `jv/reconcile.py` | compara hub × agent view, marca quedas, retoma, dispara dependentes |
| `jv/claude_bridge.py` | único ponto que chama `claude` (agent view está em preview) |
| `jv/db.py` + `migrations/` | esquema versionado, WAL, backup antes de migrar |
| `jv/panel.py` + `panel/index.html` | painel: cards, filtros, fila de decisões, detalhe ao vivo, metadados |
| `tests/` | 20 testes com um Claude falso (sem gastar tokens) |

Estado do usuário (nunca vai para o repo): `~/.jarvinho/hub.db`, `plans/<agente>.md`
(checkpoints), `agents/<agente>.settings.json`, `backups/`.

## O que o teste real mostrou

Rodado com agentes de verdade num repo de demonstração (2026-10-30):

- **Queda de processo:** o supervisor do agent view reinicia o agente sozinho; o hub nem precisa agir.
- **"Reboot"** (supervisor e agente mortos): `jv reconcile` marcou `interrompida`; a resposta a uma
  decisão retomou **a mesma conversa**, e o agente concluiu a missão.
- **Decisões:** os agentes param e perguntam quando faz sentido (um deles percebeu sozinho que o
  teste antigo quebraria e perguntou antes de agir).
- **Dependências:** `readme --after sub` disparou sozinho quando `sub` concluiu.
- **Mensagens:** resposta entregue por `SendMessage` a um agente vivo funcionou.

## Limitações conhecidas (entram na v1)

1. **Entregas ficam em branches de worktree.** O agent view isola cada agente numa branch
   `worktree-*`. Um agente dependente parte da branch principal e não vê o trabalho do anterior.
   Falta uma etapa de integração (dependente parte da branch do anterior, ou merge com decisão).
2. **Permissões pendentes** (`aguardando permissão`) hoje exigem `claude attach <id>` para aprovar.
   Liberar comandos comuns no `.claude/settings.json` de cada projeto evita a maior parte.
3. Painel sem login: só `127.0.0.1` por padrão.
4. Variáveis de ambiente não chegam aos agentes em segundo plano; o hub os identifica pelo nome
   da sessão (`jv-<id>`).

## Desenvolvimento

```bash
python3 -m unittest discover -s tests
```
