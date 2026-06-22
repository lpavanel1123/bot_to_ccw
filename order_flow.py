"""
Fluxo: DM pessoal ao ccwbot -> card com status -> clica download -> salva XLS.

Token pessoal envia o comando (ccwbot responde com card apenas para humanos).
Token pessoal le o card e submete a acao de download.
"""
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from config import WEBEX_USER_TOKEN, RESPONSE_TIMEOUT
from webex_client import download_file, poll_room, send_direct_message, submit_card_action
from xls_parser import latest_estimated_delivery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CCWBOT_EMAIL  = "ccwbot@webex.bot"
LOG_FILE      = Path(__file__).parent / "messages.log"
DOWNLOADS_DIR = Path(__file__).parent / "downloads"
INITIAL_WAIT  = 30

DOWNLOADS_DIR.mkdir(exist_ok=True)

LINE = "-" * 60


def _hr(title: str = "") -> None:
    if title:
        pad = max(0, 58 - len(title))
        left = pad // 2
        logger.info(f"{'=' * left} {title} {'=' * (pad - left)}")
    else:
        logger.info("=" * 60)


def _step(n: int, total: int, msg: str) -> None:
    logger.info(f"")
    logger.info(f"[{n}/{total}] {msg}")
    logger.info(LINE)


def _log(entry: dict) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _log_message(msg: dict, indent: str = "      ") -> None:
    entry = {
        "ts":           datetime.now(timezone.utc).isoformat(),
        "from":         msg.get("personEmail"),
        "text":         msg.get("text", ""),
        "has_card":     bool(msg.get("attachments")),
        "card_content": msg["attachments"][0].get("content") if msg.get("attachments") else None,
        "files":        msg.get("files", []),
        "msg_id":       msg["id"],
    }
    _log(entry)

    sender  = entry["from"] or "desconhecido"
    preview = (entry["text"] or "").replace("\n", " ")[:120]

    logger.info(f"{indent}De     : {sender}")
    if preview:
        logger.info(f"{indent}Texto  : \"{preview}\"")
    if entry["has_card"]:
        logger.info(f"{indent}Tipo   : CARD Adaptive Card detectado")
    if entry["files"]:
        logger.info(f"{indent}Tipo   : ARQUIVO anexado ({len(entry['files'])} URL(s))")


def _find_card(messages: list) -> Optional[dict]:
    """Retorna msg_id e inputs do botao 'Download Complete Line Status', se encontrado."""
    for msg in messages:
        if msg.get("attachments"):
            for att in msg["attachments"]:
                if att.get("contentType") == "application/vnd.microsoft.card.adaptive":
                    actions = att.get("content", {}).get("actions", [])
                    for action in actions:
                        if action.get("title") == "Download Complete Line Status":
                            inputs = {k: str(v) for k, v in action.get("data", {}).items()}
                            return {"msg_id": msg["id"], "inputs": inputs}
    return None


def _check_token() -> str:
    """Retorna um WEBEX_USER_TOKEN valido, renovando automaticamente se necessario."""
    logger.info(f"      Verificando token pessoal Webex...")
    r = requests.get(
        "https://webexapis.com/v1/people/me",
        headers={"Authorization": f"Bearer {WEBEX_USER_TOKEN}"},
    )
    if r.status_code == 200:
        data = r.json()
        name  = data.get("displayName", "?")
        email = data.get("emails", ["?"])[0]
        logger.info(f"      OK | Autenticado como: {name} ({email})")
        return WEBEX_USER_TOKEN

    logger.warning(f"      Token expirado (HTTP {r.status_code}) — renovando automaticamente...")
    import subprocess
    result = subprocess.run(
        ["python3", str(Path(__file__).parent / "renew_token.py")],
        text=True,
    )
    if result.returncode != 0:
        raise EnvironmentError("Falha ao renovar token. Execute manualmente: python renew_token.py")

    from dotenv import dotenv_values
    fresh = dotenv_values(Path(__file__).parent / ".env").get("WEBEX_USER_TOKEN", "")
    if not fresh:
        raise EnvironmentError("WEBEX_USER_TOKEN nao encontrado no .env apos renovacao.")
    logger.info("      Token renovado com sucesso.")
    return fresh


