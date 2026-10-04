"""Teste de carga leve: `python -m fm_seller.cli loadtest --api URL`.

Dispara pedidos simultâneos contra rotas que não exigem login (saúde, prontidão e uma rota fechada)
e mede quantos por segundo a API aguenta e a latência. Serve para **comparar a mesma máquina antes e
depois de uma mudança**: os números dependem do computador, do banco e da rede, e NÃO são a
capacidade de produção. Sem custo: roda contra o que você mesmo subiu. Por segurança só aceita
endereço local, a menos que se confirme com `--remote` (carga em servidor de verdade pode custar ou
derrubar o que está no ar).
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

PATHS = (
    "/v1/health",
    "/v1/ready",
    "/v1/me",
)  # /v1/me sem login: dá 401, passa pelo guarda e pelo banco
LOCAL_HOSTS = ("localhost", "127.0.0.1", "testserver")


@dataclass
class LoadReport:
    seconds: float
    concurrency: int
    latencies_ms: list[float] = field(default_factory=list)
    statuses: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return len(self.latencies_ms)

    @property
    def rps(self) -> float:
        return round(self.total / self.seconds, 1) if self.seconds else 0.0

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
        return round(ordered[index], 1)

    @property
    def errors(self) -> int:
        return sum(n for k, n in self.statuses.items() if k.startswith("erro") or k.startswith("5"))

    def lines(self) -> list[str]:
        mean = round(statistics.fmean(self.latencies_ms), 1) if self.latencies_ms else 0.0
        return [
            f"pedidos: {self.total} em {self.seconds:.0f} s com {self.concurrency} simultâneos "
            f"= {self.rps} por segundo",
            f"latência (ms): média {mean} · p50 {self.percentile(50)} · "
            f"p95 {self.percentile(95)} · p99 {self.percentile(99)}",
            "respostas: " + ", ".join(f"{k}: {v}" for k, v in sorted(self.statuses.items())),
            "ATENÇÃO: números desta máquina, para comparar antes e depois; não são a "
            "capacidade de produção.",
        ]


def is_local(url: str) -> bool:
    host = httpx.URL(url).host
    return host in LOCAL_HOSTS


def run(
    api: str,
    *,
    seconds: float = 10,
    concurrency: int = 10,
    paths: tuple[str, ...] = PATHS,
    client_factory: Callable[[], httpx.Client] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> LoadReport:
    api = api.rstrip("/")
    report = LoadReport(seconds=seconds, concurrency=concurrency)
    lock = threading.Lock()
    deadline = clock() + seconds

    def worker(offset: int) -> None:
        client = client_factory() if client_factory else httpx.Client(timeout=20)
        i = offset
        try:
            while clock() < deadline:
                path = paths[i % len(paths)]
                i += 1
                started = time.perf_counter()
                try:
                    code = str(client.get(f"{api}{path}").status_code)
                except httpx.HTTPError as exc:
                    code = "erro:" + type(exc).__name__
                elapsed = (time.perf_counter() - started) * 1000
                with lock:
                    report.latencies_ms.append(elapsed)
                    report.statuses[code] += 1
        finally:
            client.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return report
