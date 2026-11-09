"""Entrada da Vercel: todas as rotas são reescritas para cá (ver vercel.json).

Variáveis de ambiente usadas:
- DATABASE_URL (ou POSTGRES_URL): banco Postgres, ex.: Neon gratuito
- DASHBOARD_PASSWORD (e opcionalmente DASHBOARD_USER): login do dashboard
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promo.db import Database  # noqa: E402
from promo.web import make_handler  # noqa: E402

_db = None
_lock = threading.Lock()


def get_db() -> Database:
    """Abre a conexão na primeira consulta e a reaproveita entre requisições."""
    global _db
    with _lock:
        if _db is None:
            if not (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")):
                raise RuntimeError("configure DATABASE_URL nas variáveis de ambiente da Vercel")
            _db = Database()
        return _db


_pwd = os.environ.get("DASHBOARD_PASSWORD")
handler = make_handler(get_db, (os.environ.get("DASHBOARD_USER", "admin"), _pwd) if _pwd else None)
