import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promo.analysis import analyze_all, compare_groups, ranking  # noqa: E402
from promo.collectors.affiliate_feed import AffiliateFeedCollector, parse_money  # noqa: E402
from promo.collectors.mercadolivre import MercadoLivreCollector  # noqa: E402
from promo.db import Database  # noqa: E402
from promo.matching import Matcher  # noqa: E402
from promo.models import Offer  # noqa: E402
from promo.pipeline import apply_coupons, ingest  # noqa: E402
from promo.web import to_csv  # noqa: E402


def mem_db():
    """SQLite em memória, ou um Postgres limpo se PROMO_TEST_PG estiver definido."""
    url = os.environ.get("PROMO_TEST_PG")
    if not url:
        return Database(":memory:")
    from promo.pgwire import connect
    c = connect(url)
    c.query("DROP TABLE IF EXISTS price_history, sellers, collection_runs, app_state, listings, product_groups CASCADE")
    c.close()
    return Database(url)


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).replace(microsecond=0).isoformat()


def offer(store="amazon", price=100.0, **kw):
    return Offer(store_id=store, external_id=kw.pop("eid", "X1"), title=kw.pop("title", "Produto Teste 10"),
                 price=price, url="https://exemplo/x", **kw)


# ---------- preço efetivo e cupons ----------
def test_effective_price_includes_coupon_and_shipping():
    assert offer(price=100, coupon_value=10, shipping_cost=15).effective_price == 105
    assert offer(price=100, coupon_value=10, shipping_cost=15, free_shipping=True).effective_price == 90


def test_best_coupon_is_applied():
    o = offer(store="netshoes", price=300)
    coupons = [{"store": "netshoes", "code": "A", "percent": 10, "max_discount": 20},
               {"store": "netshoes", "code": "B", "value": 25, "min_value": 250},
               {"store": "netshoes", "code": "C", "value": 99, "valid_until": "2000-01-01"}]
    apply_coupons(o, coupons)
    assert (o.coupon_code, o.coupon_value) == ("B", 25)


def test_parse_money_br_format():
    assert parse_money("R$ 1.299,90") == 1299.9
    assert parse_money("89.9") == 89.9
    assert parse_money("") is None


# ---------- casamento entre lojas ----------
def test_matching_by_gtin_and_title():
    m = Matcher()
    k1 = m.key_for("Fone Bluetooth Pulse 3 ANC Sonora", "Sonora", "7890000000011")
    k2 = m.key_for("Sonora Fone Bluetooth Pulse 3 ANC Original Envio Imediato", "Sonora", None)
    k3 = m.key_for("Fone Bluetooth Pulse 4 ANC Sonora", "Sonora", None)
    assert k1 == k2
    assert k3 != k1   # modelo diferente não pode casar


# ---------- desconto real x maquiado ----------
def _seed_history(db, prices, list_price=None, store="amazon", eid="X1"):
    for i, p in enumerate(prices):
        day = len(prices) - 1 - i
        lp = list_price if day == 0 else None
        ingest(db, [offer(store=store, price=p, list_price=lp, eid=eid)], collected_at=days_ago(day))


def test_real_discount_detected():
    db = mem_db()
    _seed_history(db, [100.0] * 30 + [75.0])
    [it] = analyze_all(db)
    assert "desconto_real" in it["flags"]
    assert round(it["real_discount"], 2) == 0.25
    assert "menor_preco_historico" in it["flags"]


def test_fake_discount_detected():
    db = mem_db()
    _seed_history(db, [100.0] * 30 + [99.0], list_price=200.0)
    [it] = analyze_all(db)
    assert "desconto_maquiado" in it["flags"]
    assert "desconto_real" not in it["flags"]


def test_price_raised_before_promo():
    db = mem_db()
    _seed_history(db, [100.0] * 30 + [140.0] * 10 + [100.0] * 3)
    [it] = analyze_all(db)
    assert "aumentou_antes" in it["flags"]
    assert "desconto_real" not in it["flags"]


def test_ranking_and_cheaper_elsewhere():
    db = mem_db()
    _seed_history(db, [100.0] * 20 + [80.0], store="amazon", eid="A")
    _seed_history(db, [70.0] * 21, store="mercadolivre", eid="M")
    items = analyze_all(db)
    groups = compare_groups(items)
    assert len(groups) == 1
    top = ranking(items)[0]
    assert top["store_id"] == "amazon"
    assert top["cheaper_elsewhere"]["store_id"] == "mercadolivre"
    assert "Mercado Livre" in to_csv(ranking(items))


