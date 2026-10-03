"""Janela de silêncio e limite diário no fuso do cliente."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def in_quiet_hours(now: datetime, tz: str, start: int, end: int) -> bool:
    """True se `now` cai na janela de silêncio [start, end) no fuso do cliente (pode cruzar 0h)."""
    hour = now.astimezone(ZoneInfo(tz)).hour
    if start == end:
        return False
    return hour >= start or hour < end if start > end else start <= hour < end


def next_allowed(at: datetime, tz: str, start: int, end: int) -> datetime:
    """Primeiro instante >= `at` fora da janela de silêncio."""
    if not in_quiet_hours(at, tz, start, end):
        return at
    zone = ZoneInfo(tz)
    local = at.astimezone(zone)
    candidate = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    # Em fusos com horário de verão o dia pode ter 23/25 h: reconfere o resultado.
    result = candidate.astimezone(at.tzinfo)
    return result if not in_quiet_hours(result, tz, start, end) else result + timedelta(hours=1)


def local_day_start(now: datetime, tz: str) -> datetime:
    zone = ZoneInfo(tz)
    return now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)


def next_local_day_open(now: datetime, tz: str, end: int) -> datetime:
    """Amanhã às `end` horas no fuso do cliente (usado quando o limite diário estoura)."""
    zone = ZoneInfo(tz)
    tomorrow = (now.astimezone(zone) + timedelta(days=1)).replace(
        hour=end, minute=0, second=0, microsecond=0
    )
    return tomorrow.astimezone(now.tzinfo)
