"""
Roda o fluxo de consulta para todos os pedidos com Order ID no portal.
Executado diariamente (launchd, Task Scheduler, cron, ou manualmente).

Fluxo:
  1. GET /api/v1/orders  → lista de {quote_id, order_id} do portal
  2. Para cada pedido:  order_flow.run(order_id) → baixa XLS do ccwbot
  3. parse_order_lines(xls) → extrai lead time por linha
  4. POST /api/v1/leadtime  → atualiza produtos + deals no portal
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import order_flow
import portal_client
from xls_parser import parse_order_lines

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "daily.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def main():
    logger.info(f"=== Run diário {datetime.now(timezone.utc).isoformat()} ===")

    try:
        orders = portal_client.fetch_orders()
    except Exception as e:
        logger.error(f"Falha ao buscar pedidos do portal: {e}")
        return

    if not orders:
        logger.warning("Nenhum pedido com Order ID cadastrado no portal. Nada a fazer.")
        return

    logger.info(f"{len(orders)} pedido(s) para processar.")

    results = {}
    for item in orders:
        quote_id = item["quote_id"]
        order_id = item["order_id"]
        subject  = item.get("subject", "")[:50]
        logger.info(f"--- order={order_id} | quote={quote_id[:8]}... | '{subject}' ---")

        try:
            xls_path = order_flow.run(order_number=order_id)
        except Exception as e:
            logger.error(f"  order_flow falhou: {e}")
            results[order_id] = f"ERRO order_flow: {e}"
            continue

        if not xls_path:
            logger.warning(f"  Sem arquivo XLS para order={order_id}")
            results[order_id] = "sem arquivo"
            continue

        try:
            parsed = parse_order_lines(xls_path)
        except Exception as e:
            logger.error(f"  parse_order_lines falhou: {e}")
            results[order_id] = f"ERRO parse: {e}"
            continue

        if not parsed["lines"]:
            logger.warning(f"  Nenhuma linha com Estimated Delivery Date no XLS.")
            results[order_id] = "sem linhas"
            continue

        logger.info(
            f"  {len(parsed['lines'])} linha(s) | "
            f"max_delivery={parsed['max_estimated_delivery']}"
        )

        try:
            resp = portal_client.push_leadtime(
                quote_id=quote_id,
                order_id=order_id,
                lines=parsed["lines"],
                max_estimated_delivery=parsed["max_estimated_delivery"] or "",
            )
            results[order_id] = f"OK — {resp.get('products_updated', 0)} produto(s)"
        except Exception as e:
            logger.error(f"  push_leadtime falhou: {e}")
            results[order_id] = f"ERRO push: {e}"

    logger.info("=== Resumo ===")
    for order, status in results.items():
        logger.info(f"  {order}: {status}")


if __name__ == "__main__":
    main()