# ---------- coletores ----------
def test_mercadolivre_parser_and_sellers():
    calls = []

    def fake_http(url, params=None, headers=None):
        calls.append(url)
        if "/search" in url:
            return {"results": [] if params["offset"] else [{
                "id": "MLB1", "title": "Air Fryer Lumen 4L", "price": 300, "original_price": 450,
                "permalink": "https://produto.mercadolivre.com.br/MLB-1", "catalog_product_id": "MLB-CAT1",
                "shipping": {"free_shipping": True}, "seller": {"id": 9},
                "tags": ["lightning_deal"], "attributes": [{"id": "GTIN", "value_name": "7890000000066"}]}]}
        if "/products/MLB-CAT1/items" in url:
            return {"results": [{"item_id": "MLB2", "price": 310, "seller_id": 10,
                                 "shipping": {"free_shipping": False}}]}
        if "/users/" in url:
            return {"nickname": "LOJA", "seller_reputation": {"level_id": "5_green",
                    "power_seller_status": "platinum", "transactions": {"completed": 1000}}}
        raise AssertionError(url)

    col = MercadoLivreCollector({"queries": ["air fryer"], "access_token": "t"}, http=fake_http)
    offers = list(col.collect())
    assert [o.external_id for o in offers] == ["MLB1", "MLB2"]
    o = offers[0]
    assert o.is_lightning and o.free_shipping and o.gtin == "7890000000066"
    assert o.list_price == 450 and o.seller_reputation == "Excelente · MercadoLíder Platinum"
    assert offers[1].catalog_id == "MLB-CAT1"


def test_affiliate_csv_feed(tmp_path):
    f = tmp_path / "feed.csv"
    f.write_text("sku;nome;preco_por;preco_de;link;frete\n"
                 "1;Tênis X;R$ 199,90;R$ 299,90;https://loja/1;sim\n"
                 ";sem id;10;;https://loja/2;\n", encoding="utf-8")
    col = AffiliateFeedCollector("netshoes", {"feed_file": str(f), "fields": {
        "external_id": "sku", "title": "nome", "price": "preco_por", "list_price": "preco_de",
        "url": "link", "free_shipping": "frete"}})
    [o] = list(col.collect())
    assert (o.price, o.list_price, o.free_shipping) == (199.9, 299.9, True)


def test_demo_copy_and_clear():
    from promo.demo import load_demo
    db = mem_db()
    n = load_demo(db, days=10)
    assert n > 0 and not db.is_empty()
    items = analyze_all(db)
    assert len(items) == 83 and all(i["image"] for i in items)
    assert len(compare_groups(items)) == 18
    # inserir depois da cópia precisa continuar gerando ids novos
    ingest(db, [offer(store="amazon", eid="REAL-1", title="Produto Real 1")])
    assert db.clear_demo() > 0
    assert [i["external_id"] for i in db.current_listings()] == ["REAL-1"]


def test_state_roundtrip():
    db = mem_db()
    assert db.get_state("k") is None
    db.set_state("k", "a")
    db.set_state("k", "b")
    assert db.get_state("k") == "b"


def test_secretbox_and_ml_token_storage():
    from promo.pipeline import ML_TOKEN_KEY, load_ml_token, save_ml_token
    from promo.secretbox import decrypt, encrypt
    t = encrypt("segredo", "TG-abc-123")
    assert "TG-abc" not in t and decrypt("segredo", t) == "TG-abc-123"
    import pytest
    with pytest.raises(ValueError):
        decrypt("outra-chave", t)
    db = mem_db()
    cfg = {"client_secret": "segredo"}
    save_ml_token(db, cfg, "TG-novo")
    assert "TG-novo" not in db.get_state(ML_TOKEN_KEY)       # gravado cifrado
    assert load_ml_token(db, cfg) == "TG-novo"
    assert load_ml_token(db, {"client_secret": "errado"}) is None


def test_safe_url_strips_tokens():
    from promo.collectors.base import safe_url
    assert safe_url("https://feed.exemplo/x.csv?token=SEGREDO") == "https://feed.exemplo/x.csv"


def test_compact_keeps_one_price_per_day():
    db = mem_db()
    for d in (30, 20):
        for h in range(3):
            noon = (datetime.now(timezone.utc) - timedelta(days=d)).replace(hour=12, minute=0, second=0, microsecond=0)
            ingest(db, [offer(price=100 + h)], collected_at=(noon + timedelta(hours=h)).isoformat())
    ingest(db, [offer(price=90)], collected_at=days_ago(0))
    removed = db.compact()
    assert removed == 4
    assert len(db.history(1)) == 3


def test_build_site(tmp_path):
    from promo.demo import load_demo
    from promo.site import build_site
    db = mem_db()
    load_demo(db, days=5)
    path = build_site(db, str(tmp_path))
    html = open(path, encoding="utf-8").read()
    assert "__DATA__" not in html and '"demo":true' in html and "Fone Bluetooth" in html
    assert (tmp_path / ".nojekyll").exists()
