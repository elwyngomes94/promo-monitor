"""Orquestra a coleta: coletor -> cupons -> casamento entre lojas -> banco."""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, Optional

from .collectors.affiliate_feed import AffiliateFeedCollector
from .collectors.base import Collector
from .collectors.mercadolivre import MercadoLivreCollector
from .db import Database
from .matching import Matcher
from .models import Offer
from .stores import STORES

log = logging.getLogger("promo")


def build_collector(store_id: str, cfg: dict) -> Collector:
    if store_id == "mercadolivre":
        return MercadoLivreCollector(cfg)
    return AffiliateFeedCollector(store_id, cfg)


def load_coupons(path: str = "coupons.json") -> list[dict]:
    """Cupons cadastrados manualmente ou vindos de fonte autorizada.

    Formato: [{"store": "netshoes", "code": "BLACK10", "percent": 10, "min_value": 199,
               "max_discount": 100, "category": null, "valid_until": "2026-11-30"}]
    """
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def apply_coupons(offer: Offer, coupons: list[dict], today: Optional[str] = None) -> Offer:
    """Aplica o melhor cupom válido, caso a loja não tenha informado um no próprio feed."""
    if offer.coupon_value:
        return offer
    today = today or datetime.now(timezone.utc).date().isoformat()
    best_value, best_code = 0.0, None
    for c in coupons:
        if c.get("store") != offer.store_id:
            continue
        if c.get("valid_until") and c["valid_until"] < today:
            continue
        if c.get("category") and c["category"] != offer.category:
            continue
        if offer.price < float(c.get("min_value") or 0):
            continue
        value = float(c.get("value") or 0) + offer.price * float(c.get("percent") or 0) / 100
        if c.get("max_discount"):
            value = min(value, float(c["max_discount"]))
        if value > best_value:
            best_value, best_code = round(value, 2), c.get("code")
    if best_value:
        offer.coupon_value, offer.coupon_code = best_value, best_code
    return offer


def ingest(db: Database, offers: Iterable[Offer], coupons: Optional[list[dict]] = None,
           matcher: Optional[Matcher] = None, collected_at: Optional[str] = None) -> int:
    if matcher is None:
        matcher = Matcher()
        matcher.load([(r["match_key"], r["name"]) for r in
                      db.q("SELECT match_key, name FROM product_groups")])
    n = 0
    for offer in offers:
        apply_coupons(offer, coupons or [])
        key = matcher.key_for(offer.title, offer.brand, offer.gtin)
        gid = db.group_id_for(key, offer.title)
        db.save_offer(offer, gid, collected_at)
        n += 1
    db.commit()
    return n


def run_store(db: Database, store_id: str, cfg: dict, coupons: list[dict]) -> int:
    run_id = db.start_run(store_id)
    try:
        if store_id == "mercadolivre":
            # o refresh_token do ML muda a cada renovação: o mais recente fica no banco,
            # assim funciona em ambientes sem disco (GitHub Actions, Vercel)
            saved = db.get_state("mercadolivre_refresh_token")
            if saved:
                cfg = {**cfg, "refresh_token": saved}
        collector = build_collector(store_id, cfg)
        try:
            n = ingest(db, collector.collect(), coupons)
        finally:
            new_token = getattr(collector, "config", {}).get("refresh_token")
            if store_id == "mercadolivre" and new_token and new_token != db.get_state("mercadolivre_refresh_token"):
                db.set_state("mercadolivre_refresh_token", new_token)
        db.finish_run(run_id, "ok", n)
        log.info("%s: %d ofertas", STORES[store_id].name, n)
        return n
    except Exception as e:  # noqa: BLE001 — uma loja com erro não derruba as outras
        db.rollback()
        db.finish_run(run_id, "erro", 0, str(e)[:500])
        log.error("%s: %s", STORES[store_id].name, e)
        return 0


def run_all(db: Database, config: dict, only: Optional[list[str]] = None) -> dict[str, int]:
    coupons = load_coupons(config.get("coupons_file", "coupons.json"))
    result = {}
    for store_id, cfg in config.get("stores", {}).items():
        if store_id not in STORES or not cfg.get("enabled"):
            continue
        if only and store_id not in only:
            continue
        result[store_id] = run_store(db, store_id, cfg, coupons)
    return result


def scheduler_loop(db_path: Optional[str], config: dict, stop=None, on_saved=None, tick: int = 30):
    """Coleta contínua respeitando a frequência de cada loja.

    Usa conexão própria com o banco (pode rodar numa thread ao lado do dashboard)."""
    import time
    db = Database(db_path)
    last: dict[str, float] = {}
    while not (stop and stop.is_set()):
        due = []
        for sid, scfg in config.get("stores", {}).items():
            if sid in STORES and scfg.get("enabled"):
                every = 60 * int(scfg.get("interval_minutes", STORES[sid].interval_minutes))
                if time.time() - last.get(sid, 0) >= every:
                    due.append(sid)
        if due:
            run_all(db, config, due)
            if on_saved:
                on_saved(config)
            for sid in due:
                last[sid] = time.time()
        if stop:
            stop.wait(tick)
        else:
            time.sleep(tick)
    db.close()
