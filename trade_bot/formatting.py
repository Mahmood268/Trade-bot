"""Message bodies for signals, reports and errors.

Everything is rendered as Telegram HTML: only ``&``, ``<`` and ``>`` need
escaping, which is far less error-prone than MarkdownV2's fifteen reserved
characters. Every value that comes from market data or an exchange is passed
through :func:`esc`.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

_SIDE_ICONS = {
    "BUY": "\U0001F7E2",     # green circle
    "LONG": "\U0001F7E2",
    "SELL": "\U0001F534",    # red circle
    "SHORT": "\U0001F534",
    "CLOSE": "⚪",       # white circle
    "HOLD": "⚪",
}


def esc(value: Any) -> str:
    """Escape a value for Telegram's HTML parse mode."""
    return html.escape(str(value), quote=False)


def _fmt_number(value: float | int | None, places: int = 8) -> str:
    """Render a price or size without trailing zero noise."""
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}"
    text = f"{value:,.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_time(moment: datetime | None) -> str:
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass
class Signal:
    """One actionable trading signal."""

    symbol: str
    side: str
    price: float | None = None
    quantity: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy: str | None = None
    confidence: float | None = None
    note: str | None = None
    timestamp: datetime | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = str(self.symbol).upper()
        self.side = str(self.side).upper()


def format_signal(signal: Signal) -> str:
    """Render a :class:`Signal` as an HTML message body."""
    icon = _SIDE_ICONS.get(signal.side, "\U0001F4C8")
    lines = [f"{icon} <b>{esc(signal.side)} {esc(signal.symbol)}</b>"]

    if signal.price is not None:
        lines.append(f"Price: <code>{esc(_fmt_number(signal.price))}</code>")
    if signal.quantity is not None:
        lines.append(f"Size: <code>{esc(_fmt_number(signal.quantity))}</code>")
    if signal.stop_loss is not None:
        lines.append(f"Stop loss: <code>{esc(_fmt_number(signal.stop_loss))}</code>")
    if signal.take_profit is not None:
        lines.append(f"Take profit: <code>{esc(_fmt_number(signal.take_profit))}</code>")
    if signal.confidence is not None:
        lines.append(f"Confidence: <b>{esc(f'{signal.confidence:.0%}')}</b>")
    if signal.strategy:
        lines.append(f"Strategy: {esc(signal.strategy)}")

    for key, value in signal.extra.items():
        lines.append(f"{esc(str(key).replace('_', ' ').capitalize())}: {esc(value)}")

    if signal.note:
        lines.append("")
        lines.append(esc(signal.note))

    lines.append("")
    lines.append(f"<i>{esc(_fmt_time(signal.timestamp))}</i>")
    return "\n".join(lines)


def format_report(
    title: str,
    metrics: Mapping[str, Any] | None = None,
    *,
    rows: Sequence[Sequence[Any]] | None = None,
    headers: Sequence[str] | None = None,
    footer: str | None = None,
    timestamp: datetime | None = None,
) -> str:
    """Render a report: a headline, key/value metrics and an optional table.

    The table is emitted inside ``<pre>`` with padded columns so it stays
    aligned in Telegram's monospace font.
    """
    lines = [f"\U0001F4CA <b>{esc(title)}</b>"]

    if metrics:
        lines.append("")
        width = max(len(str(k)) for k in metrics)
        for key, value in metrics.items():
            label = str(key).replace("_", " ")
            lines.append(f"<code>{esc(label.ljust(width))}</code>  {esc(value)}")

    if rows:
        table = _render_table(rows, headers)
        lines.append("")
        lines.append(f"<pre>{esc(table)}</pre>")

    if footer:
        lines.append("")
        lines.append(esc(footer))

    lines.append("")
    lines.append(f"<i>{esc(_fmt_time(timestamp))}</i>")
    return "\n".join(lines)


def _render_table(rows: Sequence[Sequence[Any]], headers: Sequence[str] | None) -> str:
    body = [[str(cell) for cell in row] for row in rows]
    all_rows = ([list(headers)] if headers else []) + body
    if not all_rows:
        return ""

    columns = max(len(row) for row in all_rows)
    for row in all_rows:
        row.extend([""] * (columns - len(row)))
    widths = [max(len(row[i]) for row in all_rows) for i in range(columns)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    out = []
    if headers:
        out.append(line(all_rows[0]))
        out.append("-" * min(len(out[0]), 60))
        body_rows = all_rows[1:]
    else:
        body_rows = all_rows
    out.extend(line(row) for row in body_rows)
    return "\n".join(out)


def format_error(context: str, error: BaseException | str, *, timestamp: datetime | None = None) -> str:
    """Render an alert for something that went wrong in the trading loop."""
    detail = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    return "\n".join(
        [
            f"⚠️ <b>{esc(context)}</b>",
            "",
            f"<pre>{esc(detail)}</pre>",
            "",
            f"<i>{esc(_fmt_time(timestamp))}</i>",
        ]
    )


def format_key_values(pairs: Iterable[tuple[str, Any]]) -> str:
    """Small helper for command replies that list a handful of fields."""
    pairs = list(pairs)
    if not pairs:
        return "<i>nothing to show</i>"
    width = max(len(str(k)) for k, _ in pairs)
    return "\n".join(f"<code>{esc(str(k).ljust(width))}</code>  {esc(v)}" for k, v in pairs)
