"""
Client for the Portal de Cotacoes REST API.
Used by run_daily.py to fetch orders and push CCW lead-time results back.
"""
import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

PORTAL_URL     = os.getenv("PORTAL_URL", "http://localhost:8080")
PORTAL_API_KEY = os.getenv("PORTAL_API_KEY", "")

logger = logging.getLogger(__name__)

_HEADERS = {
    "X-API-Key":    PORTAL_API_KEY,
    "Content-Type": "application/json",
}


def fetch_orders() -> list:
    """
    Retorna todas as cotacoes com order_id cadastrado no portal.
    Cada item: {quote_id, order_id, subject, last_sync}
    """
    if not PORTAL_API_KEY:
        raise EnvironmentError("PORTAL_API_KEY nao definido no .env")

    url = f"{PORTAL_URL}/api/v1/orders"
    logger.info(f"  GET {url}")

    r = requests.get(url, headers=_HEADERS, timeout=15)
    logger.info(f"  HTTP {r.status_code} | {len(r.content)} bytes")
    r.raise_for_status()

    orders = r.json()
    logger.info(f"  {len(orders)} pedido(s) com Order ID retornado(s).")
    return orders


def push_leadtime(quote_id: str, order_id: str, lines: list,
                   max_estimated_delivery: str) -> dict:
    """
    POST lead-time data de volta ao portal.
    lines: [{"part_number": str, "estimated_delivery": str, "lead_time_days": int}]
    Retorna resposta JSON do portal.
    """
    url     = f"{PORTAL_URL}/api/v1/leadtime"
    payload = {
        "quote_id":               quote_id,
        "order_id":               order_id,
        "max_estimated_delivery": max_estimated_delivery,
        "lines":                  lines,
    }

    logger.info(f"  POST {url}")
    logger.info(f"  Payload: quote_id={quote_id[:8]}... | order_id={order_id} | {len(lines)} linha(s) | max={max_estimated_delivery}")

    r = requests.post(url, headers=_HEADERS, json=payload, timeout=15)
    logger.info(f"  HTTP {r.status_code} | {len(r.content)} bytes")
    r.raise_for_status()

    resp = r.json()
    n_upd = resp.get("products_updated", 0)
    logger.info(f"  Portal confirmou: {n_upd} produto(s) com lead_time atualizado.")
    return resp
