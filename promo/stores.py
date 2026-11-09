"""Cadastro das lojas monitoradas.

Cada loja aparece como opção independente em filtros, relatórios, dashboard e ranking.
O campo `source` documenta a fonte de dados autorizada usada para a loja
(requisito: priorizar APIs oficiais e recursos autorizados).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Store:
    id: str
    name: str
    source: str          # "official_api" | "affiliate_feed"
    source_note: str     # onde obter acesso / credenciais
    interval_minutes: int  # frequência padrão de coleta
    multi_seller: bool   # marketplace com vários vendedores por produto


STORES: dict[str, Store] = {s.id: s for s in [
    Store("magalu", "Magazine Luiza", "affiliate_feed",
          "Programa de afiliados Parceiro Magalu (feed/API de produtos)", 60, True),
    Store("amazon", "Amazon Brasil", "affiliate_feed",
          "Programa de Associados Amazon (API de produtos para afiliados)", 60, True),
    Store("shopee", "Shopee", "affiliate_feed",
          "Programa de Afiliados Shopee (API de ofertas para afiliados)", 60, True),
    Store("shein", "SHEIN", "affiliate_feed",
          "Programa de afiliados SHEIN (feed de produtos)", 120, False),
    Store("tiktokshop", "TikTok Shop", "affiliate_feed",
          "TikTok Shop Affiliate (API/feed de produtos para afiliados)", 120, True),
    Store("centauro", "Centauro", "affiliate_feed",
          "Rede de afiliados que atende a loja (verificar qual)", 120, False),
    Store("netshoes", "Netshoes", "affiliate_feed",
          "Rede de afiliados que atende a loja (verificar qual)", 120, False),
    Store("boticario", "O Boticário", "affiliate_feed",
          "Rede de afiliados que atende a loja (verificar qual)", 180, False),
    Store("mercadolivre", "Mercado Livre", "official_api",
          "API oficial Mercado Livre (developers.mercadolivre.com.br) — app + OAuth", 30, True),
]}


def store_name(store_id: str) -> str:
    s = STORES.get(store_id)
    return s.name if s else store_id
