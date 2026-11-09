"""Gera o site estático (GitHub Pages): um index.html com os dados já embutidos.

O dashboard roda inteiro no navegador (filtros, ranking, comparação, histórico, CSV),
então não precisa de servidor: o GitHub Actions regenera a página a cada coleta.
"""
import json
import os
from datetime import datetime, timezone

from .analysis import analyze_all
from .db import Database
from .stores import STORES

TEMPLATE = os.path.join(os.path.dirname(__file__), "static", "site.html")


def snapshot(db: Database) -> dict:
    items = analyze_all(db)
    hists = db.histories_since("0000")
    hist = {}
    for it in items:
        # um ponto por dia (último preço do dia), últimos 90 dias
        daily: dict[str, float] = {}
        for x in hists.get(it["listing_id"], []):
            daily[x["collected_at"][:10]] = round(x["price"], 2)
        days = sorted(daily)[-90:]
        if days:
            hist[it["listing_id"]] = [days[0], [daily[d] for d in days], days]
    demo = db.one("SELECT 1 AS x FROM listings WHERE external_id LIKE 'DEMO-%' LIMIT 1") is not None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "demo": demo,
        "stores": [{"id": s.id, "name": s.name, "source": s.source, "source_note": s.source_note,
                    "interval_minutes": s.interval_minutes} for s in STORES.values()],
        "items": items,
        "hist": hist,
    }


def build_site(db: Database, out_dir: str = "site") -> str:
    os.makedirs(out_dir, exist_ok=True)
    data = json.dumps(snapshot(db), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read().replace("__DATA__", data)
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    # evita que o GitHub Pages processe a pasta com Jekyll
    open(os.path.join(out_dir, ".nojekyll"), "w").close()
    return path
