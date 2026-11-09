# Publicar grátis: Vercel + Neon + GitHub Actions

| Parte | Serviço gratuito | Papel |
|---|---|---|
| Dashboard | Vercel (plano Hobby) | Mostra ranking, comparação, histórico e relatório |
| Banco de dados | Neon (Postgres, plano Free) | Guarda anúncios e histórico de preços (1 GB) |
| Coleta automática | GitHub Actions | Busca os preços de hora em hora e grava no Neon |

A coleta não roda na Vercel porque, no plano gratuito, o agendamento de lá só pode rodar
uma vez por dia. O GitHub Actions roda de hora em hora sem custo: num repositório
privado são 2.000 minutos grátis por mês, e a coleta usa cerca de 1 minuto por execução.

O projeto não tem dependências externas, então não há nada para instalar em nenhum dos três.

## 1. Criar o banco no Neon

1. Crie uma conta em neon.com (dá para entrar com o GitHub) e um projeto
   (região sugerida: *AWS US East (N. Virginia)*, a mesma da Vercel).
2. No painel do projeto, clique em **Connect** e copie a *connection string*. Ela começa
   com `postgresql://` e termina com `?sslmode=require`. Esse é o seu `DATABASE_URL`.

As tabelas são criadas sozinhas no primeiro acesso.

## 2. Configurar o GitHub Actions (coleta)

No repositório: **Settings > Secrets and variables > Actions > New repository secret**.

| Secret | Valor |
|---|---|
| `DATABASE_URL` | A connection string do Neon |
| `PROMO_CONFIG_JSON` | Conteúdo do seu `config.json` (pode ficar para depois) |

Para ver o sistema funcionando antes de configurar as lojas: **Actions > Coleta de
preços > Run workflow**, escolha `carregar-demo` e confirme. Quando as coletas reais
começarem, rode de novo escolhendo `apagar-demo`.

A coleta de hora em hora começa sozinha. Enquanto `PROMO_CONFIG_JSON` não existir, ela
apenas termina sem fazer nada.

## 3. Publicar na Vercel

1. Entre em vercel.com com a conta do GitHub, clique em **Add New > Project** e importe
   o repositório `promo-monitor`. Não precisa mudar nenhuma configuração de build.
2. Antes de clicar em **Deploy**, abra **Environment Variables** e adicione:

| Variável | Valor |
|---|---|
| `DATABASE_URL` | A mesma connection string do Neon |
| `DASHBOARD_PASSWORD` | Uma senha para entrar no dashboard (o usuário é `admin`) |

3. Clique em **Deploy**. O endereço fica no formato `https://promo-monitor-xxxx.vercel.app`.

Mudou alguma variável depois? Vá em **Deployments**, abra o menu do último deploy e
clique em **Redeploy** para ela valer.

## Observações

- **Neon:** o banco gratuito "dorme" após 5 minutos sem uso. O primeiro acesso depois
  disso leva alguns segundos a mais.
- **Mercado Livre:** o `refresh_token` muda a cada renovação. O sistema guarda o mais
  recente no próprio banco, então você só precisa colocá-lo uma vez no `PROMO_CONFIG_JSON`.
- **Horário:** o agendamento do GitHub Actions usa UTC e pode atrasar alguns minutos em
  horários de pico.
- **Rodar localmente contra o Neon:** `DATABASE_URL='postgresql://...' python -m promo serve`.