def run(order_number: str, output_path: Optional[str] = None) -> Optional[str]:
    t_start     = time.time()
    output_path = output_path or str(DOWNLOADS_DIR / f"order_{order_number}.xls")
    command     = f"get order status report {order_number}"

    _hr(f"ORDER {order_number}")
    logger.info(f"      Hora inicio  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"      Arquivo saida: {output_path}")

    # ── ETAPA 1: Token ─────────────────────────────────────────────────────────
    _step(1, 4, "Verificando token Webex pessoal")
    try:
        token = _check_token()
    except Exception as e:
        logger.error(f"      ERRO: {e}")
        _hr()
        return None

    # ── ETAPA 2: Envio do comando ───────────────────────────────────────────────
    _step(2, 4, "Enviando comando ao ccwbot")
    logger.info(f"      Destinatario : {CCWBOT_EMAIL}")
    logger.info(f"      Comando      : \"{command}\"")

    try:
        dm = send_direct_message(CCWBOT_EMAIL, command, token=token)
    except Exception as e:
        logger.error(f"      ERRO ao enviar mensagem: {e}")
        _hr()
        return None

    logger.info(f"      Sala 1:1     : {dm['room_id'][:36]}...")
    logger.info(f"      msg_id env.  : {dm['msg_id'][:24]}...")
    logger.info(f"      OK | Mensagem enviada com sucesso.")
    _log({
        "ts":      datetime.now(timezone.utc).isoformat(),
        "event":   "run_start",
        "order":   order_number,
        "command": command,
        "room_id": dm["room_id"],
    })

    # ── ETAPA 3: Aguardando resposta ────────────────────────────────────────────
    _step(3, 4, f"Aguardando resposta do ccwbot")
    logger.info(f"      Espera inicial: {INITIAL_WAIT}s (ccwbot consulta sistema Cisco)")
    logger.info(f"      Polling:        a cada 5s | timeout {RESPONSE_TIMEOUT}s")

    for elapsed in range(5, INITIAL_WAIT + 1, 5):
        time.sleep(5)
        remaining = INITIAL_WAIT - elapsed
        bar = "#" * (elapsed // 5) + "." * ((INITIAL_WAIT - elapsed) // 5)
        if remaining > 0:
            logger.info(f"      [{bar}] {elapsed:3d}s / {INITIAL_WAIT}s — aguardando...")
        else:
            logger.info(f"      [{bar}] {INITIAL_WAIT}s — pronto. Iniciando polling.")

    after_id    = dm["msg_id"]
    all_messages = []
    poll_cycle  = 0

    while True:
        poll_cycle += 1
        logger.info(f"")
        logger.info(f"      Ciclo {poll_cycle} | after_id={after_id[:20]}...")
        batch = poll_room(dm["room_id"], after_id=after_id, token=token)

        if not batch:
            elapsed = int(time.time() - t_start)
            if not all_messages:
                logger.warning(f"      Ciclo {poll_cycle} | Nenhuma resposta recebida ({elapsed}s total).")
            else:
                logger.warning(f"      Ciclo {poll_cycle} | Timeout sem novas mensagens ({elapsed}s total).")
            break

        logger.info(f"      Ciclo {poll_cycle} | {len(batch)} mensagem(ns) recebida(s):")
        for i, msg in enumerate(batch, 1):
            logger.info(f"")
            logger.info(f"      --- Mensagem {i}/{len(batch)} ---")
            _log_message(msg, indent="      ")
            all_messages.append(msg)

            if msg.get("files"):
                # ── ETAPA 4: Download ─────────────────────────────────────────
                _step(4, 4, "Arquivo XLS recebido — baixando")
                file_url = msg["files"][0]
                logger.info(f"      URL      : {file_url[:60]}...")
                logger.info(f"      Destino  : {output_path}")

                try:
                    saved = download_file(file_url, output_path, token=token)
                except Exception as e:
                    logger.error(f"      ERRO no download: {e}")
                    _hr()
                    return None

                size_kb = Path(saved).stat().st_size / 1024
                logger.info(f"      Tamanho  : {size_kb:.1f} KB")
                logger.info(f"      OK | Arquivo salvo em: {saved}")

                logger.info(f"")
                logger.info(f"      Extraindo Estimated Delivery Date...")
                date = latest_estimated_delivery(saved)
                if date:
                    logger.info(f"      Max. Delivery : {date.strftime('%d-%b-%Y')} ({date.strftime('%Y-%m-%d')})")
                else:
                    logger.warning(f"      Nenhuma 'Estimated Delivery Date' encontrada no XLS.")

                elapsed = int(time.time() - t_start)
                logger.info(f"")
                logger.info(f"      Tempo total: {elapsed}s")
                _hr()
                return saved

        # Card de status?
        card = _find_card(batch)
        if card:
            logger.info(f"")
            logger.info(f"      >>> CARD detectado — clicando 'Download Complete Line Status'")
            logger.info(f"      msg_id do card : {card['msg_id'][:36]}...")
            logger.info(f"      Inputs do botao:")
            for k, v in card["inputs"].items():
                logger.info(f"        {k}: {v}")

            try:
                logger.info(f"      Submetendo acao (token pessoal)...")
                action = submit_card_action(
                    card_message_id=card["msg_id"],
                    inputs=card["inputs"],
                    token=token,
                )
                logger.info(f"      OK | action_id: {action.get('id', '?')[:36]}...")
            except Exception as e:
                logger.error(f"      ERRO ao submeter card action: {e}")
                _hr()
                return None

            logger.info(f"      Aguardando XLS que ccwbot vai enviar apos o clique...")
            after_id   = batch[-1]["id"]
            poll_cycle = 0
            continue

        after_id = batch[-1]["id"]
        logger.info(f"      Sem card e sem arquivo — aguardando proximo ciclo...")

    elapsed = int(time.time() - t_start)
    logger.warning(f"Fluxo encerrado sem arquivo. Tempo total: {elapsed}s")
    logger.warning(f"Verifique o log completo em: {LOG_FILE}")
    _hr()
    return None


if __name__ == "__main__":
    order  = sys.argv[1] if len(sys.argv) > 1 else "119659099"
    result = run(order_number=order)
    print(f"\nResultado: {result}" if result else f"\nSem arquivo — ver {LOG_FILE}")
