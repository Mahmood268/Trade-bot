"""MetaTrader 5 adapter — the ONLY module in the codebase that imports MetaTrader5.

Everything else talks to :class:`MT5Client`, so moving to a VPS, swapping brokers
or writing tests means touching this one file.

Platform note
-------------
The ``MetaTrader5`` package is Windows-only: it communicates with a running MT5
desktop terminal over Windows IPC. There is no cloud or REST path to MT5. This
module therefore imports it lazily and raises an actionable error elsewhere, so
the rest of the package (strategy, risk, backtester, tests) remains importable
on Linux and macOS for development.
"""

from __future__ import annotations

import logging
import math
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd

log = logging.getLogger(__name__)

Direction = Literal["buy", "sell"]

# --- Trade server return codes we treat specially ---------------------------
RETCODE_DONE = 10009
RETCODE_PLACED = 10008
RETCODE_REQUOTE = 10004
RETCODE_PRICE_CHANGED = 10020
RETCODE_PRICE_OFF = 10021
RETCODE_INVALID_FILL = 10030
RETCODE_INVALID_STOPS = 10016
RETCODE_NO_MONEY = 10019
RETCODE_TRADE_DISABLED = 10017
RETCODE_MARKET_CLOSED = 10018
RETCODE_TIMEOUT = 10012

SUCCESS_RETCODES = frozenset({RETCODE_DONE, RETCODE_PLACED})

# Transient conditions: the price moved under us. Re-read the tick and retry.
RETRYABLE_RETCODES = frozenset(
    {RETCODE_REQUOTE, RETCODE_PRICE_CHANGED, RETCODE_PRICE_OFF, RETCODE_TIMEOUT}
)

# Human-readable explanations, so a failed order in the journal is diagnosable
# months later without looking up MQL5 documentation.
RETCODE_MEANINGS: dict[int, str] = {
    RETCODE_REQUOTE: "requote — price moved before the order reached the server",
    RETCODE_PLACED: "order placed",
    RETCODE_DONE: "done",
    RETCODE_TIMEOUT: "request timed out",
    RETCODE_INVALID_STOPS: "invalid stops — SL/TP too close to price or on the wrong side",
    RETCODE_TRADE_DISABLED: "trading disabled for this symbol or account",
    RETCODE_MARKET_CLOSED: "market closed",
    RETCODE_NO_MONEY: "insufficient free margin",
    RETCODE_PRICE_CHANGED: "price changed",
    RETCODE_PRICE_OFF: "no quotes to process the request",
    RETCODE_INVALID_FILL: "unsupported filling mode for this symbol",
}


class MT5Error(RuntimeError):
    """Raised for any unrecoverable MetaTrader 5 condition."""


class MT5UnavailableError(MT5Error):
    """The MetaTrader5 package cannot be imported (almost always: not on Windows)."""


def _import_mt5() -> Any:
    """Import MetaTrader5 with an error message that explains the real problem."""
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError as exc:
        raise MT5UnavailableError(
            "The 'MetaTrader5' package could not be imported "
            f"(current platform: {platform.system()}).\n"
            "It is Windows-only — it talks to a running MT5 desktop terminal over "
            "Windows IPC, and there is no cloud or REST alternative.\n"
            "On Windows: install 64-bit Python 3.11 and run `pip install MetaTrader5`.\n"
            "On Linux/macOS: development, backtesting and the test suite all work, but "
            "live trading requires a Windows machine or VPS running the terminal."
        ) from exc
    return mt5


