"""Coletor genérico de feeds de produtos de programas de afiliados.

Serve para Magalu, Amazon, Shopee, SHEIN, TikTok Shop, Centauro, Netshoes e O Boticário:
cada programa entrega o catálogo por API ou arquivo (CSV/JSON) com nomes de campos
diferentes. Em vez de um coletor por loja, o `config.json` diz de onde ler o feed e
como mapear os campos dele para o formato comum (Offer).

Exemplo de configuração:
    "netshoes": {
        "enabled": true,
        "feed_url": "https://<rede-de-afiliados>/feed.csv?token=SEU_TOKEN",
        "format": "csv",
        "fields": {"external_id": "sku", "title": "nome", "price": "preco_por",
                   "list_price": "preco_de", "url": "link_afiliado", "gtin": "ean"}
    }
"""
import csv
import io
import json
import urllib.request
from typing import Iterator, Optional

from ..models import Offer
from .base import USER_AGENT, Collector, CollectorError

DEFAULT_FIELDS = {
    "external_id": "id", "title": "title", "price": "price", "list_price": "list_price",
    "url": "url", "gtin": "gtin", "brand": "brand", "category": "category",
    "image": "image", "free_shipping": "free_shipping", "shipping_cost": "shipping_cost",
    "coupon_value": "coupon_value", "coupon_code": "coupon_code",
    "is_lightning": "is_lightning", "promo_label": "promo_label",
    "seller_id": "seller_id", "seller_name": "seller_name",
    "seller_reputation": "seller_reputation", "seller_sales": "seller_sales",
    "available": "available",
}


def parse_money(v) -> Optional[float]:
    """Aceita 1299.9, '1299.90', '1.299,90', 'R$ 1.299,90'."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("R$", "").replace("\xa0", "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "sim", "yes", "s", "y"}


def dig(obj, path: str):
    """Acessa caminhos tipo 'data.items' em JSON aninhado."""
    for part in path.split(".") if path else []:
        obj = obj.get(part, []) if isinstance(obj, dict) else []
    return obj


class AffiliateFeedCollector(Collector):
    def __init__(self, store_id: str, config: dict):
        super().__init__(config)
        self.store_id = store_id
        self.fields = {**DEFAULT_FIELDS, **self.config.get("fields", {})}

    def _read_raw(self) -> str:
        if self.config.get("feed_file"):
            with open(self.config["feed_file"], encoding="utf-8-sig") as f:
                return f.read()
        url = self.config.get("feed_url")
        if not url:
            raise CollectorError(f"{self.store_id}: configure feed_url ou feed_file")
        headers = {"User-Agent": USER_AGENT, **self.config.get("headers", {})}
        self.limiter.wait()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=60) as resp:
                return resp.read().decode("utf-8-sig")
        except Exception as e:  # noqa: BLE001
            raise CollectorError(f"{self.store_id}: falha ao baixar feed: {e}") from e

    def rows(self) -> list[dict]:
        raw = self._read_raw()
        fmt = self.config.get("format", "csv").lower()
        if fmt == "json":
            data = json.loads(raw)
            return dig(data, self.config.get("json_path", "")) if self.config.get("json_path") else data
        delimiter = self.config.get("delimiter") or (";" if raw[:2000].count(";") > raw[:2000].count(",") else ",")
        return list(csv.DictReader(io.StringIO(raw), delimiter=delimiter))

    def collect(self) -> Iterator[Offer]:
        f = self.fields
        for row in self.rows():
            g = lambda k: row.get(f[k]) if f.get(k) else None  # noqa: E731
            price = parse_money(g("price"))
            if price is None or not g("external_id") or not g("url"):
                continue
            yield Offer(
                store_id=self.store_id,
                external_id=str(g("external_id")),
                title=str(g("title") or ""),
                price=price,
                list_price=parse_money(g("list_price")),
                url=str(g("url")),
                gtin=(str(g("gtin")).strip() or None) if g("gtin") else None,
                brand=g("brand") or None,
                category=g("category") or None,
                image=g("image") or None,
                free_shipping=parse_bool(g("free_shipping")) if g("free_shipping") is not None else False,
                shipping_cost=parse_money(g("shipping_cost")),
                coupon_value=parse_money(g("coupon_value")) or 0.0,
                coupon_code=g("coupon_code") or None,
                is_lightning=parse_bool(g("is_lightning")) if g("is_lightning") is not None else False,
                promo_label=g("promo_label") or None,
                seller_id=str(g("seller_id")) if g("seller_id") else None,
                seller_name=g("seller_name") or None,
                seller_reputation=g("seller_reputation") or None,
                seller_sales=int(parse_money(g("seller_sales")) or 0) or None,
                available=parse_bool(g("available")) if g("available") not in (None, "") else True,
            )
