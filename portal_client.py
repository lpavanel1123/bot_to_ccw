"""
Client for the Portal de Cotações REST API.
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
    Returns all quotes that have an order_id set in the portal.
    Each item: {quote_id, order_id, subject, last_sync}
    """
    if not PORTAL_API_KEY:
        raise EnvironmentError("PORTAL_API_KEY não definido no .env")
    r = requests.get(f"{PORTAL_URL}/api/v1/orders", headers=_HEADERS, timeout=15)
    r.raise_for_status()
    orders = r.json()
    logger.info(f"Portal retornou {len(orders)} pedido(s) com Order ID.")
    return orders


def push_leadtime(quote_id: str, order_id: str, lines: list,
                   max_estimated_delivery: str) -> dict:
    """
    POST lead-time data back to the portal.
    lines: [{"part_number": str, "estimated_delivery": str, "lead_time_days": int}]
    Returns portal JSON response.
    """
    payload = {
        "quote_id":               quote_id,
        "order_id":               order_id,
        "max_estimated_delivery": max_estimated_delivery,
        "lines":                  lines,
    }
    r = requests.post(
        f"{PORTAL_URL}/api/v1/leadtime",
        headers=_HEADERS,
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    resp = r.json()
    logger.info(
        f"Portal atualizado: {resp.get('products_updated', 0)} produto(s) | "
        f"max_delivery={resp.get('max_estimated_delivery', '')}"
    )
    return resp