@dataclass(frozen=True)
class SymbolSpec:
    """Everything the sizing and order code needs to know about the traded symbol.

    Read once at startup and cached: these values are broker-specific and getting
    any of them wrong silently mis-sizes every position.
    """

    name: str
    digits: int
    point: float
    contract_size: float          # ounces per 1.00 lot — 100 for standard XAUUSD
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: int       # broker minimum SL/TP distance from price, in points
    freeze_level_points: int
    tick_value: float             # account-currency value of one tick per 1.00 lot
    tick_size: float
    filling_modes: int            # bitmask of supported order filling modes
    currency_profit: str

    @property
    def value_per_point_per_lot(self) -> float:
        """Account-currency P&L for a one-point move on a 1.00 lot position.

        Prefers the broker's own tick_value/tick_size (correct even when the profit
        currency differs from the account currency), falling back to the contract
        size when the broker reports nothing usable.
        """
        if self.tick_value > 0 and self.tick_size > 0:
            return self.tick_value * (self.point / self.tick_size)
        return self.contract_size * self.point

    def normalize_price(self, price: float) -> float:
        return round(price, self.digits)

    def normalize_volume(self, volume: float) -> float:
        """Floor a lot size onto the broker's volume step and clamp to its range.

        Flooring rather than rounding is deliberate: rounding up would risk
        slightly more than the Risk Warden authorised.
        """
        if self.volume_step <= 0:
            return round(volume, 2)
        steps = math.floor(round(volume / self.volume_step, 8))
        vol = steps * self.volume_step
        # Volume steps are decimal (0.01); derive precision from the step itself.
        decimals = max(0, -int(math.floor(math.log10(self.volume_step))))
        vol = round(vol, decimals)
        return max(0.0, min(vol, self.volume_max))


@dataclass(frozen=True)
class Tick:
    time: datetime
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) / 2.0


@dataclass(frozen=True)
class Position:
    ticket: int
    symbol: str
    direction: Direction
    volume: float
    open_price: float
    sl: float
    tp: float
    profit: float
    magic: int
    comment: str
    open_time: datetime


@dataclass(frozen=True)
class AccountInfo:
    login: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    currency: str
    leverage: int
    server: str
    trade_allowed: bool


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    retcode: int
    comment: str
    ticket: int | None = None
    price: float | None = None
    volume: float | None = None
    request: dict[str, Any] | None = None

    @property
    def reason(self) -> str:
        meaning = RETCODE_MEANINGS.get(self.retcode, "unknown retcode")
        return f"{self.retcode} ({meaning}): {self.comment}"


