"""Coletor do Mercado Livre via API oficial (https://api.mercadolibre.com).

Requisitos atendidos:
- produtos e ofertas (busca por termos e/ou categorias)
- ofertas relâmpago e promoções especiais (tags / sale_price.metadata.promotion_type)
- comparação entre vendedores do mesmo produto de catálogo (/products/{id}/items)
- frete grátis (shipping.free_shipping)
- vendedor e reputação (/users/{id})
- link direto (permalink)

Acesso: crie um aplicativo em developers.mercadolivre.com.br e preencha client_id,
client_secret e refresh_token em config.json. As permissões e limites de cada
endpoint mudam com o tempo — confira a documentação oficial antes de ir para produção.
"""
import json
import urllib.parse
import urllib.request
from typing import Iterator, Optional

from ..models import Offer
from .base import USER_AGENT, Collector, CollectorError

API = "https://api.mercadolibre.com"
SITE = "MLB"

LIGHTNING_TYPES = {"lightning", "lightning_deal", "dod", "deal_of_the_day"}
LIGHTNING_TAGS = {"lightning_deal", "deal_of_the_day"}

REPUTATION_LABEL = {
    "5_green": "Excelente", "4_light_green": "Boa", "3_yellow": "Regular",
    "2_orange": "Ruim", "1_red": "Muito ruim",
}


class MercadoLivreCollector(Collector):
    store_id = "mercadolivre"

    def __init__(self, config: dict, http=None):
        super().__init__(config)
        self.access_token: Optional[str] = self.config.get("access_token")
        self._seller_cache: dict[str, dict] = {}
        if http is not None:          # injeção para testes
            self.http_get_json = http

    # -------- autenticação OAuth --------
    def refresh_access_token(self):
        cid, secret, refresh = (self.config.get(k) for k in
                                ("client_id", "client_secret", "refresh_token"))
        if not (cid and secret and refresh):
            return
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token", "client_id": cid,
            "client_secret": secret, "refresh_token": refresh}).encode()
        req = urllib.request.Request(f"{API}/oauth/token", data=data, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"})
        self.limiter.wait()
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read())
        except Exception as e:  # noqa: BLE001
            raise CollectorError(f"Falha ao renovar token do Mercado Livre: {e}") from e
        self.access_token = payload["access_token"]
        # o ML devolve um novo refresh_token a cada renovação: guarde-o
        self.config["refresh_token"] = payload.get("refresh_token", refresh)

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}
        return self.http_get_json(f"{API}{path}", params, headers)

    # -------- coleta --------
    def collect(self) -> Iterator[Offer]:
        if not self.access_token:
            self.refresh_access_token()
        seen: set[str] = set()
        limit_total = int(self.config.get("max_items_per_query", 50))
        searches = [{"q": q} for q in self.config.get("queries", [])]
        searches += [{"category": c} for c in self.config.get("categories", [])]
        catalogs: set[str] = set()

        for params in searches:
            offset = 0
            while offset < limit_total:
                page = self._get(f"/sites/{SITE}/search",
                                 {**params, "offset": offset, "limit": min(50, limit_total - offset)})
                results = page.get("results", [])
                if not results:
                    break
                for item in results:
                    if item["id"] in seen:
                        continue
                    seen.add(item["id"])
                    if item.get("catalog_product_id"):
                        catalogs.add(item["catalog_product_id"])
                    yield self.parse_item(item)
                offset += len(results)

        # comparação de preços entre vendedores do mesmo produto de catálogo
        if self.config.get("compare_sellers", True):
            for cat_id in catalogs:
                for offer in self.catalog_offers(cat_id):
                    if offer.external_id not in seen:
                        seen.add(offer.external_id)
                        yield offer

    def catalog_offers(self, catalog_id: str) -> Iterator[Offer]:
        try:
            data = self._get(f"/products/{catalog_id}/items")
        except CollectorError:
            return
        for item in data.get("results", []):
            item = {**item, "catalog_product_id": catalog_id}
            item.setdefault("id", item.get("item_id"))
            item.setdefault("permalink", f"https://produto.mercadolivre.com.br/{item['id'].replace('MLB', 'MLB-')}")
            yield self.parse_item(item)

    # -------- conversão --------
    def parse_item(self, item: dict) -> Offer:
        price = float(item.get("price") or 0)
        list_price = item.get("original_price")
        sale = item.get("sale_price") or {}
        promo_type = ((sale.get("metadata") or {}).get("promotion_type") or "").lower()
        if sale.get("regular_amount") and not list_price:
            list_price = sale["regular_amount"]
        tags = set(item.get("tags") or [])
        shipping = item.get("shipping") or {}
        seller = item.get("seller") or {}
        seller_id = str(seller.get("id") or item.get("seller_id") or "") or None

        gtin = brand = None
        for attr in item.get("attributes") or []:
            if attr.get("id") == "GTIN":
                gtin = attr.get("value_name")
            elif attr.get("id") == "BRAND":
                brand = attr.get("value_name")

        offer = Offer(
            store_id=self.store_id,
            external_id=item["id"],
            title=item.get("title") or "",
            price=price,
            list_price=float(list_price) if list_price else None,
            url=item.get("permalink") or "",
            gtin=gtin, brand=brand,
            category=item.get("category_id"),
            image=(item.get("secure_thumbnail") or item.get("thumbnail") or "").replace("http://", "https://") or None,
            free_shipping=bool(shipping.get("free_shipping")),
            is_lightning=promo_type in LIGHTNING_TYPES or bool(tags & LIGHTNING_TAGS),
            promo_label=promo_type or None,
            seller_id=seller_id,
            catalog_id=item.get("catalog_product_id"),
            available=item.get("available_quantity", 1) != 0,
        )
        if seller_id and self.config.get("fetch_seller_reputation", True):
            self._attach_seller(offer)
        return offer

    def _attach_seller(self, offer: Offer):
        sid = offer.seller_id
        if sid not in self._seller_cache:
            try:
                self._seller_cache[sid] = self._get(f"/users/{sid}")
            except CollectorError:
                self._seller_cache[sid] = {}
        u = self._seller_cache[sid]
        rep = u.get("seller_reputation") or {}
        level = rep.get("level_id")
        power = rep.get("power_seller_status")
        parts = [REPUTATION_LABEL.get(level, level) if level else None,
                 f"MercadoLíder {power.capitalize()}" if power else None]
        offer.seller_name = u.get("nickname")
        offer.seller_reputation = " · ".join(p for p in parts if p) or None
        offer.seller_sales = (rep.get("transactions") or {}).get("completed")
