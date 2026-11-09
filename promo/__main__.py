"""Linha de comando.

    python -m promo demo                 # cria banco com dados fictícios
    python -m promo collect [--store ml] # coleta agora (lojas habilitadas no config.json)
    python -m promo run                  # coleta contínua, respeitando a frequência de cada loja
    python -m promo serve                # abre o dashboard em http://127.0.0.1:8000
    python -m promo report -o promo.csv  # exporta o ranking em CSV
    python -m promo stores               # lista lojas e fontes de dados
"""
import argparse
import json
import logging
import os
import sys

from .analysis import analyze_all, ranking
from .db import Database, default_db_target
from .pipeline import run_all
from .stores import STORES
from .web import serve, to_csv


def load_config(path: str) -> dict:
    # em servidores, a configuração pode vir inteira numa variável de ambiente
    if os.environ.get("PROMO_CONFIG_JSON"):
        return json.loads(os.environ["PROMO_CONFIG_JSON"])
    if not os.path.exists(path):
        return {"stores": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(path: str, cfg: dict):
    """Guarda o refresh_token renovado do Mercado Livre."""
    if os.path.exists(path) and not os.environ.get("PROMO_CONFIG_JSON"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)


def main(argv=None):
    p = argparse.ArgumentParser(prog="promo", description="Monitor de promoções")
    p.add_argument("--db", default=None,
                   help="arquivo SQLite ou URL postgres:// (padrão: DATABASE_URL, POSTGRES_URL, "
                        "PROMO_DB ou promo.db)")
    p.add_argument("--config", default=os.environ.get("PROMO_CONFIG", "config.json"))
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="popular o banco com dados de demonstração")
    d.add_argument("--days", type=int, default=90)
    c = sub.add_parser("collect", help="coletar agora")
    c.add_argument("--store", action="append", choices=list(STORES))
    sub.add_parser("run", help="coleta contínua (deixe 'serve' rodando em outro terminal)")
    s = sub.add_parser("serve", help="abrir o dashboard")
    s.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    s.add_argument("--collect", action="store_true",
                   help="rodar a coleta contínua junto com o dashboard (uso em servidor)")
    s.add_argument("--demo-if-empty", action="store_true",
                   help="carregar dados de demonstração se o banco estiver vazio")
    rep = sub.add_parser("report", help="exportar ranking em CSV")
    rep.add_argument("-o", "--output", default="promocoes.csv")
    rep.add_argument("--store", action="append", choices=list(STORES))
    rep.add_argument("--limit", type=int, default=200)
    sub.add_parser("stores", help="listar lojas")
    sub.add_parser("clear-demo", help="apagar os dados de demonstração")

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if a.cmd == "stores":
        for st in STORES.values():
            print(f"{st.id:13} {st.name:16} {st.source:15} a cada {st.interval_minutes:>3} min — {st.source_note}")
        return 0

    a.db = a.db or default_db_target()
    db = Database(a.db)
    cfg = load_config(a.config)

    if a.cmd == "demo":
        from .demo import load_demo
        n = load_demo(db, a.days)
        print(f"{n} registros de demonstração gravados")
    elif a.cmd == "clear-demo":
        print(f"{db.clear_demo()} anúncios de demonstração removidos")
    elif a.cmd == "collect":
        res = run_all(db, cfg, a.store)
        save_config(a.config, cfg)
        if not res:
            print("Nenhuma loja habilitada. Copie config.example.json para config.json e ajuste.")
        for sid, n in res.items():
            print(f"{STORES[sid].name}: {n} ofertas")
    elif a.cmd == "run":
        from .pipeline import scheduler_loop
        print("Coleta contínua iniciada (Ctrl+C para parar)")
        try:
            scheduler_loop(a.db, cfg, on_saved=lambda c: save_config(a.config, c))
        except KeyboardInterrupt:
            pass
    elif a.cmd == "serve":
        if a.demo_if_empty and db.is_empty():
            from .demo import load_demo
            load_demo(db)
            print("Banco vazio: dados de demonstração carregados")
        if a.collect:
            import threading
            from .pipeline import scheduler_loop
            threading.Thread(target=scheduler_loop, args=(a.db, cfg),
                             kwargs={"on_saved": lambda c: save_config(a.config, c)},
                             daemon=True).start()
            print("Coleta contínua ativada")
        user, pwd = os.environ.get("DASHBOARD_USER", "admin"), os.environ.get("DASHBOARD_PASSWORD")
        serve(db, a.host, a.port, (user, pwd) if pwd else None)
    elif a.cmd == "report":
        rows = ranking(analyze_all(db, a.store), analyze_all(db), a.limit)
        with open(a.output, "w", encoding="utf-8-sig", newline="") as f:
            f.write(to_csv(rows))
        print(f"{len(rows)} ofertas exportadas para {a.output}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
