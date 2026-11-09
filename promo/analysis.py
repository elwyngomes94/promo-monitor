"""Análise de preços: desconto real x anunciado, comparação entre lojas e ranking.

Regras (ajustáveis em AnalysisConfig):
- Preço de referência = mediana do preço nos últimos 90 dias, ignorando os 3 dias mais
  recentes (para a própria promoção não contaminar a referência).
- Desconto real = (referência - preço atual) / referência. "Redução real" se >= 10%.
- Desconto maquiado = loja anuncia >= 15% de desconto ("de/por"), mas o desconto real < 5%.
- Aumento antes da promoção = nos 30 dias anteriores o preço subiu >= 20% acima da referência.
- Preço efetivo = preço - cupom + frete (quando não é grátis e é conhecido).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from .db import Database
from .stores import store_name


@dataclass
class AnalysisConfig:
    window_days: int = 90
    exclude_recent_days: int = 3
    min_points: int = 5
    real_discount_min: float = 0.10
    fake_announced_min: float = 0.15
    fake_real_max: float = 0.05
    pre_raise_factor: float = 1.20


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def reputation_score(rep: Optional[str]) -> float:
    if not rep:
        return 0.0
    r = rep.lower()
    if "platinum" in r or "excelente" in r or "5_green" in r:
        return 3.0
    if "gold" in r or "boa" in r:
        return 2.0
    if "regular" in r:
        return 0.5
    if "ruim" in r:
        return -3.0
    return 1.0


def analyze_listing(db: Database, row: dict, cfg: AnalysisConfig = AnalysisConfig(),
                    now: Optional[datetime] = None) -> dict:
    now = now or _parse(row["collected_at"])
    since = (now - timedelta(days=cfg.window_days)).isoformat()
    cutoff = now - timedelta(days=cfg.exclude_recent_days)
    hist = db.history(row["id"], since)
    past = [h["price"] for h in hist if _parse(h["collected_at"]) < cutoff]
    price = row["price"]

    ref = median(past) if len(past) >= cfg.min_points else None
    min_hist = min(past) if past else None
    recent30 = [h["price"] for h in hist
                if now - timedelta(days=30) <= _parse(h["collected_at"]) < cutoff]

    real = round((ref - price) / ref, 4) if ref else None
    announced = (round((row["list_price"] - price) / row["list_price"], 4)
                 if row.get("list_price") and row["list_price"] > price else 0.0)

    flags = []
    if real is not None and real >= cfg.real_discount_min:
        flags.append("desconto_real")
    if announced >= cfg.fake_announced_min and (real is None or real < cfg.fake_real_max):
        flags.append("desconto_maquiado")
    if ref and recent30 and max(recent30) >= ref * cfg.pre_raise_factor:
        flags.append("aumentou_antes")
    if min_hist is not None and price <= min_hist:
        flags.append("menor_preco_historico")
    if row.get("is_lightning"):
        flags.append("relampago")
    if row.get("free_shipping"):
        flags.append("frete_gratis")
    if row.get("coupon_value"):
        flags.append("cupom")

    score = 0.0
    if real is not None and real > 0:
        score += real * 100
    if "menor_preco_historico" in flags:
        score += 5
    if "relampago" in flags:
        score += 4
    if "frete_gratis" in flags:
        score += 3
    if "cupom" in flags:
        score += 2
    score += reputation_score(row.get("seller_reputation"))
    if "desconto_maquiado" in flags:
        score -= 15
    if "aumentou_antes" in flags:
        score -= 10

    return {
        "listing_id": row["id"], "store_id": row["store_id"],
        "store_name": store_name(row["store_id"]),
        "group_id": row.get("group_id"), "group_name": row.get("group_name"),
        "title": row["title"], "url": row["url"], "image": row.get("image"),
        "category": row.get("category"), "brand": row.get("brand"),
        "price": price, "list_price": row.get("list_price"),
        "effective_price": row["effective_price"],
        "free_shipping": bool(row.get("free_shipping")),
        "shipping_cost": row.get("shipping_cost"),
        "coupon_value": row.get("coupon_value") or 0, "coupon_code": row.get("coupon_code"),
        "is_lightning": bool(row.get("is_lightning")), "promo_label": row.get("promo_label"),
        "seller_id": row.get("seller_id"), "seller_name": row.get("seller_name"),
        "seller_reputation": row.get("seller_reputation"), "seller_sales": row.get("seller_sales"),
        "catalog_id": row.get("catalog_id"),
        "reference_price": round(ref, 2) if ref else None,
        "min_price_90d": min_hist,
        "real_discount": real, "announced_discount": announced,
        "flags": flags, "score": round(score, 2),
        "collected_at": row["collected_at"],
    }


def analyze_all(db: Database, store_ids: Optional[list[str]] = None,
                cfg: AnalysisConfig = AnalysisConfig()) -> list[dict]:
    return [analyze_listing(db, r, cfg) for r in db.current_listings(store_ids)]


def filter_offers(items: list[dict], category: Optional[str] = None, q: Optional[str] = None,
                  only_real: bool = False, free_shipping: bool = False,
                  lightning: bool = False, max_price: Optional[float] = None) -> list[dict]:
    out = items
    if category:
        out = [i for i in out if i.get("category") == category]
    if q:
        ql = q.lower()
        out = [i for i in out if ql in i["title"].lower()]
    if only_real:
        out = [i for i in out if "desconto_real" in i["flags"]]
    if free_shipping:
        out = [i for i in out if i["free_shipping"]]
    if lightning:
        out = [i for i in out if i["is_lightning"]]
    if max_price is not None:
        out = [i for i in out if i["effective_price"] <= max_price]
    return out


def compare_groups(items: list[dict]) -> dict[int, dict]:
    """Para cada produto (grupo), as ofertas de todas as lojas ordenadas por preço efetivo."""
    groups: dict[int, dict] = {}
    for it in items:
        gid = it["group_id"]
        if gid is None:
            continue
        g = groups.setdefault(gid, {"group_id": gid, "name": it["group_name"], "offers": []})
        g["offers"].append(it)
    for g in groups.values():
        g["offers"].sort(key=lambda o: o["effective_price"])
        best = g["offers"][0]
        g["stores"] = sorted({o["store_id"] for o in g["offers"]})
        g["best_store"] = best["store_id"]
        g["best_price"] = best["effective_price"]
        prices = [o["effective_price"] for o in g["offers"]]
        g["worst_price"] = max(prices)
        g["spread"] = round(max(prices) - min(prices), 2)
    return groups


def ranking(items: list[dict], all_items: Optional[list[dict]] = None, limit: int = 50) -> list[dict]:
    """Melhores promoções. Mantém uma oferta por produto e loja (a de maior pontuação)
    e indica se o mesmo produto está mais barato em outra loja monitorada."""
    groups = compare_groups(all_items if all_items is not None else items)
    best_per: dict[tuple, dict] = {}
    for it in items:
        k = (it["group_id"], it["store_id"])
        if k not in best_per or it["score"] > best_per[k]["score"]:
            best_per[k] = it
    ranked = sorted(best_per.values(), key=lambda i: (-i["score"], i["effective_price"]))
    out = []
    for pos, it in enumerate(ranked[:limit], 1):
        g = groups.get(it["group_id"])
        cheaper = None
        if g and g["best_price"] < it["effective_price"] - 0.01:
            cheaper = {"store_id": g["best_store"], "store_name": store_name(g["best_store"]),
                       "effective_price": g["best_price"]}
        out.append({**it, "rank": pos, "cheaper_elsewhere": cheaper,
                    "stores_compared": len(g["stores"]) if g else 1})
    return out


def summary(items: list[dict]) -> dict:
    by_store: dict[str, dict] = {}
    for it in items:
        s = by_store.setdefault(it["store_id"], {"store_id": it["store_id"],
                                                 "store_name": it["store_name"], "offers": 0,
                                                 "real_discounts": 0, "fake_discounts": 0,
                                                 "lightning": 0, "free_shipping": 0,
                                                 "avg_real_discount": 0.0})
        s["offers"] += 1
        s["real_discounts"] += "desconto_real" in it["flags"]
        s["fake_discounts"] += "desconto_maquiado" in it["flags"]
        s["lightning"] += it["is_lightning"]
        s["free_shipping"] += it["free_shipping"]
        if "desconto_real" in it["flags"]:
            s["avg_real_discount"] += it["real_discount"]
    for s in by_store.values():
        if s["real_discounts"]:
            s["avg_real_discount"] = round(s["avg_real_discount"] / s["real_discounts"], 4)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_offers": len(items),
        "real_discounts": sum(s["real_discounts"] for s in by_store.values()),
        "fake_discounts": sum(s["fake_discounts"] for s in by_store.values()),
        "lightning": sum(s["lightning"] for s in by_store.values()),
        "stores": sorted(by_store.values(), key=lambda s: s["store_name"]),
    }
