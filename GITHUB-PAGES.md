# Produção no GitHub (grátis, sem outras contas)

Tudo roda no próprio GitHub:

- **Coleta:** o workflow `Produção (coleta e site)` roda de hora em hora no GitHub Actions.
- **Banco de dados:** um arquivo SQLite guardado no ramo `dados`. Cada execução substitui o
  commit anterior, então o repositório não cresce. O histórico antigo é compactado para
  1 preço por dia, e o que passa de 180 dias é apagado.
- **Site:** o GitHub Pages publica o dashboard em
  `https://elwyngomes94.github.io/promo-monitor/`, regenerado a cada coleta.

## Ativar (uma vez)

1. **Tornar o repositório público.** O GitHub Pages grátis exige isso. Caminho:
   Settings > General > Danger Zone > Change repository visibility > Make public.
2. **Ativar o Pages.** Em Settings > Pages > Build and deployment > Source, escolha
   **GitHub Actions**.
3. **Publicar a primeira vez.** Em Actions > Produção (coleta e site) > Run workflow,
   clique em **Run workflow**. Em cerca de 2 minutos o site está no ar.

Enquanto as lojas não forem configuradas, o site mostra os dados de demonstração.

## Ligar os preços reais do Mercado Livre (grátis)

1. Entre em https://developers.mercadolivre.com.br com a sua conta do Mercado Livre,
   abra **Minhas aplicações > Criar nova aplicação** e preencha nome, descrição e uma
   URL de redirecionamento (pode ser `https://elwyngomes94.github.io/promo-monitor/`).
   Nos escopos, basta leitura.
2. Copie o **App ID** (Client ID) e a **Secret Key** (Client Secret) da aplicação criada.
3. No GitHub, em Settings > Secrets and variables > Actions > New repository secret, crie:
   - `ML_CLIENT_ID` com o App ID;
   - `ML_CLIENT_SECRET` com a Secret Key.
4. Em Actions > Produção (coleta e site) > Run workflow, rode com **coletar**.

Na primeira coleta real, a demonstração é removida sozinha. Os termos pesquisados vêm de
uma lista padrão (fone, smartwatch, air fryer, tênis, perfume, TV, celular, notebook...);
para trocar, use o secret `PROMO_CONFIG_JSON` com `"queries"` (veja `config.example.json`).

Por que pelo catálogo: a busca de anúncios da API (`/sites/MLB/search`) responde 403 para
aplicativos comuns. O sistema usa as rotas de catálogo (`/products/search` e
`/products/{id}/items`), que trazem o preço de cada vendedor do mesmo produto.

## Outras lojas

Amazon, Magalu, Shopee, SHEIN, TikTok Shop, Centauro, Netshoes e O Boticário entram pelos
programas de afiliados (cadastro grátis em cada um). Com o acesso aprovado, adicione a
loja no `PROMO_CONFIG_JSON` (formato em `config.example.json`).

## O que fica público e o que não fica

| Público | Protegido |
|---|---|
| Código, site e histórico de preços (ramo `dados`) | Secrets do GitHub (`PROMO_CONFIG_JSON`) |
| | Token do Mercado Livre: guardado no banco, mas criptografado com o seu `client_secret` |

Endereços de feeds com chaves de acesso não são gravados nos registros de erro.

## Observações

- **Atraso:** os horários do GitHub Actions são em UTC e podem atrasar alguns minutos.
- **Inatividade:** o GitHub pausa agendamentos de repositórios públicos sem atividade por
  60 dias. A própria coleta grava no ramo `dados` a cada hora. Se mesmo assim aparecer o
  aviso em Actions, clique em **Enable workflow**.
- **Sem senha:** o site é estático. Qualquer pessoa com o link vê as promoções. Para ter
  login, use a opção da Vercel (`VERCEL.md`) ou um servidor próprio (`DEPLOY.md`).
