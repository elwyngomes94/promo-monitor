"""Dados de demonstração: produtos FICTÍCIOS com 90 dias de histórico em todas as lojas.

Permite ver o dashboard funcionando antes de configurar as credenciais reais.
Inclui cenários que o sistema precisa distinguir:
- queda real de preço;
- desconto "maquiado" (preço "de" inflado, preço atual igual ao habitual);
- preço aumentado antes da promoção ("metade do dobro");
- ofertas relâmpago e vários vendedores no Mercado Livre;
- mesmo produto com e sem EAN em lojas diferentes.
"""
import random
from datetime import datetime, timedelta, timezone

from .db import Database
from .matching import Matcher
from .models import Offer
from .pipeline import ingest

# nome, marca, categoria, preço base, ean (ou None), lojas
PRODUCTS = [
    ("Fone Bluetooth Pulse 3 ANC", "Sonora", "Eletrônicos", 399.0, "7890000000011",
     ["mercadolivre", "amazon", "magalu", "shopee", "tiktokshop"]),
    ("Smartwatch Kinetix Fit 2 GPS", "Kinetix", "Eletrônicos", 699.0, "7890000000028",
     ["mercadolivre", "amazon", "magalu", "netshoes", "centauro"]),
    ("Caixa de Som Portátil Boom 20W", "Sonora", "Eletrônicos", 249.0, None,
     ["mercadolivre", "amazon", "shopee", "magalu"]),
    ("Tênis de Corrida Vértice Run 5", "Vértice", "Esporte", 549.0, "7890000000035",
     ["mercadolivre", "netshoes", "centauro", "amazon"]),
    ("Tênis Casual Vértice Street", "Vértice", "Esporte", 329.0, None,
     ["netshoes", "centauro", "shein", "mercadolivre"]),
    ("Garrafa Térmica Inox 1L", "Polar", "Esporte", 119.0, "7890000000042",
     ["centauro", "netshoes", "amazon", "shopee", "mercadolivre"]),
    ("Kit Halteres Ajustáveis 20kg", "Kinetix", "Esporte", 459.0, None,
     ["mercadolivre", "centauro", "amazon"]),
    ("Perfume Aurora Bloom 100ml", "Aurora", "Beleza", 229.0, "7890000000059",
     ["boticario", "amazon", "mercadolivre", "magalu"]),
    ("Hidratante Corporal Aurora Nutri 400ml", "Aurora", "Beleza", 69.9, None,
     ["boticario", "shopee", "mercadolivre", "tiktokshop"]),
    ("Base Líquida Aurora Matte 30ml", "Aurora", "Beleza", 89.9, None,
     ["boticario", "tiktokshop", "shopee", "amazon"]),
    ("Vestido Midi Floral", "Lumen", "Moda", 129.0, None,
     ["shein", "shopee", "tiktokshop", "mercadolivre"]),
    ("Jaqueta Corta-Vento Unissex", "Lumen", "Moda", 189.0, None,
     ["shein", "netshoes", "centauro", "mercadolivre"]),
    ("Air Fryer Lumen 4L Digital", "Lumen", "Casa", 449.0, "7890000000066",
     ["magalu", "amazon", "mercadolivre", "shopee"]),
    ("Liquidificador Turbo 1200W", "Polar", "Casa", 229.0, "7890000000073",
     ["magalu", "amazon", "mercadolivre"]),
    ("Aspirador Robô Polar Clean S", "Polar", "Casa", 1299.0, "7890000000080",
     ["magalu", "amazon", "mercadolivre", "shopee"]),
    ("Monitor 27 pol IPS 144Hz", "Vértice", "Informática", 1399.0, "7890000000097",
     ["amazon", "magalu", "mercadolivre"]),
    ("SSD NVMe 1TB Gen4", "Kinetix", "Informática", 499.0, "7890000000103",
     ["amazon", "mercadolivre", "magalu", "shopee"]),
    ("Mouse Sem Fio Ergonômico", "Kinetix", "Informática", 149.0, None,
     ["amazon", "mercadolivre", "shopee", "tiktokshop", "magalu"]),
]

# cenário aplicado nos últimos dias, por produto (índice)
SCENARIOS = {
    0: "real_drop", 1: "fake_list", 3: "real_drop", 5: "real_drop", 7: "pre_raise",
    8: "real_drop", 10: "real_drop", 12: "real_drop", 13: "fake_list", 14: "real_drop",
    15: "pre_raise", 16: "real_drop",
}

STORE_FACTOR = {"amazon": 0.98, "magalu": 1.0, "mercadolivre": 0.97, "shopee": 0.95,
                "shein": 0.9, "tiktokshop": 0.93, "centauro": 1.02, "netshoes": 1.0,
                "boticario": 1.0}

STORE_URL = {
    "amazon": "https://www.amazon.com.br/dp/DEMO{n}", "magalu": "https://www.magazineluiza.com.br/p/demo{n}/",
    "mercadolivre": "https://produto.mercadolivre.com.br/MLB-DEMO{n}", "shopee": "https://shopee.com.br/demo-i.{n}",
    "shein": "https://br.shein.com/demo-p-{n}.html", "tiktokshop": "https://shop.tiktok.com/view/product/demo{n}",
    "centauro": "https://www.centauro.com.br/demo-{n}", "netshoes": "https://www.netshoes.com.br/demo-{n}",
    "boticario": "https://www.boticario.com.br/demo-{n}/",
}

ML_SELLERS = [("101", "LOJA_DEMO_A", "Excelente · MercadoLíder Platinum", 52000),
              ("102", "LOJA_DEMO_B", "Boa · MercadoLíder Gold", 8400),
              ("103", "LOJA_DEMO_C", "Regular", 310)]

