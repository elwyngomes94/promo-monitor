"""Estrutura comum de uma oferta coletada, independente da loja."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Offer:
    store_id: str
    external_id: str              # id do anúncio/produto na loja
    title: str
    price: float                  # preço atual (à vista) em R$
    url: str                      # link direto para compra
    list_price: Optional[float] = None   # preço "de" exibido pela loja
    gtin: Optional[str] = None           # EAN/GTIN, quando disponível
    brand: Optional[str] = None
    category: Optional[str] = None
    image: Optional[str] = None
    free_shipping: bool = False
    shipping_cost: Optional[float] = None  # None = desconhecido
    coupon_value: float = 0.0              # desconto de cupom aplicável (R$)
    coupon_code: Optional[str] = None
    is_lightning: bool = False             # oferta relâmpago / do dia
    promo_label: Optional[str] = None      # rótulo da promoção especial
    seller_id: Optional[str] = None
    seller_name: Optional[str] = None
    seller_reputation: Optional[str] = None   # ex.: "5_green", "MercadoLíder Platinum"
    seller_sales: Optional[int] = None
    catalog_id: Optional[str] = None       # id de catálogo (mesmo produto, vários vendedores)
    available: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def effective_price(self) -> float:
        """Preço final considerado: preço - cupom + frete (se não for grátis e conhecido)."""
        total = self.price - (self.coupon_value or 0)
        if not self.free_shipping and self.shipping_cost:
            total += self.shipping_cost
        return round(max(total, 0.0), 2)
