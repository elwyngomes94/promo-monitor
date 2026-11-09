"""Criptografia simétrica pequena, só com a biblioteca padrão.

Usada para guardar o refresh_token do Mercado Livre dentro do banco quando o banco fica
num repositório público. Construção: fluxo HMAC-SHA256 em modo contador (cifra) +
HMAC-SHA256 sobre nonce e texto cifrado (autenticação), com chaves separadas.
"""
import base64
import hashlib
import hmac
import os

PREFIX = "enc1:"


def _keys(secret: str) -> tuple[bytes, bytes]:
    master = hashlib.sha256(("promo-monitor:" + secret).encode()).digest()
    return (hmac.new(master, b"enc", "sha256").digest(),
            hmac.new(master, b"mac", "sha256").digest())


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(key, nonce + i.to_bytes(8, "big"), "sha256").digest()
        i += 1
    return out[:n]


def encrypt(secret: str, text: str) -> str:
    ek, mk = _keys(secret)
    nonce = os.urandom(16)
    data = text.encode()
    ct = bytes(a ^ b for a, b in zip(data, _stream(ek, nonce, len(data))))
    tag = hmac.new(mk, nonce + ct, "sha256").digest()
    return PREFIX + base64.urlsafe_b64encode(nonce + ct + tag).decode()


def decrypt(secret: str, token: str) -> str:
    if not token.startswith(PREFIX):
        raise ValueError("valor não criptografado")
    raw = base64.urlsafe_b64decode(token[len(PREFIX):])
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    ek, mk = _keys(secret)
    if not hmac.compare_digest(tag, hmac.new(mk, nonce + ct, "sha256").digest()):
        raise ValueError("chave incorreta ou valor adulterado")
    return bytes(a ^ b for a, b in zip(ct, _stream(ek, nonce, len(ct)))).decode()
