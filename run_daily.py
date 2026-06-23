"""
Roda o fluxo de consulta para todos os pedidos com Order ID no portal.
Executado diariamente (launchd, Task Scheduler, cron, ou manualmente).

Fluxo:
  1. GET /api/v1/orders  -> lista de {quote_id, order_id} do portal
  2. Para cada pedido: order_flow.run(order_id) -> baixa XLS do ccwbot
  3. parse_order_lines(xls) -> extrai lead time por linha de produto
  4. POST /api/v1/leadtime -> atualiza produtos + deals no portal
"""
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import order_flow
import portal_client
from xls_parser import parse_order_lines

METRICS_FILE = Path(__file__).parent / "metrics.json"
MAX_RUNS_KEPT = 30

_LOG_FILE = Path(__file__).parent / "daily.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

_W = 60


def _banner(title: str) -> None:
    logger.info("=" * _W)
    pad  = max(0, _W - 2 - len(title))
    left = pad // 2
    logger.info(f"{'=' * left} {title} {'=' * (pad - left)}")
    logger.info("=" * _W)


def _section(title: str) -> None:
    logger.info("")
    logger.info(f"  >> {title}")
    logger.info("  " + "-" * (_W - 2))


def _ok(msg: str) -> None:
    logger.info(f"  [OK] {msg}")


def _warn(msg: str) -> None:
    logger.warning(f"  [!!] {msg}")


def _err(msg: str) -> None:
    logger.error(f"  [XX] {msg}")


