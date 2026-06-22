"""
Diagnóstico: envia qualquer comando de texto ao ccwbot via DM e exibe a resposta.

Uso:
    python test_direct_ccwbot.py                         # envia 'help'
    python test_direct_ccwbot.py "orderstatus 12345"     # envia comando customizado
"""
import json
import sys
import logging
from webex_client import send_direct_message, poll_room

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

CCWBOT_EMAIL = "ccwbot@webex.bot"


def run(command: str = "help") -> None:
    print(f"\n>>> Enviando: '{command}'")
    dm = send_direct_message(CCWBOT_EMAIL, command)
    print(f">>> Sala 1:1: {dm['room_id']}")
    print(">>> Aguardando resposta (45s)...\n")

    messages = poll_room(dm["room_id"], after_id=dm["msg_id"], timeout=45)

    if not messages:
        print("Sem resposta dentro do timeout.")
        return

    for msg in messages:
        print(f"{'='*60}")
        print(f"De    : {msg.get('personEmail')}")
        print(f"Texto : {msg.get('text', '(sem texto)')}")
        if msg.get("attachments"):
            card_types = [a.get("contentType") for a in msg["attachments"]]
            print(f"Card  : {card_types}")
            body = msg["attachments"][0].get("content", {}).get("body", [])
            if body:
                print(f"Card body (resumo): {json.dumps(body[0], ensure_ascii=False)}")
        if msg.get("files"):
            print(f"Files : {msg['files']}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "help"
    run(cmd)
