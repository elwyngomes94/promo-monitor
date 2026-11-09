"""Servidor do dashboard e da API JSON (somente biblioteca padrão do Python)."""
import base64
import csv
import hmac
import io
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .analysis import analyze_all, compare_groups, filter_offers, ranking, summary
from .db import Database
from .stores import STORES

STATIC = os.path.join(os.path.dirname(__file__), "static")

CSV_COLUMNS = [
    ("rank", "Posição"), ("store_name", "Loja"), ("title", "Produto"), ("category", "Categoria"),
    ("price", "Preço"), ("list_price", "Preço anunciado (de)"), ("coupon_code", "Cupom"),
    ("coupon_value", "Valor cupom"), ("free_shipping", "Frete grátis"),
    ("effective_price", "Preço final"), ("reference_price", "Preço de referência (90d)"),
    ("real_discount", "Desconto real"), ("announced_discount", "Desconto anunciado"),
    ("flags", "Sinais"), ("seller_name", "Vendedor"), ("seller_reputation", "Reputação"),
    ("cheaper_elsewhere", "Mais barato em"), ("score", "Pontuação"), ("url", "Link"),
]


def make_handler(db, auth: tuple[str, str] | None = None):
    """`db` pode ser um Database ou uma função que devolve um (conexão sob demanda,
    usada na Vercel)."""
    get_db = db if callable(db) else (lambda: db)
    expected = ("Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()) if auth else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silencioso
            pass

        def _send(self, body: bytes, ctype: str, status: int = 200, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=200):
            self._send(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

        def _all(self):
            if not hasattr(self, "_cache"):
                self._cache = analyze_all(get_db())
            return self._cache

        def _items(self, qs):
            stores = {s for s in (qs.get("store", [""])[0]).split(",") if s}
            items = [i for i in self._all() if not stores or i["store_id"] in stores]
            flag = lambda k: qs.get(k, ["0"])[0] in ("1", "true")  # noqa: E731
            mp = qs.get("max_price", [""])[0]
            return filter_offers(items, category=qs.get("category", [None])[0] or None,
                                 q=qs.get("q", [None])[0] or None, only_real=flag("only_real"),
                                 free_shipping=flag("free_shipping"), lightning=flag("lightning"),
                                 max_price=float(mp) if mp else None)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            path = u.path
            # na Vercel tudo é reescrito para /api/index?__p=<caminho original>
            if qs.get("__p"):
                path = qs.pop("__p")[0] or "/"
            elif path in ("/api", "/api/index", "/api/index.py"):
                path = "/"
            if path == "/healthz":
                return self._send(b"ok", "text/plain")
            if expected and not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                return self._send("Login necessário".encode(), "text/plain; charset=utf-8", 401,
                                  {"WWW-Authenticate": 'Basic realm="Promo Monitor", charset="UTF-8"'})
            try:
                db = get_db() if path.startswith("/api/") else None
                if path in ("/", "/index.html"):
                    with open(os.path.join(STATIC, "index.html"), "rb") as f:
                        return self._send(f.read(), "text/html; charset=utf-8")
                if path == "/api/stores":
                    runs = {r["store_id"]: r for r in db.last_runs()}
                    return self._json([{"id": s.id, "name": s.name, "source": s.source,
                                        "source_note": s.source_note,
                                        "interval_minutes": s.interval_minutes,
                                        "last_run": runs.get(s.id)} for s in STORES.values()])
                if path == "/api/categories":
                    return self._json(db.categories())
                if path == "/api/offers":
                    return self._json(self._items(qs))
                if path == "/api/ranking":
                    limit = int(qs.get("limit", ["50"])[0])
                    items = self._items(qs)
                    return self._json(ranking(items, self._all(), limit))
                if path == "/api/summary":
                    return self._json(summary(self._items(qs)))
                if path == "/api/compare":
                    groups = compare_groups(self._all())
                    if qs.get("group_id"):
                        g = groups.get(int(qs["group_id"][0]))
                        return self._json(g) if g else self._json({"error": "não encontrado"}, 404)
                    sel = {s for s in (qs.get("store", [""])[0]).split(",") if s}
                    out = [g for g in groups.values() if len(g["stores"]) > 1
                           and (not sel or sel & set(g["stores"]))]
                    for g in out:
                        g["offers"] = [o for o in g["offers"]][:12]
                    return self._json(sorted(out, key=lambda g: -g["spread"]))
                if path == "/api/history":
                    lid = int(qs["listing_id"][0])
                    return self._json({"listing": db.listing(lid), "history": db.history(lid)})
                if path == "/api/report.csv":
                    rows = ranking(self._items(qs), self._all(), int(qs.get("limit", ["500"])[0]))
                    return self._send(to_csv(rows).encode("utf-8-sig"), "text/csv; charset=utf-8",
                                      extra={"Content-Disposition": 'attachment; filename="promocoes.csv"'})
                self._json({"error": "rota não encontrada"}, 404)
            except (KeyError, ValueError) as e:
                self._json({"error": f"parâmetro inválido: {e}"}, 400)
            except Exception as e:  # noqa: BLE001 — ex.: banco fora do ar
                self._json({"error": f"falha ao consultar o banco: {e}"}, 500)

    return Handler


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([label for _, label in CSV_COLUMNS])
    for r in rows:
        line = []
        for key, _ in CSV_COLUMNS:
            v = r.get(key)
            if key == "flags":
                v = ", ".join(v or [])
            elif key == "cheaper_elsewhere":
                v = f'{v["store_name"]} (R$ {v["effective_price"]:.2f})' if v else ""
            elif key in ("real_discount", "announced_discount"):
                v = f"{v * 100:.1f}%".replace(".", ",") if v is not None else ""
            elif key == "free_shipping":
                v = "sim" if v else "não"
            elif isinstance(v, float):
                v = f"{v:.2f}".replace(".", ",")
            line.append("" if v is None else v)
        w.writerow(line)
    return buf.getvalue()


def serve(db: Database, host: str = "127.0.0.1", port: int = 8000,
          auth: tuple[str, str] | None = None):
    httpd = HTTPServer((host, port), make_handler(db, auth))
    if auth:
        print("Dashboard protegido por usuário e senha")
    print(f"Dashboard em http://{host}:{port}  (Ctrl+C para sair)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
