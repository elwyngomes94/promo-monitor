"""Persistência em SQLite: anúncios, histórico de preços, vendedores e execuções de coleta."""
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional

from .models import Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_groups (
    id INTEGER PRIMARY KEY,
    match_key TEXT UNIQUE NOT NULL,     -- gtin:<ean> ou name:<assinatura>
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
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: str = "promo.db"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 10000")
        if path != ":memory:":
            # permite o dashboard ler enquanto o coletor grava
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ---------- grupos (mesmo produto em lojas diferentes) ----------
    def group_id_for(self, match_key: str, name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM product_groups WHERE match_key = ?", (match_key,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO product_groups(match_key, name) VALUES (?, ?)", (match_key, name))
        return cur.lastrowid

    # ---------- gravação de ofertas ----------
    def save_offer(self, offer: Offer, group_id: Optional[int] = None,
                   collected_at: Optional[str] = None) -> int:
        ts = collected_at or now_iso()
        c = self.conn
        row = c.execute("SELECT id FROM listings WHERE store_id=? AND external_id=?",
                        (offer.store_id, offer.external_id)).fetchone()
        if row:
            listing_id = row["id"]
            c.execute("""UPDATE listings SET title=?, url=?, gtin=COALESCE(?, gtin),
                         brand=COALESCE(?, brand), category=COALESCE(?, category),
                         image=COALESCE(?, image), catalog_id=COALESCE(?, catalog_id),
                         seller_id=COALESCE(?, seller_id),
                         group_id=COALESCE(?, group_id), available=?, last_seen=?
                         WHERE id=?""",
                      (offer.title, offer.url, offer.gtin, offer.brand, offer.category,
                       offer.image, offer.catalog_id, offer.seller_id, group_id,
                       int(offer.available), ts, listing_id))
        else:
            cur = c.execute("""INSERT INTO listings(store_id, external_id, title, url, gtin,
                         brand, category, image, catalog_id, seller_id, group_id, available,
                         first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (offer.store_id, offer.external_id, offer.title, offer.url,
                             offer.gtin, offer.brand, offer.category, offer.image,
                             offer.catalog_id, offer.seller_id, group_id,
                             int(offer.available), ts, ts))
            listing_id = cur.lastrowid

        c.execute("""INSERT INTO price_history(listing_id, collected_at, price, list_price,
                     effective_price, free_shipping, shipping_cost, coupon_value, coupon_code,
                     is_lightning, promo_label) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (listing_id, ts, offer.price, offer.list_price, offer.effective_price,
                   int(offer.free_shipping), offer.shipping_cost, offer.coupon_value or 0,
                   offer.coupon_code, int(offer.is_lightning), offer.promo_label))

        if offer.seller_id:
            c.execute("""INSERT INTO sellers(store_id, seller_id, name, reputation, sales, updated_at)
                         VALUES (?,?,?,?,?,?)
                         ON CONFLICT(store_id, seller_id) DO UPDATE SET
                           name=COALESCE(excluded.name, name),
                           reputation=COALESCE(excluded.reputation, reputation),
                           sales=COALESCE(excluded.sales, sales),
                           updated_at=excluded.updated_at""",
                      (offer.store_id, offer.seller_id, offer.seller_name,
                       offer.seller_reputation, offer.seller_sales, ts))
        return listing_id

    def commit(self):
        self.conn.commit()

    # ---------- execuções ----------
    def start_run(self, store_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO collection_runs(store_id, started_at, status) VALUES (?,?, 'running')",
            (store_id, now_iso()))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, items: int, error: Optional[str] = None):
        self.conn.execute("""UPDATE collection_runs SET finished_at=?, status=?, items=?, error=?
                             WHERE id=?""", (now_iso(), status, items, error, run_id))
        self.conn.commit()

    def last_runs(self) -> list[dict]:
        rows = self.conn.execute("""SELECT r.* FROM collection_runs r
            JOIN (SELECT store_id, MAX(id) AS mid FROM collection_runs GROUP BY store_id) m
              ON r.id = m.mid ORDER BY r.store_id""").fetchall()
        return [dict(r) for r in rows]

    # ---------- leitura ----------
    def history(self, listing_id: int, since: Optional[str] = None) -> list[dict]:
        q = "SELECT * FROM price_history WHERE listing_id=?"
        args: list = [listing_id]
        if since:
            q += " AND collected_at >= ?"
            args.append(since)
        q += " ORDER BY collected_at"
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def current_listings(self, store_ids: Optional[Iterable[str]] = None) -> list[dict]:
        """Anúncios disponíveis com o último preço registrado e dados do vendedor."""
        q = """SELECT l.*, h.price, h.list_price, h.effective_price, h.free_shipping,
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
            q += f" AND l.store_id IN ({','.join('?' * len(ids))})"
            args += ids
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def listing(self, listing_id: int) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        return dict(r) if r else None
