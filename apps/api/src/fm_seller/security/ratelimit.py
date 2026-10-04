"""Limite de taxa por janela deslizante, na memória do processo.

Serve de trava contra enxurrada e tentativa repetida, não de cota comercial. Cada chave guarda só
os instantes dos últimos pedidos; o número de chaves é limitado para um atacante não encher a
memória trocando de chave. Com mais de uma instância da API cada uma conta sozinha (limite real =
limite x instâncias): aceitável para o MVP e registrado em docs/SEGURANCA.md.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

MAX_KEYS = 20_000


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, window: float, now: float) -> None:
        """Esvazia o que já saiu da janela; se ainda passar do teto, descarta as mais velhas."""
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= now - window]:
            del self._hits[key]
        if len(self._hits) > MAX_KEYS:
            oldest = sorted(self._hits, key=lambda k: self._hits[k][-1])
            for key in oldest[: len(self._hits) - MAX_KEYS + MAX_KEYS // 10]:
                del self._hits[key]

    def check(self, key: str, limit: int, window: float) -> tuple[bool, int]:
        """Registra um pedido. Devolve (permitido, segundos até poder tentar de novo)."""
        now = self._clock()
        with self._lock:
            queue = self._hits.setdefault(key, deque())
            while queue and queue[0] <= now - window:
                queue.popleft()
            if len(queue) >= limit:
                return False, max(1, int(queue[0] + window - now) + 1)
            queue.append(now)
            if len(self._hits) > MAX_KEYS:
                self._trim(window, now)
            return True, 0

    def count(self, key: str, window: float) -> int:
        """Quantos pedidos a chave fez na janela (sem registrar um novo)."""
        now = self._clock()
        with self._lock:
            queue = self._hits.get(key)
            if not queue:
                return 0
            return sum(1 for t in queue if t > now - window)

    def retry_after(self, key: str, window: float) -> int:
        now = self._clock()
        with self._lock:
            queue = self._hits.get(key)
            return max(1, int(queue[0] + window - now) + 1) if queue else 1

    def add(self, key: str) -> None:
        """Registra um acontecimento (por exemplo, um webhook recusado) sem exigir permissão."""
        now = self._clock()
        with self._lock:
            self._hits.setdefault(key, deque()).append(now)
