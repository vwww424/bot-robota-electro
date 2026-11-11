"""Гроші: розбір введених сум, форматування, локальна дата."""

import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Kyiv"))

_AMOUNT = re.compile(
    r"^\s*(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(?:грн\.?|₴|uah)?\s*[-—–:]?\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def local_today() -> date:
    return datetime.now(TZ).date()


def parse_amount(text: str) -> tuple[int, str] | None:
    """'1 500,50 грн кабель' -> (150050, 'кабель'). Суми зберігаються в копійках."""
    m = _AMOUNT.match(text or "")
    if not m:
        return None
    whole = int(re.sub(r"\D", "", m.group(1)))
    frac = int((m.group(2) or "0").ljust(2, "0"))
    cents = whole * 100 + frac
    if cents <= 0:
        return None
    return cents, m.group(3).strip()


def fmt(cents: int | float) -> str:
    cents = round(cents)
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    s = f"{whole:,}".replace(",", " ")
    if frac:
        s += f",{frac:02d}"
    return f"{sign}{s} грн"


def parse_date(text: str) -> date | None:
    """'05.11.2026', '5.11', '05/11/26' -> date."""
    m = re.match(r"^\s*(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\s*$", text or "")
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else local_today().year
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def days_between(start: str, end: str | None) -> int:
    """Calendar days including both ends; end defaults to today."""
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end) if end else local_today()
    return max((d1 - d0).days + 1, 1)
