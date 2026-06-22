# Handoff — Bot de Rotina Webex (Vale-LeadTime)

## Objetivo do projeto

Criar um bot Python que, de forma automatizada:
1. Lê IDs de WebOrders do arquivo `/Users/lpavanel/Dev/ControleDePedidos/claudeteste/extractions.json`
2. Consulta o **ccwbot** no Webex para obter um relatório de status de pedido
3. Baixa o arquivo XLS gerado pelo ccwbot
4. Atualiza o `extractions.json` com os dados extraídos do XLS, localizando o item pelo `id`

---

## Ambiente

| Item | Valor |
|---|---|
| Python | 3.9.6 (macOS, ARM) |
| Pasta do bot | `/Users/lpavanel/Dev/ControleDePedidos/Bot-rotina/` |
| Bot Webex | `Vale-LeadTime@webex.bot` |
| Bot consultado | `ccwbot@webex.bot` (Commerce BOT da Cisco) |
| Sala Webex | Room ID no `.env` |
| Arquivo de dados | `/Users/lpavanel/Dev/ControleDePedidos/claudeteste/extractions.json` |

---

## Arquivos criados

| Arquivo | Função |
|---|---|
| `config.py` | Carrega variáveis do `.env` (tokens, room ID, timeouts) |
| `webex_client.py` | Classe principal: envia mensagens, faz polling, submete card actions, baixa arquivos |
| `history_search.py` | **Estratégia atual**: varre histórico da sala buscando mensagens relacionadas à WebOrder e baixa arquivos associados |
| `order_flow.py` | **Estratégia ativa**: envia "help" → recebe card → submete "Get Order Status Report" → aguarda XLS |
| `webhook_server.py` | Servidor Flask para receber webhooks do Webex (construído mas **inutilizável** — ver seção de erros) |
| `tunnel.py` | Túnel SSH via serveo.net (construído mas **bloqueado** — ver seção de erros) |
| `test_ccwbot.py` | Script de teste: envia mensagem ao ccwbot e imprime a resposta via polling |
| `message_parser.py` | Parser flexível de respostas (JSON, chave-valor, texto livre) — criado para uso futuro |
| `processed.json` | Rastreador de mensagens já processadas `{msg_id: {order, date, files}}` |
| `requirements.txt` | Dependências Python |
| `.env` | Credenciais (NÃO versionar) |
| `.env.example` | Modelo de configuração |

---

## Variáveis de ambiente (`.env`)

```env
WEBEX_BOT_TOKEN=...       # Token do bot Vale-LeadTime (permanente)
WEBEX_USER_TOKEN=...      # Token pessoal do usuário (expira em 12h — precisa renovar)
WEBEX_ROOM_ID=...         # ID da sala onde o bot opera
POLLING_INTERVAL=5        # Segundos entre cada poll
RESPONSE_TIMEOUT=120      # Timeout geral em segundos
```

---

## Raciocínio e decisões de arquitetura

### Decisão 1: SDK — webexteamssdk, não webexpythonsdk
O pacote `webexpythonsdk` não existe no PyPI. O SDK oficial disponível é `webexteamssdk==1.7`. Imports trocados em todos os arquivos.

### Decisão 2: Dois tokens — bot para enviar, pessoal para ler
**Bots em group rooms no Webex têm acesso restrito ao histórico de mensagens.**

- `GET /messages?roomId=...` com bot token → **403 Forbidden** (bots só leem mensagens onde foram @mencionados)
- `GET /messages?roomId=...` com token pessoal → **200 OK**, retorna todo o histórico

Solução adotada:
- **Bot token**: envia mensagens, registra webhooks
- **Token pessoal**: lê mensagens, submete card actions

### Decisão 3: Polling, não webhook
A abordagem ideal seria webhook (Webex chama nosso servidor a cada nova mensagem). Tentamos com:
- **pyngrok**: falhou (ngrok v3 requer authtoken, ambiente não permite)
- **serveo.net** (SSH tunnel): o Cisco SSE (Secure Service Edge) da rede corporativa bloqueia `*.serveousercontent.com` com 403 → `malware.block.sse.cisco.com`

Resultado: qualquer tunnel externo é bloqueado pela rede. Adotado polling com `_list_messages()`.

### Decisão 4: Histórico passivo vs. comando ativo
Tentamos duas estratégias:

**Estratégia A (order_flow.py)**: enviar comando ao ccwbot e aguardar resposta
- Funciona parcialmente (card recebido, ação submetida)
- Problema: o arquivo pós-submit não chegou em 30s (pode ser timeout curto ou ccwbot mandou outro card intermediário)

**Estratégia B (history_search.py)**: varrer histórico da sala buscando o que o ccwbot já postou
- Não precisa aguardar resposta em tempo real
- Mais resiliente a timeouts
- **Não testada** — o token pessoal expirou antes do teste

### Decisão 5: Token pessoal para card actions
Tentamos submeter o botão "Get Order Status Report" com o bot token:
- `POST /v1/attachment/actions` com bot token → **404 Not Found** (bots não têm permissão para submeter ações em cards de outros bots)
- Com token pessoal → **201 Created** (sucesso)

### Decisão 6: inputs da card action devem ser somente strings
A API `/v1/attachment/actions` retorna **400 Bad Request** se `inputs` contiver valores não-string (ex: `False` boolean Python). Todos os valores foram convertidos para string ou removidos.

