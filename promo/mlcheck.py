"""Diagnóstico das rotas da API do Mercado Livre, a partir do ambiente onde a coleta roda.

Grava um JSON com o status de cada rota (sem tokens), para decidir quais usar.
Uso: python -m promo.mlcheck saida.json
Com ML_CLIENT_ID e ML_CLIENT_SECRET definidos, testa também com token de aplicativo.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.mercadolibre.com"
UA = "PromoMonitor/0.1 (diagnostico)"


def call(path: str, token: str | None = None, data: dict | None = None) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data else None
    if body:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(API + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return {"status": r.status, "body": json.loads(r.read().decode())}
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return {"status": e.code, "body": json.loads(txt)}
        except ValueError:
            return {"status": e.code, "body": txt[:300]}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "body": str(e)[:300]}


def trim(obj, depth=0):
    """Reduz o JSON para mostrar a estrutura sem ficar enorme."""
    if isinstance(obj, dict):
        return {k: trim(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [trim(v, depth + 1) for v in obj[:2]] + ([f"... +{len(obj) - 2}"] if len(obj) > 2 else [])
    if isinstance(obj, str) and len(obj) > 200:
        return obj[:200] + "..."
    return obj


def run_suite(token: str | None) -> dict:
    out = {}
    out["sites_search"] = call("/sites/MLB/search?q=fone%20bluetooth&limit=2", token)
    ps = call("/products/search?site_id=MLB&status=active&q=fone%20bluetooth&limit=3", token)
    out["products_search"] = ps
    pid = None
    if ps["status"] == 200 and isinstance(ps["body"], dict) and ps["body"].get("results"):
        pid = ps["body"]["results"][0].get("id")
    if pid:
        out["product"] = call(f"/products/{pid}", token)
        items = call(f"/products/{pid}/items?limit=3", token)
        out["product_items"] = items
        if items["status"] == 200 and items["body"].get("results"):
            first = items["body"]["results"][0]
            if first.get("seller_id"):
                out["user"] = call(f"/users/{first['seller_id']}", token)
            if first.get("item_id"):
                out["item"] = call(f"/items/{first['item_id']}", token)
    out["highlights"] = call("/highlights/MLB/category/MLB1000", token)
    return {k: {"status": v["status"], "body": trim(v["body"])} for k, v in out.items()}


def main(path: str):
    report = {"anonimo": run_suite(None)}
    cid, secret = os.environ.get("ML_CLIENT_ID"), os.environ.get("ML_CLIENT_SECRET")
    if cid and secret:
        tok = call("/oauth/token", data={"grant_type": "client_credentials",
                                         "client_id": cid, "client_secret": secret})
        report["token_status"] = tok["status"]
        if tok["status"] == 200:
            report["com_token"] = run_suite(tok["body"]["access_token"])
        else:
            report["token_erro"] = trim(tok["body"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: {kk: vv["status"] for kk, vv in v.items()} if isinstance(v, dict) and k != "token_erro" else v
                      for k, v in report.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ml_diag.json")
