# Colocar o Promo Monitor no ar

O sistema roda num único processo: o dashboard e a coleta automática, com o banco
SQLite num disco persistente. Ele já vem pronto para Docker.

## Variáveis de ambiente

| Variável | Para que serve |
|---|---|
| `DASHBOARD_PASSWORD` | Senha do dashboard. Sem ela, o site fica aberto a qualquer pessoa. |
| `DASHBOARD_USER` | Usuário do dashboard (padrão: `admin`). |
| `PROMO_CONFIG_JSON` | Conteúdo completo do seu `config.json` (credenciais e lojas). |
| `PROMO_DB` | Caminho do banco (padrão no Docker: `/data/promo.db`). |
| `PORT` | Porta HTTP (o provedor normalmente define sozinho). |

Enquanto nenhuma loja estiver configurada, o servidor sobe com os dados de demonstração
(`--demo-if-empty`). Quando as coletas reais começarem, apague o banco de demonstração
para não misturar dados.

## Opção A — Render (mais simples)

1. Suba esta pasta para um repositório no GitHub.
2. No Render: **New > Blueprint** e selecione o repositório. O `render.yaml` cria o
   serviço, o disco de 1 GB e gera uma senha para o dashboard.
3. No painel do serviço, em **Environment**, cole o conteúdo do `config.json` em
   `PROMO_CONFIG_JSON`.
4. O endereço fica no formato `https://promo-monitor-xxxx.onrender.com`.

O disco persistente exige plano pago. Sem disco, o histórico de preços se perde a cada
reinício, o que inviabiliza a detecção de desconto real.

## Opção B — qualquer servidor com Docker (VPS)

```bash
docker build -t promo-monitor .
docker run -d --name promo --restart unless-stopped -p 80:8000 \
  -v promo-data:/data \
  -e DASHBOARD_PASSWORD='troque-esta-senha' \
  -e PROMO_CONFIG_JSON="$(cat config.json)" \
  promo-monitor
```

Use um proxy com HTTPS (Caddy, Nginx + Let's Encrypt, ou o do provedor). Sem HTTPS, a
senha do dashboard trafega sem criptografia.

## Sem Docker

```bash
export DASHBOARD_PASSWORD='troque-esta-senha' HOST=0.0.0.0 PORT=8000
python -m promo serve --collect
```

## Rotas úteis

- `/healthz`: verificação de saúde, sem login.
- O token do Mercado Livre é renovado sozinho. Com `PROMO_CONFIG_JSON`, o
  `refresh_token` novo fica só na memória. Se o processo reiniciar depois de o token antigo
  expirar, gere outro no portal do Mercado Livre e atualize a variável. Num VPS, você pode
  preferir montar o `config.json` como arquivo (`-v ./config.json:/app/config.json` e
  remover `PROMO_CONFIG_JSON`); assim o token renovado é gravado nele.