TITLE_VARIANTS = ["{name} {brand}", "{brand} {name} Original", "{name} - {brand} Lançamento",
                  "{name} {brand} Envio Imediato"]


# ilustrações geradas (os produtos são fictícios, então não há foto real)
CATEGORY_ART = {
    "Eletrônicos": ("#dbe8f5", "#2f5d8a", '<rect x="22" y="30" width="56" height="40" rx="6"/><circle cx="50" cy="50" r="10" fill="#dbe8f5"/>'),
    "Esporte": ("#dff0e4", "#2d7a4b", '<path d="M18 62 L40 40 L58 52 L82 34 L82 66 L18 66 Z"/>'),
    "Beleza": ("#f6e1ea", "#9a3d64", '<rect x="40" y="22" width="20" height="12" rx="2"/><rect x="30" y="34" width="40" height="44" rx="10"/>'),
    "Moda": ("#f3e8d8", "#8a5a1f", '<path d="M36 24 L50 32 L64 24 L80 36 L72 46 L66 42 L66 78 L34 78 L34 42 L28 46 L20 36 Z"/>'),
    "Casa": ("#eee6f6", "#5b3f8c", '<path d="M50 22 L80 46 L72 46 L72 78 L28 78 L28 46 L20 46 Z"/>'),
    "Informática": ("#e1eef0", "#2b6b74", '<rect x="18" y="26" width="64" height="40" rx="4"/><rect x="40" y="70" width="20" height="6"/>'),
}


def product_image(category: str, brand: str) -> str:
    import urllib.parse
    bg, fg, shape = CATEGORY_ART.get(category, ("#eeeeee", "#555555", '<circle cx="50" cy="50" r="24"/>'))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           f'<rect width="100" height="100" fill="{bg}"/><g fill="{fg}">{shape}</g>'
           f'<text x="50" y="94" font-family="sans-serif" font-size="10" font-weight="700" '
           f'text-anchor="middle" fill="{fg}">{brand.upper()}</text></svg>')
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)


def _price_on(base: float, day: int, scenario: str, rnd: random.Random) -> tuple[float, float]:
    """Retorna (preço, preço 'de') para o dia `day` (0 = hoje, 89 = 89 dias atrás)."""
    noise = 1 + rnd.uniform(-0.03, 0.03)
    price, list_price = base * noise, base * 1.10
    if scenario == "real_drop" and day <= 2:
        price = base * rnd.uniform(0.66, 0.8)
    elif scenario == "fake_list" and day <= 2:
        list_price = base * 1.8          # "de R$ X por R$ Y" sem queda real
    elif scenario == "pre_raise":
        if 3 <= day <= 16:
            price, list_price = base * 1.35, base * 1.35
        elif day <= 2:
            price, list_price = base * 0.97, base * 1.35   # "promoção" volta ao preço normal
    return round(price, 2), round(list_price, 2)


def seed_demo(db: Database, days: int = 90, seed: int = 42) -> int:
    rnd = random.Random(seed)
    matcher = Matcher()
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    n = 0
    for day in range(days - 1, -1, -1):
        ts = (today - timedelta(days=day)).isoformat()
        offers = []
        for i, (name, brand, cat, base, ean, stores) in enumerate(PRODUCTS):
            scenario = SCENARIOS.get(i, "stable")
            for j, store in enumerate(stores):
                prnd = random.Random(f"{seed}-{i}-{store}-{day}")
                price, list_price = _price_on(base * STORE_FACTOR[store], day, scenario, prnd)
                # só a loja "principal" do cenário recebe a promoção forte
                if scenario != "stable" and j != (i % len(stores)) and day <= 2:
                    price, list_price = _price_on(base * STORE_FACTOR[store], 10, "stable", prnd)
                title = TITLE_VARIANTS[(i + j) % len(TITLE_VARIANTS)].format(name=name, brand=brand)
                free = store in {"amazon", "mercadolivre", "magalu"} and price >= 79 or store == "shein" and price > 149
                sellers = ML_SELLERS if store == "mercadolivre" else [(None, None, None, None)]
                for k, (sid, sname, rep, sales) in enumerate(sellers):
                    if store == "mercadolivre" and k > 0 and i % 3 != 0:
                        continue  # nem todo produto tem vários vendedores
                    seller_factor = 1 + 0.04 * k
                    offers.append(Offer(
                        store_id=store, external_id=f"DEMO-{i}-{k}",
                        title=f"[DEMO] {title}",
                        price=round(price * seller_factor, 2), list_price=list_price,
                        url=STORE_URL[store].format(n=f"{i}{k}"),
                        gtin=ean if (ean and j % 2 == 0) else None,   # EAN só em parte das lojas
                        brand=brand, category=cat, image=product_image(cat, brand),
                        free_shipping=free if k == 0 else False,
                        shipping_cost=None if free else round(prnd.uniform(9.9, 29.9), 2),
                        coupon_value=round(price * 0.05, 2) if store in {"shopee", "tiktokshop"} and day <= 1 else 0,
                        coupon_code="DEMO5" if store in {"shopee", "tiktokshop"} and day <= 1 else None,
                        is_lightning=store == "mercadolivre" and scenario == "real_drop" and day == 0 and k == 0 and j == i % len(stores),
                        promo_label="lightning" if store == "mercadolivre" and scenario == "real_drop" and day == 0 and k == 0 and j == i % len(stores) else None,
                        seller_id=sid, seller_name=sname, seller_reputation=rep, seller_sales=sales,
                        catalog_id=f"MLB-CAT-DEMO{i}" if store == "mercadolivre" else None,
                    ))
        n += ingest(db, offers, matcher=matcher, collected_at=ts)
    return n
