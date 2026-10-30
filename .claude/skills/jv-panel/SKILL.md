---
name: jv-panel
description: Abre o painel do Jarvinho com um card por agente, status, metadados e o que cada agente está fazendo ao vivo. Use quando o usuário pedir o painel, a visão geral visual, ou quiser acompanhar os agentes.
---

# Painel

- **Ao vivo (local):** `jv panel serve --port 8765` em segundo plano e passe o endereço
  `http://127.0.0.1:8765`. Ele atualiza a cada 3 s; clicar num card abre o detalhe com as
  últimas falas do agente, o plano, as decisões, os metadados e o comando `claude attach` para
  abrir a sessão. Se já houver um painel rodando nessa porta, só passe o endereço.
  Para abrir no celular na mesma rede, use `--host 0.0.0.0` e o IP da máquina (só em rede confiável:
  o painel não tem login).
- **Foto estática:** `jv panel build /caminho/painel.html` gera um HTML único com os dados embutidos,
  bom para mandar ou guardar. Não atualiza sozinho.

O painel é só leitura. Ações (pausar, responder) passam por você, via `jv-control`.