class MT5Client:
    """Connection, market data and order execution against a MetaTrader 5 terminal."""

    TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

    def __init__(self, cfg: Any, mt5_module: Any | None = None) -> None:
        """
        Args:
            cfg: an ``MT5Config`` (see goldbot.config).
            mt5_module: injectable MetaTrader5 module — used by the test suite to
                run the whole adapter on Linux against a fake terminal.
        """
        self.cfg = cfg
        self._mt5 = mt5_module
        self._connected = False
        self._symbol: str | None = None
        self._spec: SymbolSpec | None = None
        self._filling_mode: int | None = None

    # -- connection ----------------------------------------------------------

    @property
    def mt5(self) -> Any:
        if self._mt5 is None:
            self._mt5 = _import_mt5()
        return self._mt5

    def connect(self) -> AccountInfo:
        """Initialise the terminal, log in if credentials are configured, and
        verify that algorithmic trading is actually permitted."""
        mt5 = self.mt5
        kwargs: dict[str, Any] = {"timeout": int(self.cfg.connect_timeout_s * 1000)}
        if self.cfg.terminal_path:
            kwargs["path"] = self.cfg.terminal_path
        if self.cfg.login:
            kwargs["login"] = int(self.cfg.login)
            if self.cfg.password:
                kwargs["password"] = self.cfg.password
            if self.cfg.server:
                kwargs["server"] = self.cfg.server

        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            raise MT5Error(
                f"MT5 initialize() failed: [{code}] {msg}\n"
                "Checklist: is the MetaTrader 5 terminal running and logged in? Is "
                "'Algo Trading' enabled (Tools -> Options -> Expert Advisors)? Are the "
                "login/server in config correct? Is your Python 64-bit?"
            )

        self._connected = True
        info = self.account_info()
        if not info.trade_allowed:
            log.warning(
                "Terminal reports trading is NOT allowed for this account. Enable "
                "'Algo Trading' in the terminal toolbar, or the bot can read prices "
                "but never place an order."
            )
        log.info(
            "Connected to MT5: account %s on %s, balance %.2f %s",
            info.login, info.server, info.balance, info.currency,
        )
        return info

    def shutdown(self) -> None:
        if self._connected and self._mt5 is not None:
            self._mt5.shutdown()
            self._connected = False
            log.info("MT5 connection closed")

    def is_connected(self) -> bool:
        """Cheap liveness probe — the watchdog calls this, so it must never raise."""
        if not self._connected or self._mt5 is None:
            return False
        try:
            return self._mt5.terminal_info() is not None
        except Exception:  # noqa: BLE001 - liveness check must not propagate
            return False

    def __enter__(self) -> "MT5Client":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()

    # -- symbol discovery ----------------------------------------------------

    def discover_symbol(self) -> str:
        """Find this broker's tradable gold symbol.

        Brokers name gold inconsistently (XAUUSD, GOLD, XAUUSD.m, XAUUSDc...), so
        rather than hardcoding one we probe the configured candidates and take the
        first that both exists and is fully tradable. An explicit ``symbol`` in
        config skips discovery but is still validated.
        """
        if self._symbol:
            return self._symbol

        mt5 = self.mt5
        candidates = [self.cfg.symbol] if self.cfg.symbol else list(self.cfg.symbol_candidates)
        tried: list[str] = []

        for name in candidates:
            if not name:
                continue
            tried.append(name)
            info = mt5.symbol_info(name)
            if info is None:
                continue
            # Symbols hidden from Market Watch return no ticks until selected.
            if not getattr(info, "visible", True) and not mt5.symbol_select(name, True):
                log.debug("symbol %s exists but could not be selected", name)
                continue
            info = mt5.symbol_info(name)
            if info is None:
                continue
            if getattr(info, "trade_mode", None) == getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", -1):
                log.debug("symbol %s found but trading is disabled on it", name)
                continue
            tick = mt5.symbol_info_tick(name)
            if tick is None or not tick.ask:
                log.debug("symbol %s selected but returns no quotes", name)
                continue

            self._symbol = name
            log.info("Resolved gold symbol: %s", name)
            return name

        raise MT5Error(
            "Could not find a tradable gold symbol. Tried: " + ", ".join(tried) + ".\n"
            "Open Market Watch in the terminal (Ctrl+M), right-click -> Show All, and find "
            "the exact name your broker uses for gold. Then set mt5.symbol in config/config.yaml."
        )

    @property
    def symbol(self) -> str:
        return self._symbol or self.discover_symbol()

    def symbol_spec(self, refresh: bool = False) -> SymbolSpec:
        """Contract specifications, cached after first read."""
        if self._spec is not None and not refresh:
            return self._spec

        name = self.symbol
        info = self.mt5.symbol_info(name)
        if info is None:
            raise MT5Error(f"symbol_info({name}) returned None after successful discovery")

        self._spec = SymbolSpec(
            name=name,
            digits=int(info.digits),
            point=float(info.point),
            contract_size=float(getattr(info, "trade_contract_size", 100.0)),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            stops_level_points=int(getattr(info, "trade_stops_level", 0)),
            freeze_level_points=int(getattr(info, "trade_freeze_level", 0)),
            tick_value=float(getattr(info, "trade_tick_value", 0.0)),
            tick_size=float(getattr(info, "trade_tick_size", 0.0)),
            filling_modes=int(getattr(info, "filling_mode", 0)),
            currency_profit=str(getattr(info, "currency_profit", "USD")),
        )
        return self._spec

    # -- market data ---------------------------------------------------------

    def _timeframe(self, name: str) -> int:
        attr = f"TIMEFRAME_{name.upper()}"
        tf = getattr(self.mt5, attr, None)
        if tf is None:
            raise MT5Error(f"unknown timeframe {name!r}; expected one of {self.TIMEFRAMES}")
        return tf

    def get_bars(self, timeframe: str, count: int, symbol: str | None = None) -> pd.DataFrame:
        """Most recent ``count`` bars, oldest first, indexed by UTC bar-open time.

        The final row is the *forming* bar. Strategy code must act on closed bars
        only — see :meth:`get_closed_bars`.
        """
        sym = symbol or self.symbol
        rates = self.mt5.copy_rates_from_pos(sym, self._timeframe(timeframe), 0, count)
        if rates is None or len(rates) == 0:
            code, msg = self.mt5.last_error()
            raise MT5Error(f"copy_rates_from_pos({sym}, {timeframe}, {count}) failed: [{code}] {msg}")

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time").sort_index()
        return df.rename(columns={"tick_volume": "volume", "real_volume": "real_volume"})

    def get_closed_bars(self, timeframe: str, count: int, symbol: str | None = None) -> pd.DataFrame:
        """Bars excluding the still-forming one.

        Acting on a forming bar is the classic backtest/live divergence: the signal
        appears mid-bar, then vanishes when the bar closes elsewhere.
        """
        df = self.get_bars(timeframe, count + 1, symbol=symbol)
        return df.iloc[:-1]

    def get_tick(self, symbol: str | None = None) -> Tick:
        sym = symbol or self.symbol
        tick = self.mt5.symbol_info_tick(sym)
        if tick is None:
            code, msg = self.mt5.last_error()
            raise MT5Error(f"symbol_info_tick({sym}) failed: [{code}] {msg}")
        return Tick(
            time=datetime.fromtimestamp(tick.time, tz=timezone.utc),
            bid=float(tick.bid),
            ask=float(tick.ask),
        )

    def spread_points(self, symbol: str | None = None) -> float:
        """Current spread in points — the gate that keeps scalping viable."""
        spec = self.symbol_spec()
        return self.get_tick(symbol).spread / spec.point

    def account_info(self) -> AccountInfo:
        info = self.mt5.account_info()
        if info is None:
            code, msg = self.mt5.last_error()
            raise MT5Error(f"account_info() failed: [{code}] {msg}")
        return AccountInfo(
            login=int(info.login),
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(info.margin),
            margin_free=float(info.margin_free),
            currency=str(info.currency),
            leverage=int(info.leverage),
            server=str(getattr(info, "server", "")),
            trade_allowed=bool(getattr(info, "trade_allowed", True)),
        )

    # -- positions -----------------------------------------------------------

    def positions(self, symbol: str | None = None, all_magics: bool = False) -> list[Position]:
        """Open positions belonging to this bot.

        Filtered by magic number by default, so positions you opened by hand are
        invisible to the bot and can never be modified or closed by it.
        """
        sym = symbol or self.symbol
        raw = self.mt5.positions_get(symbol=sym)
        if raw is None:
            return []
        out: list[Position] = []
        for p in raw:
            if not all_magics and int(p.magic) != int(self.cfg.magic):
                continue
            out.append(
                Position(
                    ticket=int(p.ticket),
                    symbol=str(p.symbol),
                    direction="buy" if int(p.type) == 0 else "sell",
                    volume=float(p.volume),
                    open_price=float(p.price_open),
                    sl=float(p.sl),
                    tp=float(p.tp),
                    profit=float(p.profit),
                    magic=int(p.magic),
                    comment=str(getattr(p, "comment", "")),
                    open_time=datetime.fromtimestamp(int(p.time), tz=timezone.utc),
                )
            )
        return out

    # -- order execution -----------------------------------------------------

    def _resolve_filling_modes(self) -> list[int]:
        """Filling modes to try, best first.

        Brokers accept different modes and reject the others with retcode 10030.
        The symbol's ``filling_mode`` bitmask tells us what is supported, but it is
        not always accurate, so the full list stays as fallback.
        """
        mt5 = self.mt5
        spec = self.symbol_spec()
        fok = getattr(mt5, "ORDER_FILLING_FOK", 0)
        ioc = getattr(mt5, "ORDER_FILLING_IOC", 1)
        ret = getattr(mt5, "ORDER_FILLING_RETURN", 2)

        if self._filling_mode is not None:
            preferred = [self._filling_mode]
            return preferred + [m for m in (fok, ioc, ret) if m != self._filling_mode]

        ordered: list[int] = []
        # SYMBOL_FILLING_FOK = 1, SYMBOL_FILLING_IOC = 2 in the symbol bitmask.
        if spec.filling_modes & 1:
            ordered.append(fok)
        if spec.filling_modes & 2:
            ordered.append(ioc)
        for m in (fok, ioc, ret):
            if m not in ordered:
                ordered.append(m)
        return ordered

    def validate_stops(
        self, direction: Direction, entry: float, sl: float, tp: float | None
    ) -> None:
        """Reject stops the broker would refuse, before wasting an order attempt.

        Catches both the broker's minimum-distance rule and stops placed on the
        wrong side of price — the latter being a strategy bug that must surface
        loudly rather than as a generic 'invalid stops' rejection.
        """
        spec = self.symbol_spec()
        min_dist = spec.stops_level_points * spec.point

        if direction == "buy":
            if sl >= entry:
                raise MT5Error(f"buy stop loss {sl} must be below entry {entry}")
            if tp is not None and tp <= entry:
                raise MT5Error(f"buy take profit {tp} must be above entry {entry}")
        else:
            if sl <= entry:
                raise MT5Error(f"sell stop loss {sl} must be above entry {entry}")
            if tp is not None and tp >= entry:
                raise MT5Error(f"sell take profit {tp} must be below entry {entry}")

        if min_dist > 0:
            if abs(entry - sl) < min_dist:
                raise MT5Error(
                    f"stop loss {sl} is {abs(entry - sl):.2f} from entry {entry}, inside the "
                    f"broker's minimum stop distance of {min_dist:.2f} "
                    f"({spec.stops_level_points} points)"
                )
            if tp is not None and abs(entry - tp) < min_dist:
                raise MT5Error(
                    f"take profit {tp} is inside the broker's minimum stop distance of "
                    f"{min_dist:.2f} ({spec.stops_level_points} points)"
                )

    def market_order(
        self,
        direction: Direction,
        volume: float,
        sl: float,
        tp: float | None = None,
        comment: str = "goldbot",
        max_attempts: int = 3,
    ) -> OrderResult:
        """Send a market order with a server-side stop loss attached.

        The stop loss is non-optional by design: it is set on the broker at
        placement, so a crashed bot, a sleeping PC or a dropped connection still
        leaves the position protected.

        Retries only transient price conditions (requote, price changed) and
        filling-mode rejection. Structural failures — no money, invalid stops,
        market closed — return immediately with the reason rather than hammering
        the trade server.
        """
        mt5 = self.mt5
        spec = self.symbol_spec()
        sym = self.symbol

        volume = spec.normalize_volume(volume)
        if volume < spec.volume_min:
            return OrderResult(
                ok=False,
                retcode=-1,
                comment=(
                    f"computed volume {volume} is below the broker minimum {spec.volume_min}; "
                    "the risk budget cannot cover even the smallest position for this stop "
                    "distance — skipping the trade rather than over-risking"
                ),
            )

        order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
        filling_modes = self._resolve_filling_modes()
        fill_idx = 0
        last: OrderResult | None = None

        for attempt in range(1, max_attempts + 1):
            tick = self.get_tick()
            price = tick.ask if direction == "buy" else tick.bid

            self.validate_stops(direction, price, sl, tp)

            request: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym,
                "volume": float(volume),
                "type": order_type,
                "price": spec.normalize_price(price),
                "sl": spec.normalize_price(sl),
                "deviation": int(self.cfg.deviation_points),
                "magic": int(self.cfg.magic),
                "comment": comment[:31],  # MT5 truncates silently past 31 chars
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_modes[fill_idx],
            }
            if tp is not None:
                request["tp"] = spec.normalize_price(tp)

            result = mt5.order_send(request)
            if result is None:
                code, msg = mt5.last_error()
                last = OrderResult(
                    ok=False, retcode=code, comment=f"order_send returned None: {msg}",
                    request=request,
                )
                log.warning("order_send returned None (attempt %d): %s", attempt, msg)
                time.sleep(0.5 * attempt)
                continue

            retcode = int(result.retcode)
            if retcode in SUCCESS_RETCODES:
                # Remember the accepted filling mode so later orders skip the probing.
                self._filling_mode = filling_modes[fill_idx]
                log.info(
                    "%s %.2f %s @ %.2f sl=%.2f tp=%s ticket=%s",
                    direction.upper(), volume, sym, result.price, sl,
                    f"{tp:.2f}" if tp else "none", result.order,
                )
                return OrderResult(
                    ok=True,
                    retcode=retcode,
                    comment=str(result.comment),
                    ticket=int(result.order),
                    price=float(result.price),
                    volume=float(result.volume),
                    request=request,
                )

            last = OrderResult(
                ok=False, retcode=retcode, comment=str(result.comment), request=request
            )

            if retcode == RETCODE_INVALID_FILL and fill_idx + 1 < len(filling_modes):
                fill_idx += 1
                log.info(
                    "broker rejected filling mode; retrying with mode %s",
                    filling_modes[fill_idx],
                )
                continue

            if retcode in RETRYABLE_RETCODES and attempt < max_attempts:
                log.info("transient order rejection (%s), retrying", last.reason)
                time.sleep(0.3 * attempt)
                continue

            log.error("order rejected: %s", last.reason)
            return last

        return last or OrderResult(ok=False, retcode=-1, comment="no attempts made")

    def modify_position(
        self, ticket: int, sl: float | None = None, tp: float | None = None
    ) -> OrderResult:
        """Change the stop loss / take profit of an existing position."""
        mt5 = self.mt5
        spec = self.symbol_spec()
        current = next((p for p in self.positions() if p.ticket == ticket), None)
        if current is None:
            return OrderResult(
                ok=False, retcode=-1,
                comment=f"position {ticket} not found among this bot's positions",
            )

        new_sl = spec.normalize_price(sl if sl is not None else current.sl)
        new_tp = spec.normalize_price(tp if tp is not None else current.tp)

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": current.symbol,
            "position": int(ticket),
            "sl": new_sl,
            "tp": new_tp,
            "magic": int(self.cfg.magic),
        }
        result = mt5.order_send(request)
        if result is None:
            code, msg = mt5.last_error()
            return OrderResult(ok=False, retcode=code, comment=str(msg), request=request)

        retcode = int(result.retcode)
        ok = retcode in SUCCESS_RETCODES
        if ok:
            log.info("position %s modified: sl=%.2f tp=%.2f", ticket, new_sl, new_tp)
        else:
            log.warning("modify of position %s failed: %s", ticket, result.comment)
        return OrderResult(
            ok=ok, retcode=retcode, comment=str(result.comment), ticket=ticket, request=request
        )

    def close_position(self, ticket: int, volume: float | None = None) -> OrderResult:
        """Close a position, fully or partially.

        Partial closes are how the Trade Manager takes profit off the table while
        letting the rest run.
        """
        mt5 = self.mt5
        spec = self.symbol_spec()
        pos = next((p for p in self.positions() if p.ticket == ticket), None)
        if pos is None:
            return OrderResult(
                ok=False, retcode=-1,
                comment=f"position {ticket} not found among this bot's positions",
            )

        close_volume = spec.normalize_volume(volume) if volume is not None else pos.volume
        close_volume = min(close_volume, pos.volume)
        if close_volume < spec.volume_min:
            return OrderResult(
                ok=False, retcode=-1,
                comment=f"close volume {close_volume} below broker minimum {spec.volume_min}",
            )

        tick = self.get_tick(pos.symbol)
        if pos.direction == "buy":
            order_type, price = mt5.ORDER_TYPE_SELL, tick.bid
        else:
            order_type, price = mt5.ORDER_TYPE_BUY, tick.ask

        filling_modes = self._resolve_filling_modes()
        last: OrderResult | None = None

        for fill in filling_modes[:3]:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": float(close_volume),
                "type": order_type,
                "position": int(ticket),
                "price": spec.normalize_price(price),
                "deviation": int(self.cfg.deviation_points),
                "magic": int(self.cfg.magic),
                "comment": "goldbot close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": fill,
            }
            result = mt5.order_send(request)
            if result is None:
                code, msg = mt5.last_error()
                last = OrderResult(ok=False, retcode=code, comment=str(msg), request=request)
                continue
            retcode = int(result.retcode)
            if retcode in SUCCESS_RETCODES:
                log.info("closed %.2f of position %s @ %.2f", close_volume, ticket, result.price)
                return OrderResult(
                    ok=True, retcode=retcode, comment=str(result.comment), ticket=ticket,
                    price=float(result.price), volume=float(result.volume), request=request,
                )
            last = OrderResult(
                ok=False, retcode=retcode, comment=str(result.comment), request=request
            )
            if retcode != RETCODE_INVALID_FILL:
                break

        if last is not None:
            log.error("failed to close position %s: %s", ticket, last.reason)
        return last or OrderResult(ok=False, retcode=-1, comment="no close attempted")

    def close_all(self) -> list[OrderResult]:
        """Flatten every position owned by this bot. Backs the /flat kill switch."""
        return [self.close_position(p.ticket) for p in self.positions()]
