"""
Roda o fluxo de consulta para todos os pedidos com Order ID no portal.
Executado diariamente (launchd, Task Scheduler, cron, ou manualmente).

Fluxo:
  1. GET /api/v1/orders  -> lista de {quote_id, order_id} do portal
  2. Para cada pedido: order_flow.run(order_id) -> baixa XLS do ccwbot
  3. parse_order_lines(xls) -> extrai lead time por linha de produto
  4. POST /api/v1/leadtime -> atualiza produtos + deals no portal
  5. Retry das falhas (1x, após 60s)
  6. Atualiza contadores de falhas; suspende após 3 falhas consecutivas
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

METRICS_FILE             = Path(__file__).parent / "metrics.json"
ORDER_ERRORS_FILE        = Path(__file__).parent / "order_errors.json"
TOKEN_INFO_FILE          = Path(__file__).parent / "token_info.json"
MAX_RUNS_KEPT            = 30
MAX_CONSECUTIVE_FAILURES = 3
RETRY_WAIT_SECS          = 60

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


def _load_order_errors() -> dict:
    if ORDER_ERRORS_FILE.exists():
        try:
            return json.loads(ORDER_ERRORS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_order_errors(errors: dict) -> None:
    ORDER_ERRORS_FILE.write_text(
        json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _push_to_portal(metrics: dict, order_errors: dict) -> None:
    import os, requests as _req
    portal_url = os.getenv("PORTAL_URL", "")
    portal_key = os.getenv("PORTAL_API_KEY", "")
    if not portal_url or not portal_key:
        logger.info("PORTAL_URL/PORTAL_API_KEY não configurados — push ignorado")
        return
    token_info = {}
    if TOKEN_INFO_FILE.exists():
        try:
            token_info = json.loads(TOKEN_INFO_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        r = _req.post(
            f"{portal_url.rstrip('/')}/api/v1/bot-status",
            json={"runs": metrics.get("runs", []), "order_errors": order_errors, "token_info": token_info},
            headers={"Authorization": f"Bearer {portal_key}"},
            timeout=10,
        )
        if r.ok:
            _ok(f"Métricas enviadas ao portal (HTTP {r.status_code})")
        else:
            _warn(f"Falha no push ao portal: HTTP {r.status_code}")
    except Exception as e:
        _warn(f"Erro ao conectar ao portal para push: {e}")


def _process_one_order(item: dict, label: str, total: int) -> dict:
    """Executa os passos 2–4 para um pedido. Retorna order_rec."""
    quote_id = item["quote_id"]
    order_id = item["order_id"]
    subject  = item.get("subject", "")[:50]
    order_t0 = time.time()

    logger.info("")
    logger.info("=" * _W)
    logger.info(f"  PEDIDO {label}/{total}: order={order_id}")
    logger.info(f"  Cotacao : {quote_id[:8]}... | \"{subject}\"")
    logger.info("=" * _W)

    order_rec = {
        "order_id": order_id, "quote_id": quote_id, "subject": subject,
        "status": "erro", "seconds": 0, "products_created": 0, "max_delivery": None,
    }

    # ── PASSO 2: Consultar ccwbot ──────────────────────────────────────────────
    _section(f"PASSO 2/4 | Consultando ccwbot no Webex (order={order_id})")
    try:
        xls_path = order_flow.run(order_number=order_id)
    except Exception as e:
        _err(f"order_flow.run falhou: {e}")
        order_rec["seconds"] = round(time.time() - order_t0)
        order_rec["message"] = f"ERRO ccwbot: {str(e)[:80]}"
        return order_rec

    if not xls_path:
        _warn(f"Nenhum arquivo XLS retornado para order={order_id}.")
        _warn("Verifique o arquivo messages.log para detalhes da sessao Webex.")
        order_rec["seconds"] = round(time.time() - order_t0)
        order_rec["message"] = "sem arquivo XLS"
        return order_rec

    _ok(f"XLS recebido: {xls_path}")

    # ── PASSO 3: Parsear XLS ───────────────────────────────────────────────────
    _section(f"PASSO 3/4 | Analisando XLS (order={order_id})")
    logger.info(f"  Arquivo : {xls_path}")

    try:
        parsed = parse_order_lines(xls_path)
    except Exception as e:
        _err(f"parse_order_lines falhou: {e}")
        order_rec["seconds"] = round(time.time() - order_t0)
        order_rec["message"] = f"ERRO parse: {str(e)[:80]}"
        return order_rec

    lines = parsed["lines"]
    max_d = parsed["max_estimated_delivery"]

    if not lines:
        _warn("Nenhuma linha com Estimated Delivery Date encontrada no XLS.")
        order_rec["seconds"] = round(time.time() - order_t0)
        order_rec["message"] = "sem linhas no XLS"
        return order_rec

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

    # ── PASSO 4: Atualizar portal ──────────────────────────────────────────────
    _section(f"PASSO 4/4 | Atualizando portal (quote={quote_id[:8]}...)")
    logger.info(f"  Endpoint: POST /api/v1/leadtime")
    logger.info(f"  Payload : quote_id={quote_id[:8]}... | order_id={order_id} | {len(lines)} linhas")

    try:
        resp        = portal_client.push_leadtime(
            quote_id=quote_id,
            order_id=order_id,
            lines=lines,
            max_estimated_delivery=max_d or "",
        )
        scenario    = resp.get("scenario", 0)
        n_created   = resp.get("products_created", 0)
        max_lt      = resp.get("max_lead_time_days", 0)
        final_deliv = resp.get("max_estimated_delivery", "")
        n_intersect = len(resp.get("intersection", []))
        n_only_port = len(resp.get("only_in_portal", []))
        n_only_ccw  = len(resp.get("only_in_ccw", []))

        _ok(f"Cenário {scenario} detectado:")
        if scenario == 1:
            _ok(f"  Todos os {n_intersect} produtos do portal encontrados no CCW.")
        elif scenario == 2:
            _ok(f"  {n_created} produto(s) CRIADOS no portal a partir do XLS CCW.")
        elif scenario == 3:
            _ok(f"  Intersecção: {n_intersect} produtos | "
                f"só portal: {n_only_port} | só CCW: {n_only_ccw}")
            if n_only_port:
                for pn in resp.get("only_in_portal", [])[:5]:
                    logger.info(f"    [!] No portal mas ausente no CCW: {pn}")
        top = resp.get("contributing_items", [{}])[0] if resp.get("contributing_items") else {}
        _ok(f"  LeadTime final: {max_lt}d ({final_deliv}) "
            f"— agressor: {top.get('part_number','—')}")
        _ok(f"  last_ccw_sync gravado em deals.json")

        order_rec["status"]             = "ok"
        order_rec["scenario"]           = scenario
        order_rec["products_created"]   = n_created
        order_rec["max_lead_time_days"] = max_lt
        order_rec["max_delivery"]       = final_deliv
    except Exception as e:
        _err(f"push_leadtime falhou: {e}")
        order_rec["message"] = f"ERRO push: {str(e)[:80]}"

    order_rec["seconds"] = round(time.time() - order_t0)
    return order_rec


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

    # Carregar estado de erros persistidos
    order_errors = _load_order_errors()

    # Separar ordens ativas de suspensas
    active_orders  = []
    skipped_orders = []
    for o in orders:
        if order_errors.get(o["order_id"], {}).get("suspended"):
            skipped_orders.append(o)
        else:
            active_orders.append(o)

    if skipped_orders:
        _warn(f"{len(skipped_orders)} ordem(s) SUSPENSA(S) — pulando:")
        for o in skipped_orders:
            n = order_errors[o["order_id"]].get("consecutive_failures", 0)
            logger.info(f"    [--] order={o['order_id']} ({n} falhas consecutivas — reativar manualmente)")

    _ok(f"{len(active_orders)} pedido(s) para processar:")
    for i, o in enumerate(active_orders, 1):
        last = f" | ultimo sync: {o['last_sync']}" if o.get("last_sync") else " | nunca sincronizado"
        logger.info(f"    {i:2d}. order={o['order_id']:<15} quote={o['quote_id'][:8]}... | \"{o.get('subject','')[:40]}\"{last}")

    # ── Loop principal por pedido ──────────────────────────────────────────────
    order_recs = []
    for idx, item in enumerate(active_orders, 1):
        rec = _process_one_order(item, str(idx), len(active_orders))
        order_recs.append(rec)

    # ── Camada 1: Retry após atualização completa no portal ────────────────────
    failed = [r for r in order_recs if r.get("status") != "ok"]
    if failed:
        _banner(f"RETRY — {len(failed)} ordem(s) com falha")
        logger.info(f"  Aguardando {RETRY_WAIT_SECS}s antes de retentar...")
        time.sleep(RETRY_WAIT_SECS)

        for rec in failed:
            item = next((o for o in active_orders if o["order_id"] == rec["order_id"]), None)
            if not item:
                continue
            logger.info(f"\n  >> RETRY order={rec['order_id']} | erro anterior: {rec.get('message','?')}")
            new_rec = _process_one_order(item, "RETRY", len(active_orders))
            if new_rec.get("status") == "ok":
                rec.update(new_rec)
                rec["retried"] = True
                _ok(f"Retry bem-sucedido para order={rec['order_id']}!")
            else:
                _warn(f"Retry falhou para order={rec['order_id']}: {new_rec.get('message','?')}")

    # ── Camada 2 & 3: Contadores de falha e suspensão automática ──────────────
    for rec in order_recs:
        oid      = rec["order_id"]
        err_info = order_errors.setdefault(oid, {"consecutive_failures": 0, "suspended": False})
        if rec.get("status") == "ok":
            err_info["consecutive_failures"] = 0
            err_info["suspended"]            = False
            err_info.pop("last_error",   None)
            err_info.pop("last_attempt", None)
        else:
            err_info["consecutive_failures"] = err_info.get("consecutive_failures", 0) + 1
            err_info["last_error"]           = rec.get("message", "erro desconhecido")
            err_info["last_attempt"]         = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            if (err_info["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES
                    and not err_info.get("suspended")):
                err_info["suspended"] = True
                _warn(f"order={oid} SUSPENSA após {err_info['consecutive_failures']} falhas consecutivas.")

    # Registrar ordens suspensas no resumo
    for o in skipped_orders:
        order_recs.append({
            "order_id": o["order_id"],
            "quote_id": o["quote_id"],
            "subject":  o.get("subject", ""),
            "status":   "suspensa",
            "seconds":  0,
            "message":  "suspensa — reativar manualmente",
        })

    _save_order_errors(order_errors)

    # ── Resumo final ───────────────────────────────────────────────────────────
    total_secs = round(time.time() - run_start)
    ok_count   = sum(1 for r in order_recs if r.get("status") == "ok")
    err_count  = sum(1 for r in order_recs if r.get("status") == "erro")
    susp_count = sum(1 for r in order_recs if r.get("status") == "suspensa")

    logger.info("")
    _banner("RESUMO FINAL")
    logger.info(f"  Total    : {len(orders)} pedido(s)")
    logger.info(f"  Sucesso  : {ok_count}")
    logger.info(f"  Falhas   : {err_count}")
    logger.info(f"  Suspensas: {susp_count}")
    logger.info("")
    for rec in order_recs:
        oid    = rec["order_id"]
        status = rec.get("status")
        icon   = "[OK]" if status == "ok" else ("[--]" if status == "suspensa" else "[XX]")
        scen   = f"C{rec.get('scenario','?')}" if rec.get("scenario") else "  "
        lt     = f"{rec.get('max_lead_time_days','?')}d" if status == "ok" else "—"
        deliv  = rec.get("max_delivery") or "—"
        extra  = " (retry)" if rec.get("retried") else (" (suspensa)" if status == "suspensa" else "")
        logger.info(f"  {icon} {scen} {oid:<18} leadtime={lt:<6} delivery={deliv}{extra}")
    logger.info("=" * _W)
    logger.info(f"  Log completo: {_LOG_FILE}")
    logger.info("=" * _W)

    # ── Grava métricas ─────────────────────────────────────────────────────────
    active_recs = [r for r in order_recs if r.get("status") != "suspensa"]
    avg_secs    = round(total_secs / len(active_recs), 1) if active_recs else 0
    run_rec     = {
        "started_at":            now_utc.strftime("%Y-%m-%dT%H:%M:%S"),
        "ended_at":              datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "total_seconds":         total_secs,
        "total_orders":          len(active_recs),
        "success":               ok_count,
        "failures":              err_count,
        "skipped":               susp_count,
        "avg_seconds_per_order": avg_secs,
        "orders":                order_recs,
    }
    try:
        _write_metrics(run_rec)
        _ok(f"metrics.json atualizado ({METRICS_FILE})")
    except Exception as e:
        _warn(f"Falha ao gravar metrics.json: {e}")

    try:
        updated_metrics = json.loads(METRICS_FILE.read_text(encoding="utf-8")) if METRICS_FILE.exists() else {"runs": []}
        _push_to_portal(updated_metrics, order_errors)
    except Exception as e:
        _warn(f"Falha no push de métricas ao portal: {e}")


if __name__ == "__main__":
    main()
