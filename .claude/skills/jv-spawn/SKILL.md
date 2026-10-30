---
name: jv-spawn
description: Cria e dispara um agente do Jarvinho para uma missão num projeto. Use quando o usuário pedir para "colocar um agente", "mandar alguém fazer", "abrir uma frente" ou delegar qualquer tarefa que rode sozinha num repo.
---

# Criar um agente

1. **Feche a missão antes de disparar.** Você precisa de quatro coisas; pergunte só o que faltar:
   - **id**: slug curto e legível (`api-auth`, `relatorio-out`), minúsculas, números e hífen.
   - **projeto**: pasta do repo onde o agente trabalha. Se o usuário der só o nome, procure em
     `~/projetos`, `~/code` e no `jv list --json` (projetos já usados) antes de perguntar.
   - **missão**: uma ou duas frases com o resultado esperado, não os passos.
   - **critério de pronto**: como saber que acabou (ex.: "testes passam e PR aberto").
     Se o usuário não der, proponha um e confirme.
2. **Escolha o modo de permissão** (padrão `acceptEdits`: edita sozinho, pede aprovação para
   comandos). Use `plan` para missões só de análise. Nunca use `bypassPermissions` sem o usuário pedir.
3. **Dependência:** se a missão só faz sentido depois de outra, use `--after <id>`.
4. Dispare:

   ```bash
   jv spawn <id> --project <pasta> --objective "<missão>" --done "<critério>" [--mode <modo>] [--after <id>]
   ```

5. Responda em uma linha: id, projeto e que o agente começou (ou está agendado).
   Não descreva o protocolo; o agente já recebe tudo no prompt de sistema.

Se o `jv spawn` falhar porque a pasta não é confiável ou não existe, explique o erro e peça o caminho certo.