def _write_metrics(run_record: dict) -> None:
    existing = {"runs": []}
    if METRICS_FILE.exists():
        try:
            existing = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    runs = existing.get("runs", [])
    runs.append(run_record)
    existing["runs"]         = runs[-MAX_RUNS_KEPT:]
    existing["last_updated"] = run_record["ended_at"]
    METRICS_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    run_start = time.time()
    now_utc   = datetime.now(timezone.utc)
    now_loc   = datetime.now()

    _banner("BOT CCW — RUN DIARIO")
    logger.info(f"  Data/hora : {now_loc.strftime('%Y-%m-%d %H:%M:%S')} (local)")
    logger.info(f"  UTC       : {now_utc.isoformat()}")
    logger.info(f"  Log       : {_LOG_FILE}")

    # ── PASSO 1: Buscar pedidos no portal ──────────────────────────────────────
    _section("PASSO 1/4 | Buscando pedidos com Order ID no portal")
    logger.info(f"  Portal URL: {portal_client.PORTAL_URL}")
    logger.info(f"  Endpoint  : GET /api/v1/orders")

    try:
        orders = portal_client.fetch_orders()
    except Exception as e:
        _err(f"Falha ao contactar o portal: {e}")
        _err("Verifique se o portal esta rodando e se PORTAL_API_KEY esta correto.")
        return

    if not orders:
        _warn("Nenhuma cotacao com Order ID no portal. Cadastre um Order ID em IDs & Estimates.")
        return

    _ok(f"{len(orders)} pedido(s) encontrado(s):")
    for i, o in enumerate(orders, 1):
        last = f" | ultimo sync: {o['last_sync']}" if o.get("last_sync") else " | nunca sincronizado"
        logger.info(f"    {i:2d}. order={o['order_id']:<15} quote={o['quote_id'][:8]}... | \"{o.get('subject','')[:40]}\"{last}")

    # ── Loop por pedido ────────────────────────────────────────────────────────
    results    = {}
    order_recs = []
    for idx, item in enumerate(orders, 1):
        quote_id   = item["quote_id"]
        order_id   = item["order_id"]
        subject    = item.get("subject", "")[:50]
        order_t0   = time.time()

        logger.info("")
        logger.info("=" * _W)
        logger.info(f"  PEDIDO {idx}/{len(orders)}: order={order_id}")
        logger.info(f"  Cotacao : {quote_id[:8]}... | \"{subject}\"")
        logger.info("=" * _W)

        order_rec = {
            "order_id": order_id, "quote_id": quote_id, "subject": subject,
            "status": "erro", "seconds": 0, "products_updated": 0, "max_delivery": None,
        }

        # ── PASSO 2: Consultar ccwbot ──────────────────────────────────────────
        _section(f"PASSO 2/4 | Consultando ccwbot no Webex (order={order_id})")
        try:
            xls_path = order_flow.run(order_number=order_id)
        except Exception as e:
            _err(f"order_flow.run falhou: {e}")
            results[order_id]     = f"ERRO ccwbot: {e}"
            order_rec["seconds"]  = round(time.time() - order_t0)
            order_rec["message"]  = f"ERRO ccwbot: {str(e)[:80]}"
            order_recs.append(order_rec)
            continue

        if not xls_path:
            _warn(f"Nenhum arquivo XLS retornado para order={order_id}.")
            _warn("Verifique o arquivo messages.log para detalhes da sessao Webex.")
            results[order_id]     = "sem arquivo XLS"
            order_rec["seconds"]  = round(time.time() - order_t0)
            order_rec["message"]  = "sem arquivo XLS"
            order_recs.append(order_rec)
            continue

        _ok(f"XLS recebido: {xls_path}")

        # ── PASSO 3: Parsear XLS ───────────────────────────────────────────────
        _section(f"PASSO 3/4 | Analisando XLS (order={order_id})")
        logger.info(f"  Arquivo : {xls_path}")

        try:
            parsed = parse_order_lines(xls_path)
        except Exception as e:
            _err(f"parse_order_lines falhou: {e}")
            results[order_id]     = f"ERRO parse: {e}"
            order_rec["seconds"]  = round(time.time() - order_t0)
            order_rec["message"]  = f"ERRO parse: {str(e)[:80]}"
            order_recs.append(order_rec)
            continue

        lines = parsed["lines"]
        max_d = parsed["max_estimated_delivery"]

        if not lines:
            _warn("Nenhuma linha com Estimated Delivery Date encontrada no XLS.")
            results[order_id]     = "sem linhas no XLS"
            order_rec["seconds"]  = round(time.time() - order_t0)
            order_rec["message"]  = "sem linhas no XLS"
            order_recs.append(order_rec)
            continue

        _ok(f"{len(lines)} linha(s) de produto encontrada(s):")
        logger.info(f"  {'Part Number':<25} {'Est. Delivery':<15} {'Lead Time'}")
        logger.info(f"  {'-'*25} {'-'*15} {'-'*12}")
        for ln in lines:
            pn   = (ln.get("part_number") or "—")[:25]
            dely = ln.get("estimated_delivery", "—")
            lt   = f"{ln.get('lead_time_days', '?')} dias"
            logger.info(f"  {pn:<25} {dely:<15} {lt}")

        logger.info("")
        _ok(f"Max Estimated Delivery: {max_d}")

        # ── PASSO 4: Atualizar portal ──────────────────────────────────────────
        _section(f"PASSO 4/4 | Atualizando portal (quote={quote_id[:8]}...)")
        logger.info(f"  Endpoint: POST /api/v1/leadtime")
        logger.info(f"  Payload : quote_id={quote_id[:8]}... | order_id={order_id} | {len(lines)} linhas")

        try:
            resp  = portal_client.push_leadtime(
                quote_id=quote_id,
                order_id=order_id,
                lines=lines,
                max_estimated_delivery=max_d or "",
            )
            n_upd = resp.get("products_updated", 0)
            _ok(f"{n_upd} produto(s) com lead_time atualizado no portal.")
            _ok(f"last_ccw_sync gravado em deals.json")
            results[order_id]             = f"OK — {n_upd} produto(s) | max_delivery={max_d}"
            order_rec["status"]           = "ok"
            order_rec["products_updated"] = n_upd
            order_rec["max_delivery"]     = max_d
        except Exception as e:
            _err(f"push_leadtime falhou: {e}")
            results[order_id]    = f"ERRO push: {e}"
            order_rec["message"] = f"ERRO push: {str(e)[:80]}"

        order_rec["seconds"] = round(time.time() - order_t0)
        order_recs.append(order_rec)

    # ── Resumo final ───────────────────────────────────────────────────────────
    total_secs = round(time.time() - run_start)
    ok_count   = sum(1 for v in results.values() if v.startswith("OK"))
    err_count  = len(results) - ok_count

    logger.info("")
    _banner("RESUMO FINAL")
    logger.info(f"  Total   : {len(results)} pedido(s)")
    logger.info(f"  Sucesso : {ok_count}")
    logger.info(f"  Falhas  : {err_count}")
    logger.info("")
    for order_id, status in results.items():
        icon = "[OK]" if status.startswith("OK") else "[XX]"
        logger.info(f"  {icon} {order_id:<18} {status}")
    logger.info("=" * _W)
    logger.info(f"  Log completo: {_LOG_FILE}")
    logger.info("=" * _W)

    # ── Grava métricas ─────────────────────────────────────────────────────────
    avg_secs = round(total_secs / len(order_recs), 1) if order_recs else 0
    run_rec  = {
        "started_at":            now_utc.strftime("%Y-%m-%dT%H:%M:%S"),
        "ended_at":              datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "total_seconds":         total_secs,
        "total_orders":          len(order_recs),
        "success":               ok_count,
        "failures":              err_count,
        "avg_seconds_per_order": avg_secs,
        "orders":                order_recs,
    }
    try:
        _write_metrics(run_rec)
        _ok(f"metrics.json atualizado ({METRICS_FILE})")
    except Exception as e:
        _warn(f"Falha ao gravar metrics.json: {e}")


if __name__ == "__main__":
    main()
