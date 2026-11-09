"""Cliente Postgres mínimo, só com a biblioteca padrão (protocolo v3).

Suporta TLS (SSLRequest + SNI, exigido pelo Neon), autenticação SCRAM-SHA-256, MD5 e
senha simples, e o protocolo de consulta simples. Os parâmetros são inseridos como
literais escapados no próprio cliente. Assim o projeto roda na Vercel e no GitHub Actions
sem instalar nenhum pacote.
"""
import base64
import hashlib
import hmac
import os
import socket
import ssl
import struct
import threading
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

BOOL, INTS, FLOATS = {16}, {20, 21, 23, 26}, {700, 701, 1700}


class PgError(Exception):
    def __init__(self, fields: dict):
        self.fields = fields
        super().__init__(f'{fields.get("S", "ERROR")} {fields.get("C", "")}: {fields.get("M", "")}')


def literal(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("número inválido para o banco")
        return repr(v)
    s = str(v).replace("\x00", "")
    return "'" + s.replace("'", "''") + "'"


def bind(sql: str, args) -> str:
    """Troca cada `?` (fora de literais) pelo valor escapado."""
    if not args:
        return sql
    out, it, in_str = [], iter(args), False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "?" and not in_str:
            out.append(literal(next(it)))
        else:
            out.append(ch)
    return "".join(out)


class PgConnection:
    def __init__(self, url: str):
        self.url = url
        self.lock = threading.RLock()
        self.sock = None
        self._connect()

    # ---------- conexão ----------
    def _connect(self):
        u = urlparse(self.url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.host, port = u.hostname, u.port or 5432
        user, password = unquote(u.username or ""), unquote(u.password or "")
        database = (u.path or "/").lstrip("/") or user
        sslmode = q.get("sslmode", "prefer" if self.host in ("localhost", "127.0.0.1") else "require")

        sock = socket.create_connection((self.host, port), timeout=30)
        if sslmode != "disable":
            sock.sendall(struct.pack("!ii", 8, 80877103))
            if sock.recv(1) == b"S":
                ctx = ssl.create_default_context(cafile=os.environ.get("PGSSLROOTCERT"))
                if os.environ.get("PGSSL_NO_VERIFY") == "1":
                    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=self.host)  # SNI
            elif sslmode in ("require", "verify-ca", "verify-full"):
                raise PgError({"M": "o servidor não aceita conexão segura (TLS)"})
        self.sock, self.buf = sock, b""

        params = {"user": user, "database": database, "client_encoding": "UTF8",
                  "application_name": "promo-monitor"}
        if q.get("options"):
            params["options"] = q["options"]
        body = struct.pack("!i", 196608) + b"".join(
            k.encode() + b"\0" + v.encode() + b"\0" for k, v in params.items()) + b"\0"
        self.sock.sendall(struct.pack("!i", len(body) + 4) + body)
        self._auth(user, password)

    def _auth(self, user: str, password: str):
        scram = None
        while True:
            t, data = self._read()
            if t == b"R":
                code = struct.unpack("!i", data[:4])[0]
                if code == 0:
                    continue
                if code == 3:
                    self._send(b"p", password.encode() + b"\0")
                elif code == 5:
                    salt = data[4:8]
                    inner = hashlib.md5(password.encode() + user.encode()).hexdigest().encode()
                    self._send(b"p", b"md5" + hashlib.md5(inner + salt).hexdigest().encode() + b"\0")
                elif code == 10:
                    mechs = data[4:].split(b"\0")
                    if b"SCRAM-SHA-256" not in mechs:
                        raise PgError({"M": f"autenticação não suportada: {mechs}"})
                    scram = _Scram(password)
                    first = scram.client_first()
                    self._send(b"p", b"SCRAM-SHA-256\0" + struct.pack("!i", len(first)) + first)
                elif code == 11:
                    self._send(b"p", scram.client_final(data[4:]))
                elif code == 12:
                    scram.verify(data[4:])
                else:
                    raise PgError({"M": f"método de autenticação {code} não suportado"})
            elif t == b"E":
                raise PgError(_fields(data))
            elif t == b"Z":
                return
            # S (parâmetros), K (chave de cancelamento), N (avisos): ignorados

    def close(self):
        try:
            if self.sock:
                self._send(b"X", b"")
                self.sock.close()
        except OSError:
            pass
        self.sock = None

    # ---------- E/S ----------
    def _send(self, t: bytes, body: bytes):
        self.sock.sendall(t + struct.pack("!i", len(body) + 4) + body)

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("conexão com o banco encerrada")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read(self) -> tuple[bytes, bytes]:
        head = self._recv_exact(5)
        length = struct.unpack("!i", head[1:])[0]
        return head[:1], self._recv_exact(length - 4)

    # ---------- consultas ----------
    def query(self, sql: str, args=None) -> list[dict]:
        with self.lock:
            try:
                return self._query(bind(sql, args))
            except (ConnectionError, OSError, ssl.SSLError):
                # conexão caiu (ex.: o Neon suspendeu o banco por inatividade): reconecta 1 vez
                self.close()
                self._connect()
                return self._query(bind(sql, args))

    def _query(self, sql: str) -> list[dict]:
        self._send(b"Q", sql.encode() + b"\0")
        cols, rows, result, error = [], [], [], None
        while True:
            t, data = self._read()
            if t == b"T":
                n = struct.unpack("!h", data[:2])[0]
                pos, cols = 2, []
                for _ in range(n):
                    end = data.index(b"\0", pos)
                    name = data[pos:end].decode()
                    oid = struct.unpack("!i", data[end + 7:end + 11])[0]
                    cols.append((name, oid))
                    pos = end + 19
                rows = []
            elif t == b"D":
                n = struct.unpack("!h", data[:2])[0]
                pos, row = 2, {}
                for i in range(n):
                    ln = struct.unpack("!i", data[pos:pos + 4])[0]
                    pos += 4
                    name, oid = cols[i]
                    if ln == -1:
                        row[name] = None
                    else:
                        row[name] = _convert(data[pos:pos + ln].decode(), oid)
                        pos += ln
                rows.append(row)
            elif t == b"C":
                result = rows
            elif t == b"E":
                error = PgError(_fields(data))
            elif t == b"Z":
                if error:
                    raise error
                return result
            # I (vazio), N (aviso), S (parâmetro): ignorados


def _convert(s: str, oid: int):
    if oid in BOOL:
        return s == "t"
    if oid in INTS:
        return int(s)
    if oid in FLOATS:
        return float(s)
    return s


def _fields(data: bytes) -> dict:
    out = {}
    for part in data.split(b"\0"):
        if part:
            out[chr(part[0])] = part[1:].decode(errors="replace")
    return out


class _Scram:
    """SCRAM-SHA-256 (RFC 5802/7677) sem channel binding."""

    def __init__(self, password: str):
        self.password = password.encode()
        self.nonce = base64.b64encode(os.urandom(18)).decode()

    def client_first(self) -> bytes:
        self.first_bare = f"n=,r={self.nonce}"
        return ("n,," + self.first_bare).encode()

    def client_final(self, server_first: bytes) -> bytes:
        sf = server_first.decode()
        attrs = dict(p.split("=", 1) for p in sf.split(","))
        if not attrs["r"].startswith(self.nonce):
            raise PgError({"M": "resposta SCRAM inválida do servidor"})
        salted = hashlib.pbkdf2_hmac("sha256", self.password, base64.b64decode(attrs["s"]), int(attrs["i"]))
        client_key = hmac.new(salted, b"Client Key", "sha256").digest()
        stored_key = hashlib.sha256(client_key).digest()
        final_wo_proof = f"c=biws,r={attrs['r']}"
        self.auth_msg = f"{self.first_bare},{sf},{final_wo_proof}".encode()
        sig = hmac.new(stored_key, self.auth_msg, "sha256").digest()
        proof = bytes(a ^ b for a, b in zip(client_key, sig))
        self.server_key = hmac.new(salted, b"Server Key", "sha256").digest()
        return f"{final_wo_proof},p={base64.b64encode(proof).decode()}".encode()

    def verify(self, server_final: bytes):
        attrs = dict(p.split("=", 1) for p in server_final.decode().split(","))
        expected = hmac.new(self.server_key, self.auth_msg, "sha256").digest()
        if base64.b64decode(attrs.get("v", "")) != expected:
            raise PgError({"M": "assinatura SCRAM do servidor não confere"})


def connect(url: Optional[str] = None) -> PgConnection:
    return PgConnection(url or os.environ["DATABASE_URL"])
