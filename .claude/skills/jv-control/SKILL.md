---
name: jv-control
description: Pausa, retoma, arquiva agentes do Jarvinho e entrega as respostas do usuário às decisões. Use quando o usuário responder a uma decisão ("vai de A", "#3: Postgres"), ou pedir para pausar, retomar, continuar, parar ou arquivar um agente.
---

# Controlar agentes

## Responder uma decisão

1. Identifique a decisão (`#id`). Se o usuário não disser o número e houver mais de uma aberta
   do mesmo agente, pergunte qual.
2. Rode `jv decision answer <id> "<resposta>" --json`.
3. Veja o campo `delivery`:
   - `sendmessage`: o agente está vivo. Envie `message` com a ferramenta **SendMessage** para a
     sessão `session` (`jv-<agente>`). Não reescreva a resposta: mande o texto de `message`.
   - `retomado com a resposta`: o agente estava parado e já foi retomado com a resposta. Nada a fazer.
4. Confirme ao usuário em uma linha.

## Pausar, retomar, arquivar

| Pedido | Comando |
|---|---|
| pausar | `jv pause <id>` |
| retomar um agente parado | `jv resume <id> [--message "instrução"]` |
| falar com um agente vivo | SendMessage para `jv-<id>` |
| arquivar | `jv archive <id>` (confirme antes: some do painel principal) |
| reabrir concluído ou arquivado | `jv resume <id> --force --message "..."` (só com pedido explícito) |

Se `jv resume` recusar porque o agente está vivo, use SendMessage em vez de insistir.

## Mudar a missão

Mudança de escopo é decisão do usuário. Mande a nova instrução por SendMessage (ou `jv resume
--message`) e registre: `jv note <id> "missão ajustada: ..."`.