---

## Fluxo que funciona (order_flow.py)

```
1. send_mention("ccwbot@webex.bot", "help")           → bot token
2. wait_for_card(after_message_id=sent_id)             → polling com bot token (só vê mensagens onde foi mencionado)
   *** PROBLEMA: bot não consegue ler resposta do ccwbot aqui ***
   *** Solução atual: usar personal token no _list_messages ***
3. click_card_button(msg_id, inputs={                  → personal token
       "salesOrder": order_number,
       "command": "exportOrderBacklogReport"
   })
4. wait_for_file(after_message_id=card_msg_id)         → polling com bot token
   *** PROBLEMA: mesmo problema — precisa personal token ***
```

**Status atual**: etapa 3 funciona (action id retornado). Etapa 4 nunca completou (token expirou antes de confirmar se arquivo chegou).

---

## Fluxo de busca histórica (history_search.py)

```
1. Ler até 200 mensagens da sala (paginação a cada 50)
2. Filtrar mensagens que contêm o número da WebOrder (ex: "11001504302")
3. Procurar mensagens do ccwbot COM ARQUIVO (files[]) nas 5 mensagens seguintes
4. Se arquivo não visto antes (not in processed.json):
   - Baixar o XLS
   - Salvar msg_id + date + files no processed.json
5. Retornar lista de arquivos baixados
```

**Status**: implementado mas **não testado end-to-end** (token expirou).

---

## Erros encontrados e resoluções

| Erro | Causa | Resolução |
|---|---|---|
| `ModuleNotFoundError: webexpythonsdk` | Pacote inexistente | Trocado por `webexteamssdk` |
| `403 GET /messages` com bot token | Bot em group room não lê todas as mensagens | Usar token pessoal para leitura |
| `PyngrokNgrokError` | ngrok v3 requer authtoken | Tentado serveo.net via SSH |
| `403` na URL do serveo.net | Cisco SSE bloqueia `*.serveousercontent.com` | Abandonado webhook, adotado polling |
| `TypeError: dict \| None` | Python 3.9 não suporta union types com `\|` | Trocado por `Optional[dict]` do typing |
| `400 inputs must a simple key value object` | Boolean `False` nos inputs da card action | Removidos booleans, somente strings |
| `404 POST /attachment/actions` com bot token | Bot não pode submeter ações em cards de outros bots | Trocado para token pessoal no submit |
| `401` no token pessoal | Token pessoal do Webex expira em 12h | Precisa renovar manualmente ou implementar OAuth |
| `403 GET /messages` no `history_search.py` com bot token | Mesma limitação de group room | Precisa do token pessoal |

---

## O que falta fazer

### Imediato
1. **Renovar o token pessoal**: `developer.webex.com` → avatar → "Copy personal access token" → atualizar `WEBEX_USER_TOKEN` no `.env`
2. **Testar `history_search.py`** com WebOrder `11001504302` e confirmar que encontra o arquivo
3. **Confirmar o que ccwbot respondeu** após o submit da card action (rode: `python3 test_ccwbot.py` com token válido e leia as últimas 10 mensagens)

### Médio prazo
4. **Aumentar timeout do wait_for_file** em `order_flow.py` (30s pode ser insuficiente — ccwbot provavelmente manda um card intermediário "processando" antes do XLS)
5. **Tratar o card intermediário**: depois do submit, ccwbot talvez mande um novo card de confirmação antes do arquivo. O `wait_for_file` precisa ignorar cards e aguardar o `files[]`
6. **Integrar com `extractions.json`**: após baixar o XLS, parsear e atualizar os campos `"NA"` do item correspondente pelo `id`

### Arquitetura (para produção)
7. **OAuth com refresh_token**: elimina a necessidade de renovar o token pessoal a cada 12h. Requer cadastrar uma Webex Integration em `developer.webex.com`
8. **Agendador**: usar `schedule` ou `APScheduler` para rodar a rotina periodicamente

---

## Comandos úteis

```bash
# Verificar token pessoal
python3 -c "
import requests; from config import WEBEX_USER_TOKEN
r = requests.get('https://webexapis.com/v1/people/me', headers={'Authorization': f'Bearer {WEBEX_USER_TOKEN}'})
print(r.status_code, r.json().get('displayName'))
"

# Testar envio de mensagem (bot token)
python3 hello.py

# Testar resposta do ccwbot
python3 test_ccwbot.py

# Busca histórica (requer personal token válido)
python3 history_search.py

# Fluxo ativo completo
python3 order_flow.py
```

---

## Estrutura do extractions.json (arquivo alvo)

```json
[
  {
    "id": "f25c71fc-2ebd-4d17-82da-3752c0681c06",
    "date": "2026-06-08 22:01:23",
    "from": "desconhecido",
    "subject": "(sem assunto)",
    "request_type": "NA",
    "project_type": "NA",
    "requester_name": "NA",
    "department": "NA",
    "recipient": "NA",
    "cnpj": "NA",
    "smart_account": "NA",
    "smart_account_domain": "NA",
    "virtual_account": "NA",
    "products": [],
    "project_ref": "NA"
  }
]
```

Campos com `"NA"` são os que o bot deve preencher após processar a resposta do ccwbot. A integração do XLS com o JSON ainda não foi implementada.
