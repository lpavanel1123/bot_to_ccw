"""
Fluxo: DM pessoal ao ccwbot → card com status → clica download → salva XLS.

Token pessoal envia o comando (ccwbot responde com card apenas para humanos).
Token pessoal lê o card e submete a ação de download.
"""
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from pathlib import Path
from config import WEBEX_USER_TOKEN, RESPONSE_TIMEOUT
from webex_client import download_file, poll_room, send_direct_message, submit_card_action
from xls_parser import latest_estimated_delivery


def _check_token() -> str:
    """Retorna um WEBEX_USER_TOKEN válido, renovando automaticamente se necessário."""
    r = requests.get(
        "https://webexapis.com/v1/people/me",
        headers={"Authorization": f"Bearer {WEBEX_USER_TOKEN}"},
    )
    if r.status_code == 200:
        return WEBEX_USER_TOKEN

    logger.info("Token expirado — renovando automaticamente...")
    import subprocess
    result = subprocess.run(
        ["python3", str(Path(__file__).parent / "renew_token.py")],
        text=True,
    )
    if result.returncode != 0:
        raise EnvironmentError("Falha ao renovar token. Execute manualmente: python renew_token.py")

    # Relê o token atualizado do .env (o processo atual tem o valor antigo em memória)
    from dotenv import dotenv_values
    fresh = dotenv_values(Path(__file__).parent / ".env").get("WEBEX_USER_TOKEN", "")
    if not fresh:
        raise EnvironmentError("WEBEX_USER_TOKEN não encontrado no .env após renovação.")
    logger.info("Token renovado com sucesso.")
    return fresh

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CCWBOT_EMAIL = "ccwbot@webex.bot"
LOG_FILE = Path(__file__).parent / "messages.log"
DOWNLOADS_DIR = Path(__file__).parent / "downloads"
INITIAL_WAIT = 30

DOWNLOADS_DIR.mkdir(exist_ok=True)


def _log(entry: dict) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _log_message(msg: dict) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "from": msg.get("personEmail"),
        "text": msg.get("text", ""),
        "has_card": bool(msg.get("attachments")),
        "card_content": msg["attachments"][0].get("content") if msg.get("attachments") else None,
        "files": msg.get("files", []),
        "msg_id": msg["id"],
    }
    _log(entry)

    preview = entry["text"][:200] if entry["text"] else "(sem texto)"
    logger.info(f"  de={entry['from']} | text={preview}")
    if entry["has_card"]:
        logger.info("  [CARD recebido — conteúdo salvo no log]")
    if entry["files"]:
        logger.info(f"  [ARQUIVO: {entry['files']}]")


def _find_card(messages: list) -> Optional[dict]:
    """Retorna msg_id e os inputs do botão 'Download Complete Line Status', se encontrado."""
    for msg in messages:
        if msg.get("attachments"):
            for att in msg["attachments"]:
                if att.get("contentType") == "application/vnd.microsoft.card.adaptive":
                    actions = att.get("content", {}).get("actions", [])
                    for action in actions:
                        if action.get("title") == "Download Complete Line Status":
                            # Converte tudo para string (exigência da API Webex)
                            inputs = {k: str(v) for k, v in action.get("data", {}).items()}
                            return {"msg_id": msg["id"], "inputs": inputs}
    return None


def run(order_number: str, output_path: Optional[str] = None) -> Optional[str]:
    output_path = output_path or str(DOWNLOADS_DIR / f"order_{order_number}.xls")
    command = f"get order status report {order_number}"

    token = _check_token()
    _log({"ts": datetime.now(timezone.utc).isoformat(), "event": "run_start", "order": order_number, "command": command})
    logger.info(f"=== Consulta order={order_number} ===")
    logger.info(f"Enviando como usuário pessoal: '{command}'")

    # Token pessoal envia → ccwbot responde com card (não responde com card para bots)
    dm = send_direct_message(CCWBOT_EMAIL, command, token=token)
    logger.info(f"Sala 1:1 pessoal com ccwbot: {dm['room_id']}")

    logger.info(f"Aguardando {INITIAL_WAIT}s para o ccwbot processar...")
    time.sleep(INITIAL_WAIT)

    after_id = dm["msg_id"]
    all_messages = []

    # Loop de polling — continua se receber mensagens sem arquivo (ex: "One moment..." + card)
    while True:
        logger.info(f"Polling (timeout={RESPONSE_TIMEOUT}s)...")
        batch = poll_room(dm["room_id"], after_id=after_id, token=token)

        if not batch:
            if not all_messages:
                logger.warning("Nenhuma resposta recebida.")
            else:
                logger.warning("Sem resposta adicional após última mensagem.")
            break

        logger.info(f"{len(batch)} mensagem(ns) neste ciclo:")
        for msg in batch:
            _log_message(msg)
            all_messages.append(msg)
            if msg.get("files"):
                saved = download_file(msg["files"][0], output_path, token=token)
                logger.info(f"=== XLS salvo: {saved} ===")
                date = latest_estimated_delivery(saved)
                if date:
                    logger.info(f"=== Estimated Delivery Date mais distante: {date.strftime('%d-%b-%Y')} ===")
                else:
                    logger.warning("Nenhuma 'Estimated Delivery Date' encontrada no XLS.")
                return saved

        # Verifica se recebeu o card de status
        card = _find_card(batch)
        if card:
            logger.info(f"Card de status recebido (msg_id={card['msg_id'][:20]}...).")
            logger.info(f"Inputs do botão: {card['inputs']}")
            logger.info("Submetendo 'Download Complete Line Status'...")
            action = submit_card_action(
                card_message_id=card["msg_id"],
                inputs=card["inputs"],
                token=token,
            )
            logger.info(f"Card action submetida (action_id={action.get('id', '?')[:20]}...)")

            # Aguarda o arquivo que ccwbot vai mandar após o clique
            after_id = batch[-1]["id"]
            logger.info("Aguardando arquivo após submit do card...")
            continue

        # Sem card e sem arquivo — avança after_id e espera próxima mensagem
        after_id = batch[-1]["id"]
        logger.info("Aguardando próxima mensagem do ccwbot...")

    return None


if __name__ == "__main__":
    order = sys.argv[1] if len(sys.argv) > 1 else "119659099"
    result = run(order_number=order)
    print(f"\nArquivo: {result}" if result else f"\nSem arquivo — ver {LOG_FILE}")
