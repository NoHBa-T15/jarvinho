---
name: jv-status
description: Resumo do que está acontecendo com os agentes do Jarvinho e a fila de decisões. Use quando o usuário perguntar "como estão as coisas", "o que está pendente", "quais acabaram", "o que precisa de mim", ou ao abrir a sessão.
---

# Resumo e fila de decisões

1. Rode `jv reconcile` primeiro (detecta quedas), depois `jv status --json`.
2. Monte a resposta nesta ordem, pulando o que estiver vazio:
   1. **Precisa de você:** decisões da fila, na ordem que o `jv` devolve (impacto, depois espera).
      Para cada uma: `#id`, agente, a pergunta e as opções numa linha.
   2. **Caiu:** agentes `interrompida`. Diga que vai retomá-los e rode `jv reconcile --resume`,
      a menos que o usuário tenha pedido para não retomar.
   3. **Parados sem decisão:** `pendente` com outro motivo (permissão, turno encerrado, bloqueio).
      Para ver o que o agente disse por último: `jv show <id> --json` (campo `last_message`).
   4. **Andando:** só a contagem de `ativa`, a não ser que perguntem.
   5. **Entregues:** concluídos recentes, uma linha cada com o `summary`.
3. Se o usuário perguntar por um status específico ("os arquivados", "os pausados"),
   use `jv list --all --json` e filtre.
4. Para detalhe de um agente: `jv show <id>` (plano, últimas falas, decisões, eventos).

Seja curto: uma linha por item. O usuário quer decidir, não ler log.
