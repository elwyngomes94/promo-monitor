"""Coletor do Mercado Livre via API oficial (https://api.mercadolibre.com), pelo catálogo.

Por que catálogo: a busca de anúncios (/sites/MLB/search) e a consulta de anúncios de
terceiros (/items/{id}) passaram a responder 403 para aplicativos comuns, mesmo com token.
As rotas de catálogo seguem disponíveis com o token de aplicativo:

- /products/search?site_id=MLB&q=...        produtos do catálogo (nome, fotos, atributos, EAN)
- /products/{id}/items                      ofertas de cada vendedor daquele produto
- /users/{id}                               vendedor e reputação (se a API permitir)

Requisitos atendidos: produtos e ofertas; comparação entre vendedores do mesmo produto;
frete grátis; preço "de" (original_price); promoções e ofertas relâmpago (tags e
sale_price, quando a API informa); vendedor e reputação; link direto; EAN para comparar
com as outras lojas. O histórico vem das coletas sucessivas.

Acesso: crie um aplicativo grátis em developers.mercadolivre.com.br e informe o
App ID (client_id) e a Secret Key (client_secret). O token é obtido sozinho
(grant_type=client_credentials) e renovado a cada execução.
"""
import json
import urllib.error
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

DEFAULT_QUERIES = [
    "fone bluetooth", "smartwatch", "air fryer", "tenis corrida", "perfume",
    "smart tv 50", "celular 128gb", "notebook", "aspirador robo", "cafeteira",
    "caixa de som bluetooth", "ssd 1tb",
]


def item_url(product_id: str, item_id: str) -> str:
    """Link direto para a oferta daquele vendedor na página do produto."""
    return f"https://www.mercadolivre.com.br/p/{product_id}?pdp_filters=item_id:{item_id}"


class MercadoLivreCollector(Collector):
    store_id = "mercadolivre"

    def __init__(self, config: dict, http=None, post=None):
        super().__init__(config)
        self.access_token: Optional[str] = self.config.get("access_token")
        self._seller_cache: dict[str, dict] = {}
        self._users_blocked = False
        if http is not None:          # injeção para testes
            self.http_get_json = http
        self._post = post or self._post_form
        self.stats = {"products": 0, "offers": 0, "errors": 0}

    # -------- autenticação --------
    def _post_form(self, path: str, data: dict) -> dict:
        req = urllib.request.Request(f"{API}{path}", data=urllib.parse.urlencode(data).encode(), headers={
            "User-Agent": USER_AGENT, "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"})
        self.limiter.wait()
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            raise CollectorError(f"Mercado Livre recusou as credenciais (HTTP {e.code}): {detail}") from e
        except Exception as e:  # noqa: BLE001
            raise CollectorError(f"Falha ao obter token do Mercado Livre: {e}") from e

    def authenticate(self):
        cid, secret = self.config.get("client_id"), self.config.get("client_secret")
        if not (cid and secret):
            raise CollectorError("Mercado Livre: informe client_id e client_secret do aplicativo")
        refresh = self.config.get("refresh_token")
        if refresh:   # token de usuário (opcional): renova e guarda o novo refresh_token
            payload = self._post("/oauth/token", {"grant_type": "refresh_token", "client_id": cid,
                                                  "client_secret": secret, "refresh_token": refresh})
            self.config["refresh_token"] = payload.get("refresh_token", refresh)
        else:         # token de aplicativo: basta App ID + Secret Key
            payload = self._post("/oauth/token", {"grant_type": "client_credentials",
                                                  "client_id": cid, "client_secret": secret})
        self.access_token = payload["access_token"]

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}
        return self.http_get_json(f"{API}{path}", params, headers)

    # -------- coleta --------
    def collect(self) -> Iterator[Offer]:
        if not self.access_token:
            self.authenticate()
        queries = self.config.get("queries") or DEFAULT_QUERIES
        per_query = int(self.config.get("products_per_query", 8))
        sellers_per_product = int(self.config.get("sellers_per_product", 5))
        seen_products: set[str] = set()

        for q in queries:
            try:
                page = self._get("/products/search", {"site_id": SITE, "status": "active",
                                                      "q": q, "limit": per_query})
            except CollectorError:
                self.stats["errors"] += 1
                continue
            for product in page.get("results", [])[:per_query]:
                pid = product.get("id")
                if not pid or pid in seen_products:
                    continue
                seen_products.add(pid)
                try:
                    data = self._get(f"/products/{pid}/items", {"limit": sellers_per_product})
                except CollectorError:
                    self.stats["errors"] += 1
                    continue
                items = data.get("results") or []
                if items:
                    self.stats["products"] += 1
                for item in items[:sellers_per_product]:
                    offer = self.parse_offer(product, item)
                    if offer:
                        self.stats["offers"] += 1
                        yield offer
        if not self.stats["offers"]:
            raise CollectorError("Mercado Livre: nenhuma oferta retornada "
                                 f"({self.stats['errors']} consultas com erro)")

    # -------- conversão --------
    def parse_offer(self, product: dict, item: dict) -> Optional[Offer]:
        item_id = item.get("item_id") or item.get("id")
        price = item.get("price")
        if not item_id or price is None:
            return None
        attrs = {a.get("id"): a.get("value_name") for a in product.get("attributes") or []}
        pictures = product.get("pictures") or []
        image = pictures[0].get("url") if pictures else None
        sale = item.get("sale_price") or {}
        promo_type = ((sale.get("metadata") or {}).get("promotion_type") or "").lower()
        list_price = item.get("original_price") or sale.get("regular_amount")
        tags = set(item.get("tags") or [])
        shipping = item.get("shipping") or {}
        seller_id = str(item.get("seller_id") or "") or None

        offer = Offer(
            store_id=self.store_id,
            external_id=str(item_id),
            title=product.get("name") or "",
            price=float(price),
            list_price=float(list_price) if list_price else None,
            url=item_url(product["id"], str(item_id)),
            gtin=attrs.get("GTIN"),
            brand=attrs.get("BRAND"),
            category=product.get("domain_id") or item.get("category_id"),
            image=(image or "").replace("http://", "https://") or None,
            free_shipping=bool(shipping.get("free_shipping")),
            is_lightning=promo_type in LIGHTNING_TYPES or bool(tags & LIGHTNING_TAGS),
            promo_label=promo_type or None,
            seller_id=seller_id,
            catalog_id=product["id"],
            available=item.get("available_quantity", 1) != 0,
        )
        if seller_id and self.config.get("fetch_seller_reputation", True):
            self._attach_seller(offer)
        return offer

    def _attach_seller(self, offer: Offer):
        sid = offer.seller_id
        if self._users_blocked:
            return
        if sid not in self._seller_cache:
            try:
                self._seller_cache[sid] = self._get(f"/users/{sid}")
            except CollectorError as e:
                self._seller_cache[sid] = {}
                if "HTTP 403" in str(e) or "HTTP 401" in str(e):
                    self._users_blocked = True   # API não libera: para de tentar nesta coleta
        u = self._seller_cache[sid]
        rep = u.get("seller_reputation") or {}
        level, power = rep.get("level_id"), rep.get("power_seller_status")
        parts = [REPUTATION_LABEL.get(level, level) if level else None,
                 f"MercadoLíder {power.capitalize()}" if power else None]
        offer.seller_name = u.get("nickname")
        offer.seller_reputation = " · ".join(p for p in parts if p) or None
        offer.seller_sales = (rep.get("transactions") or {}).get("completed")
