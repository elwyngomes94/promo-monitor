"""Persistência: anúncios, histórico de preços, vendedores e execuções de coleta.

Dois bancos com o mesmo código:
- SQLite (arquivo local), para rodar no computador;
- Postgres (ex.: Neon gratuito), quando o caminho é uma URL postgres://...
  ou quando as variáveis DATABASE_URL / POSTGRES_URL estão definidas.
"""
import os
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional

from .models import Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_groups (
    id INTEGER PRIMARY KEY,
    match_key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    store_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    gtin TEXT, brand TEXT, category TEXT, image TEXT,
    catalog_id TEXT,
    seller_id TEXT,
    group_id INTEGER REFERENCES product_groups(id),
    available INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(store_id, external_id)
);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    collected_at TEXT NOT NULL,
    price REAL NOT NULL,
    list_price REAL,
    effective_price REAL NOT NULL,
    free_shipping INTEGER NOT NULL DEFAULT 0,
    shipping_cost REAL,
    coupon_value REAL NOT NULL DEFAULT 0,
    coupon_code TEXT,
    is_lightning INTEGER NOT NULL DEFAULT 0,
    promo_label TEXT
);
CREATE INDEX IF NOT EXISTS ix_hist_listing ON price_history(listing_id, collected_at);
CREATE INDEX IF NOT EXISTS ix_hist_time ON price_history(collected_at);
CREATE TABLE IF NOT EXISTS sellers (
    store_id TEXT NOT NULL,
    seller_id TEXT NOT NULL,
    name TEXT,
    reputation TEXT,
    sales INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (store_id, seller_id)
);
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    store_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    items INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);
"""

TABLES = ["product_groups", "listings", "price_history", "sellers", "collection_runs", "app_state"]
SERIAL_TABLES = ["product_groups", "listings", "price_history", "collection_runs"]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_target() -> str:
    return (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
            or os.environ.get("PROMO_DB") or "promo.db")


class Database:
    def __init__(self, target: Optional[str] = None):
        self.target = target or default_db_target()
        self.is_pg = self.target.startswith(("postgres://", "postgresql://"))
        if self.is_pg:
            from .pgwire import PgConnection
            self.pg = PgConnection(self.target)
            schema = (SCHEMA.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
                      .replace(" REAL", " DOUBLE PRECISION"))
            self.pg.query(schema)
        else:
            self.conn = sqlite3.connect(self.target, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA busy_timeout = 10000")
            if self.target != ":memory:":
                self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.executescript(SCHEMA)

    # ---------- acesso genérico ----------
    def q(self, sql: str, args: Iterable = ()) -> list[dict]:
        if self.is_pg:
            return self.pg.query(sql, list(args))
        return [dict(r) for r in self.conn.execute(sql, list(args)).fetchall()]

    def one(self, sql: str, args: Iterable = ()) -> Optional[dict]:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def insert(self, sql: str, args: Iterable = ()) -> int:
        """INSERT que devolve o id gerado."""
        if self.is_pg:
            return self.pg.query(sql + " RETURNING id", list(args))[0]["id"]
        return self.conn.execute(sql, list(args)).lastrowid

    def commit(self):
        if not self.is_pg:
            self.conn.commit()

    def rollback(self):
        if not self.is_pg:
            self.conn.rollback()

    def close(self):
        if self.is_pg:
            self.pg.close()
        else:
            self.conn.close()

    def is_empty(self) -> bool:
        return self.one("SELECT 1 AS x FROM listings LIMIT 1") is None

    # ---------- estado (ex.: refresh_token do Mercado Livre) ----------
    def get_state(self, key: str) -> Optional[str]:
        r = self.one("SELECT value FROM app_state WHERE key = ?", [key])
        return r["value"] if r else None

    def set_state(self, key: str, value: str):
        self.q("""INSERT INTO app_state(key, value, updated_at) VALUES (?, ?, ?)
                  ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                  updated_at = excluded.updated_at""", [key, value, now_iso()])
        self.commit()

    # ---------- grupos (mesmo produto em lojas diferentes) ----------
    def group_id_for(self, match_key: str, name: str) -> int:
        row = self.one("SELECT id FROM product_groups WHERE match_key = ?", [match_key])
        if row:
            return row["id"]
        return self.insert("INSERT INTO product_groups(match_key, name) VALUES (?, ?)", [match_key, name])

    # ---------- gravação de ofertas ----------
    def save_offer(self, offer: Offer, group_id: Optional[int] = None,
                   collected_at: Optional[str] = None) -> int:
        ts = collected_at or now_iso()
        row = self.one("SELECT id FROM listings WHERE store_id=? AND external_id=?",
                       [offer.store_id, offer.external_id])
        if row:
            listing_id = row["id"]
            self.q("""UPDATE listings SET title=?, url=?, gtin=COALESCE(?, gtin),
                      brand=COALESCE(?, brand), category=COALESCE(?, category),
                      image=COALESCE(?, image), catalog_id=COALESCE(?, catalog_id),
                      seller_id=COALESCE(?, seller_id),
                      group_id=COALESCE(?, group_id), available=?, last_seen=?
                      WHERE id=?""",
                   [offer.title, offer.url, offer.gtin, offer.brand, offer.category,
                    offer.image, offer.catalog_id, offer.seller_id, group_id,
                    int(offer.available), ts, listing_id])
        else:
            listing_id = self.insert(
                """INSERT INTO listings(store_id, external_id, title, url, gtin, brand, category,
                   image, catalog_id, seller_id, group_id, available, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [offer.store_id, offer.external_id, offer.title, offer.url, offer.gtin,
                 offer.brand, offer.category, offer.image, offer.catalog_id, offer.seller_id,
                 group_id, int(offer.available), ts, ts])

        self.q("""INSERT INTO price_history(listing_id, collected_at, price, list_price,
                  effective_price, free_shipping, shipping_cost, coupon_value, coupon_code,
                  is_lightning, promo_label) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               [listing_id, ts, offer.price, offer.list_price, offer.effective_price,
                int(offer.free_shipping), offer.shipping_cost, offer.coupon_value or 0,
                offer.coupon_code, int(offer.is_lightning), offer.promo_label])

        if offer.seller_id:
            self.q("""INSERT INTO sellers(store_id, seller_id, name, reputation, sales, updated_at)
                      VALUES (?,?,?,?,?,?)
                      ON CONFLICT(store_id, seller_id) DO UPDATE SET
                        name=COALESCE(excluded.name, sellers.name),
                        reputation=COALESCE(excluded.reputation, sellers.reputation),
                        sales=COALESCE(excluded.sales, sellers.sales),
                        updated_at=excluded.updated_at""",
                   [offer.store_id, offer.seller_id, offer.seller_name,
                    offer.seller_reputation, offer.seller_sales, ts])
        return listing_id

    # ---------- execuções ----------
    def start_run(self, store_id: str) -> int:
        rid = self.insert("INSERT INTO collection_runs(store_id, started_at, status) VALUES (?, ?, 'running')",
                          [store_id, now_iso()])
        self.commit()
        return rid

    def finish_run(self, run_id: int, status: str, items: int, error: Optional[str] = None):
        self.q("UPDATE collection_runs SET finished_at=?, status=?, items=?, error=? WHERE id=?",
               [now_iso(), status, items, error, run_id])
        self.commit()

    def last_runs(self) -> list[dict]:
        return self.q("""SELECT r.* FROM collection_runs r
            JOIN (SELECT store_id, MAX(id) AS mid FROM collection_runs GROUP BY store_id) m
              ON r.id = m.mid ORDER BY r.store_id""")

    # ---------- leitura ----------
    def history(self, listing_id: int, since: Optional[str] = None) -> list[dict]:
        sql, args = "SELECT * FROM price_history WHERE listing_id=?", [listing_id]
        if since:
            sql += " AND collected_at >= ?"
            args.append(since)
        return self.q(sql + " ORDER BY collected_at", args)

    def histories_since(self, since: str) -> dict[int, list[dict]]:
        """Histórico de todos os anúncios numa só consulta (evita uma ida ao banco por item)."""
        out: dict[int, list[dict]] = {}
        for r in self.q("""SELECT listing_id, collected_at, price FROM price_history
                           WHERE collected_at >= ? ORDER BY listing_id, collected_at""", [since]):
            out.setdefault(r["listing_id"], []).append(r)
        return out

    def current_listings(self, store_ids: Optional[Iterable[str]] = None) -> list[dict]:
        """Anúncios disponíveis com o último preço registrado e dados do vendedor."""
        sql = """SELECT l.*, h.price, h.list_price, h.effective_price, h.free_shipping,
                        h.shipping_cost, h.coupon_value, h.coupon_code, h.is_lightning,
                        h.promo_label, h.collected_at,
                        s.name AS seller_name, s.reputation AS seller_reputation,
                        s.sales AS seller_sales, g.name AS group_name
                 FROM listings l
                 JOIN price_history h ON h.id = (
                     SELECT id FROM price_history WHERE listing_id = l.id
                     ORDER BY collected_at DESC, id DESC LIMIT 1)
                 LEFT JOIN sellers s ON s.store_id = l.store_id AND s.seller_id = l.seller_id
                 LEFT JOIN product_groups g ON g.id = l.group_id
                 WHERE l.available = 1"""
        args: list = []
        if store_ids:
            ids = list(store_ids)
            sql += f" AND l.store_id IN ({','.join('?' * len(ids))})"
            args += ids
        return self.q(sql, args)

    def listing(self, listing_id: int) -> Optional[dict]:
        return self.one("SELECT * FROM listings WHERE id=?", [listing_id])

    def categories(self) -> list[str]:
        return [r["category"] for r in self.q(
            "SELECT DISTINCT category FROM listings WHERE category IS NOT NULL ORDER BY category")]

    # ---------- manutenção ----------
    def clear_demo(self) -> int:
        """Remove os dados de demonstração (anúncios DEMO-*)."""
        ids = "SELECT id FROM listings WHERE external_id LIKE 'DEMO-%'"
        n = len(self.q(ids))
        self.q(f"DELETE FROM price_history WHERE listing_id IN ({ids})")
        self.q("DELETE FROM listings WHERE external_id LIKE 'DEMO-%'")
        self.q("DELETE FROM sellers WHERE seller_id IN ('101', '102', '103')")
        self.q("DELETE FROM product_groups WHERE id NOT IN "
               "(SELECT DISTINCT group_id FROM listings WHERE group_id IS NOT NULL)")
        self.commit()
        return n

    def compact(self, keep_detail_days: int = 7, keep_days: int = 180) -> int:
        """Mantém o banco pequeno: histórico completo dos últimos dias, depois só o último
        preço de cada dia, e nada além de `keep_days`."""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=keep_days)).isoformat()
        detail = (now - timedelta(days=keep_detail_days)).isoformat()
        before = self.one("SELECT COUNT(*) AS n FROM price_history")["n"]
        self.q("DELETE FROM price_history WHERE collected_at < ?", [old])
        self.q("""DELETE FROM price_history WHERE collected_at < ? AND id NOT IN (
                    SELECT MAX(id) FROM price_history WHERE collected_at < ?
                    GROUP BY listing_id, substr(collected_at, 1, 10))""", [detail, detail])
        self.q("DELETE FROM collection_runs WHERE started_at < ?", [old])
        self.commit()
        if not self.is_pg:
            self.conn.execute("VACUUM")
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return before - self.one("SELECT COUNT(*) AS n FROM price_history")["n"]

    def copy_from(self, other: "Database", batch: int = 400) -> int:
        """Copia todas as tabelas de outro banco (ex.: demo gerada em memória -> Postgres)
        com inserções em lote, para ser rápido mesmo com o banco na nuvem."""
        total = 0
        for table in TABLES:
            rows = other.q(f"SELECT * FROM {table}")
            if not rows:
                continue
            cols = list(rows[0].keys())
            for i in range(0, len(rows), batch):
                chunk = rows[i:i + batch]
                ph = ",".join("(" + ",".join("?" * len(cols)) + ")" for _ in chunk)
                args = [r[c] for r in chunk for c in cols]
                self.q(f"INSERT INTO {table} ({','.join(cols)}) VALUES {ph} ON CONFLICT DO NOTHING", args)
            total += len(rows)
        if self.is_pg:
            for table in SERIAL_TABLES:
                self.q(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                       f"COALESCE((SELECT MAX(id) FROM {table}), 1))")
        self.commit()
        return total
