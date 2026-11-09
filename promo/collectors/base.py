"""Base dos coletores: interface comum, HTTP com limite de requisições e repetição."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator, Optional

from ..models import Offer

USER_AGENT = "PromoMonitor/0.1 (+monitoramento de precos via APIs autorizadas)"


def safe_url(url: str) -> str:
    """Remove a query string (pode conter tokens) antes de registrar a URL em logs/banco."""
    return url.split("?", 1)[0]


class RateLimiter:
    """Limita a N requisições por segundo (token bucket simples, thread-safe)."""

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second if per_second > 0 else 0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


class CollectorError(Exception):
    pass


class Collector:
    """Cada loja implementa `collect()` devolvendo objetos Offer."""
    store_id: str = ""

    def __init__(self, config: dict):
        self.config = config or {}
        self.limiter = RateLimiter(float(self.config.get("requests_per_second", 2)))
        self.max_retries = int(self.config.get("max_retries", 3))

    def collect(self) -> Iterator[Offer]:
        raise NotImplementedError

    # -------- HTTP --------
    def http_get_json(self, url: str, params: Optional[dict] = None,
                      headers: Optional[dict] = None) -> dict:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        hdrs.update(headers or {})
        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            req = urllib.request.Request(url, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # 429 (limite) e 5xx: espera e tenta de novo, respeitando Retry-After
                if e.code == 429 or 500 <= e.code < 600:
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                    if attempt < self.max_retries:
                        time.sleep(wait)
                        delay *= 2
                        continue
                body = e.read().decode("utf-8", "replace")[:300]
                raise CollectorError(f"HTTP {e.code} em {safe_url(url)}: {body}") from e
            except urllib.error.URLError as e:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise CollectorError(f"Falha de rede em {safe_url(url)}: {e.reason}") from e
        raise CollectorError(f"Esgotadas as tentativas em {safe_url(url)}")
