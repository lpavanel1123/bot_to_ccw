import time
import logging
import requests
from typing import Optional
from config import WEBEX_BOT_TOKEN, WEBEX_USER_TOKEN, POLLING_INTERVAL, RESPONSE_TIMEOUT

WEBEX_API_BASE = "https://webexapis.com/v1"
logger = logging.getLogger(__name__)


def send_direct_message(person_email: str, text: str, token: str = WEBEX_BOT_TOKEN) -> dict:
    """Envia DM direta. Usa token pessoal para ccwbot (recebe card), bot token para outros."""
    resp = requests.post(
        f"{WEBEX_API_BASE}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"toPersonEmail": person_email, "text": text},
    )
    resp.raise_for_status()
    data = resp.json()
    return {"msg_id": data["id"], "room_id": data["roomId"]}


def poll_room(room_id: str, after_id: str, timeout: int = RESPONSE_TIMEOUT, token: str = WEBEX_BOT_TOKEN) -> list:
    """
    Aguarda mensagens novas a partir de after_id.
    Para assim que encontra arquivo ou timeout esgotar.
    Retorna lista em ordem cronológica.
    """
    deadline = time.time() + timeout
    seen = set()
    collected = []

    while time.time() < deadline:
        time.sleep(POLLING_INTERVAL)
        resp = requests.get(
            f"{WEBEX_API_BASE}/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"roomId": room_id, "max": 10},
        )
        resp.raise_for_status()

        batch = []
        for msg in resp.json().get("items", []):
            if msg["id"] == after_id:
                break
            if msg["id"] not in seen:
                seen.add(msg["id"])
                batch.append(msg)

        for msg in reversed(batch):
            collected.append(msg)
            if msg.get("files"):
                return collected

    return collected


def submit_card_action(card_message_id: str, inputs: Optional[dict] = None, token: str = WEBEX_USER_TOKEN) -> dict:
    """Clica num botão de Adaptive Card usando token pessoal (único com permissão)."""
    payload = {
        "type": "submit",
        "messageId": card_message_id,
        "inputs": inputs or {},
    }
    resp = requests.post(
        f"{WEBEX_API_BASE}/attachment/actions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()


def download_file(file_url: str, output_path: str, token: str = WEBEX_USER_TOKEN) -> str:
    """Faz download de arquivo do Webex."""
    resp = requests.get(
        file_url,
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
    )
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return output_path
