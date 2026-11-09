# Promo Monitor

Monitoramento de promoções em 9 lojas brasileiras — Magazine Luiza, Amazon Brasil, Shopee,
SHEIN, TikTok Shop, Centauro, Netshoes, O Boticário e Mercado Livre — com histórico de
preços, detecção de desconto real × maquiado, comparação do mesmo produto entre lojas,
ranking das melhores ofertas, dashboard web e relatório em CSV.

Usa só a biblioteca padrão do Python 3.10+ (nenhuma dependência para instalar).

## Começo rápido (dados de demonstração)

```bash
python -m promo demo      # cria promo.db com 90 dias de preços FICTÍCIOS
python -m promo serve     # abre http://127.0.0.1:8000
```

## Usando com dados reais

1. `cp config.example.json config.json` e preencha as credenciais.
2. (opcional) `cp coupons.example.json coupons.json` para cupons cadastrados.
3. `python -m promo collect` — coleta uma vez.
4. `python -m promo run` — coleta contínua, respeitando a frequência de cada loja.
5. `python -m promo serve` — dashboard (em outro terminal).
6. `python -m promo report -o promocoes.csv [--store mercadolivre]` — relatório.

Comece com um banco novo (`--db promo.db` sem rodar `demo`) para não misturar dados fictícios.

## Colocar no ar

Veja [DEPLOY.md](DEPLOY.md): Docker, Render ou VPS, com login por senha no dashboard e coleta
automática no mesmo processo (`python -m promo serve --collect`).

## Imagens dos produtos

O dashboard mostra a imagem que vem da loja (miniatura da API do Mercado Livre ou campo
`image` do feed). Nos dados de demonstração, as imagens são ilustrações geradas por categoria.

## Fontes de dados (somente acesso autorizado)

| Loja | Fonte | Coletor |
|---|---|---|
| Mercado Livre | API oficial (developers.mercadolivre.com.br) — app + OAuth | `collectors/mercadolivre.py` |
| Amazon, Magalu, Shopee, SHEIN, TikTok Shop | Programas de afiliados da própria loja (API/feed) | `collectors/affiliate_feed.py` |
| Centauro, Netshoes, O Boticário | Feed de produtos da rede de afiliados que atende a loja | `collectors/affiliate_feed.py` |

O coletor de feeds é genérico: em `config.json` você indica a URL (ou arquivo) do feed,
o formato (CSV/JSON) e o mapeamento de campos. Não há raspagem de páginas: lojas sem
fonte autorizada ficam desabilitadas até que o acesso seja obtido. Os limites de consulta
são respeitados por `requests_per_second`, com nova tentativa automática em HTTP 429/5xx
honrando `Retry-After`.

Os endpoints, permissões e limites de cada plataforma mudam com o tempo — confira a
documentação oficial de cada programa antes de ir para produção.

### Mercado Livre — o que é coletado
- Busca por termos (`queries`) e categorias (`categories`).
- Ofertas relâmpago / do dia (tags e `sale_price.metadata.promotion_type`).
- Comparação entre vendedores do mesmo produto de catálogo (`/products/{id}/items`).
- Frete grátis, preço "de" (`original_price`), EAN (atributo GTIN), link direto.
- Vendedor e reputação (`/users/{id}`: nível e MercadoLíder).
- Cupons: a API pública não expõe cupons de forma geral; use `coupons.json`.
- O `refresh_token` renovado é salvo de volta no `config.json` automaticamente.

## Regras de análise (`promo/analysis.py`)

- **Preço final** = preço − cupom + frete (quando não é grátis).
- **Preço de referência** = mediana dos últimos 90 dias, ignorando os 3 dias mais recentes.
- **Desconto real**: queda ≥ 10% em relação à referência.
- **Desconto maquiado**: loja anuncia ≥ 15% ("de/por"), mas a queda real é < 5%.
- **Subiu antes**: nos 30 dias anteriores o preço ficou ≥ 20% acima da referência.
- **Menor preço em 90 dias**, **relâmpago**, **frete grátis**, **cupom**.
- **Pontuação do ranking**: desconto real + bônus (menor preço, relâmpago, frete, cupom,
  reputação do vendedor) − penalidades (maquiado, subiu antes). Ajuste em `AnalysisConfig`.

## Mesmo produto em lojas diferentes (`promo/matching.py`)

1. EAN/GTIN idêntico. 2. Assinatura do título (marca, números de modelo, palavras
relevantes, sem acentos e termos de marketing). Números de modelo diferentes nunca
casam ("Pulse 3" ≠ "Pulse 4", "128GB" ≠ "256GB").

## Dashboard e API

Filtro independente por loja (incluindo Mercado Livre), busca, categoria, só desconto real,
frete grátis, relâmpago e preço máximo. Abas: Melhores promoções, Comparar lojas e
Relatório por loja. Clique numa oferta para ver o histórico de preços e as outras lojas/vendedores.

| Rota | Descrição |
|---|---|
| `/api/ranking` | Ranking (parâmetros: `store`, `q`, `category`, `only_real`, `free_shipping`, `lightning`, `max_price`, `limit`) |
| `/api/offers` | Todas as ofertas analisadas (mesmos filtros) |
| `/api/compare[?group_id=]` | Mesmo produto entre lojas |
| `/api/history?listing_id=` | Histórico de preços |
| `/api/summary` | Totais por loja |
| `/api/stores` | Lojas, fonte de dados e status da última coleta |
| `/api/report.csv` | Relatório CSV (abre no Excel) |

## Estrutura

```
promo/
  stores.py        cadastro das 9 lojas e frequência de coleta
  models.py        formato comum de oferta
  db.py            SQLite: anúncios, histórico, vendedores, execuções
  collectors/      Mercado Livre (API oficial) e feeds de afiliados
  pipeline.py      coleta → cupons → casamento → banco
  matching.py      identificação do mesmo produto
  analysis.py      desconto real, comparação, ranking
  web.py           servidor do dashboard e API
  static/index.html
  demo.py          dados fictícios
tests/             python -m pytest
```
