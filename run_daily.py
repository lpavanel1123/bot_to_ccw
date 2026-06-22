"""
Roda o fluxo de consulta para todos os pedidos em orders.txt.
Executado diariamente pelo launchd às 06:00.
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import order_flow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "daily.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

ORDERS_FILE = Path(__file__).parent / "orders.txt"


def load_orders() -> list[str]:
    lines = ORDERS_FILE.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def main():
    orders = load_orders()
    if not orders:
        logger.warning(f"Nenhum pedido em {ORDERS_FILE}. Nada a fazer.")
        return

    logger.info(f"=== Run diário {datetime.now(timezone.utc).isoformat()} — {len(orders)} pedido(s) ===")

    results = {}
    for order in orders:
        logger.info(f"--- Processando pedido {order} ---")
        try:
            saved = order_flow.run(order_number=order)
            results[order] = saved or "sem arquivo"
        except Exception as e:
            logger.error(f"Erro no pedido {order}: {e}")
            results[order] = f"ERRO: {e}"

    logger.info("=== Resumo ===")
    for order, result in results.items():
        logger.info(f"  {order}: {result}")


if __name__ == "__main__":
    main()
