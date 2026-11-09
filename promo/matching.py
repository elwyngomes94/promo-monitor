"""Identifica o mesmo produto em lojas diferentes.

1º critério: GTIN/EAN (exato).
2º critério: assinatura do título — marca + números de modelo + palavras relevantes,
   normalizadas (sem acento, sem palavras de marketing, cores e unidades padronizadas).
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

STOPWORDS = {
    "de", "da", "do", "das", "dos", "e", "com", "para", "p", "c", "a", "o", "em", "na", "no",
    "original", "oferta", "promocao", "novo", "nova", "lacrado", "envio", "imediato",
    "frete", "gratis", "kit", "unidade", "un", "pronta", "entrega", "oficial", "loja",
    "garantia", "nf", "nota", "fiscal", "lancamento", "barato", "top", "premium",
}
UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|l|g|kg|gb|tb|mah|w|pol|polegadas|\")\b")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = UNIT_RE.sub(lambda m: m.group(1).replace(",", ".") + m.group(2).replace("polegadas", "pol").replace('"', "pol"), text)
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]


def signature(title: str, brand: Optional[str] = None) -> str:
    toks = tokens(title)
    if brand:
        b = normalize(brand)
        toks = [b] + [t for t in toks if t != b]
    # ordenado para que a ordem das palavras no título não importe
    return " ".join(sorted(set(toks)))


def similarity(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    # números de modelo precisam bater: "s23" != "s24", "128gb" != "256gb"
    na = {t for t in ta if any(ch.isdigit() for ch in t)}
    nb = {t for t in tb if any(ch.isdigit() for ch in t)}
    if na and nb and na != nb:
        return 0.0
    return max(jaccard, SequenceMatcher(None, a, b).ratio() * 0.9)


class Matcher:
    """Mantém as assinaturas conhecidas e devolve a chave de grupo de cada oferta."""

    def __init__(self, threshold: float = 0.72):
        self.threshold = threshold
        self.known: dict[str, str] = {}       # assinatura -> match_key

    def load(self, rows: list[tuple[str, str]]):
        """rows: (match_key, nome) dos grupos já existentes no banco."""
        for key, name in rows:
            if key.startswith("name:"):
                self.known[key[5:]] = key
            else:
                self.known.setdefault(signature(name), key)

    def key_for(self, title: str, brand: Optional[str], gtin: Optional[str]) -> str:
        sig = signature(title, brand)
        if gtin and re.fullmatch(r"\d{8,14}", gtin.strip()):
            key = "gtin:" + gtin.strip().lstrip("0")
            # registra o título para que anúncios sem EAN do mesmo produto caiam no grupo
            self.known.setdefault(sig, key)
            return key
        if sig in self.known:
            return self.known[sig]
        best, best_score = None, 0.0
        for known_sig, key in self.known.items():
            s = similarity(sig, known_sig)
            if s > best_score:
                best, best_score = key, s
        if best and best_score >= self.threshold:
            self.known[sig] = best
            return best
        key = "name:" + sig
        self.known[sig] = key
        return key
