"""A fake MetaTrader5 module.

The real package is Windows-only, so without this the adapter could only be
tested by trading on a live terminal. This stands in for the terminal and lets
us drive the paths that matter and are hard to reproduce on demand: requotes,
filling-mode rejection, invalid stops, partial fills, symbols that exist but are
disabled.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Any

# --- constants mirroring the real module ------------------------------------
TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15 = 1, 5, 15
TIMEFRAME_M30, TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 30, 16385, 16388, 16408

ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 2
SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_FULL = 4

TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_INVALID_FILL = 10030


@dataclass
class FakeSymbol:
    name: str
    digits: int = 2
    point: float = 0.01
    trade_contract_size: float = 100.0
    volume_min: float = 0.01
    volume_max: float = 50.0
    volume_step: float = 0.01
    trade_stops_level: int = 0
    trade_freeze_level: int = 0
    trade_tick_value: float = 1.0
    trade_tick_size: float = 0.01
    filling_mode: int = 1              # bitmask: 1=FOK, 2=IOC
    currency_profit: str = "USD"
    visible: bool = True
    trade_mode: int = SYMBOL_TRADE_MODE_FULL
    bid: float = 2650.00
    ask: float = 2650.25


@dataclass
class _Result:
    retcode: int
    comment: str = "ok"
    order: int = 0
    price: float = 0.0
    volume: float = 0.0


@dataclass
class _Position:
    ticket: int
    symbol: str
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    magic: int
    comment: str
    time: int


class FakeMT5:
    """Configurable fake terminal. Instances are injected into MT5Client.

    The real MetaTrader5 constants live at module level; MT5Client reads them off
    whatever object it was given, so they are mirrored here as class attributes.
    """

    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15 = TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15
    TIMEFRAME_M30, TIMEFRAME_H1 = TIMEFRAME_M30, TIMEFRAME_H1
    TIMEFRAME_H4, TIMEFRAME_D1 = TIMEFRAME_H4, TIMEFRAME_D1

    ORDER_TYPE_BUY, ORDER_TYPE_SELL = ORDER_TYPE_BUY, ORDER_TYPE_SELL
    ORDER_FILLING_FOK = ORDER_FILLING_FOK
    ORDER_FILLING_IOC = ORDER_FILLING_IOC
    ORDER_FILLING_RETURN = ORDER_FILLING_RETURN
    ORDER_TIME_GTC = ORDER_TIME_GTC

    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = TRADE_ACTION_DEAL, TRADE_ACTION_SLTP
    SYMBOL_TRADE_MODE_DISABLED = SYMBOL_TRADE_MODE_DISABLED
    SYMBOL_TRADE_MODE_FULL = SYMBOL_TRADE_MODE_FULL

    def __init__(
        self,
        symbols: dict[str, FakeSymbol] | None = None,
        balance: float = 5000.0,
        trade_allowed: bool = True,
    ) -> None:
        self.symbols = symbols if symbols is not None else {"XAUUSD": FakeSymbol("XAUUSD")}
        self.balance = balance
        self.trade_allowed = trade_allowed
        self.initialized = False
        self._positions: list[_Position] = []
        self._next_ticket = 1000
        self._error: tuple[int, str] = (0, "no error")

        # Scripted failures, popped per order_send call. Each entry is a retcode
        # or None for "succeed".
        self.order_script: list[int | None] = []
        self.sent_requests: list[dict[str, Any]] = []
        self.init_should_fail = False

    # -- lifecycle -----------------------------------------------------------
    def initialize(self, **kwargs: Any) -> bool:
        if self.init_should_fail:
            self._error = (-10005, "IPC timeout")
            return False
        self.initialized = True
        return True

    def shutdown(self) -> None:
        self.initialized = False

    def terminal_info(self) -> Any:
        return object() if self.initialized else None

    def last_error(self) -> tuple[int, str]:
        return self._error

    def account_info(self) -> Any:
        equity = self.balance + sum(p.profit for p in self._positions)
        return type(
            "AccountInfo", (), {
                "login": 51234567, "balance": self.balance, "equity": equity,
                "margin": 0.0, "margin_free": equity, "currency": "USD",
                "leverage": 500, "server": "FakeBroker-Demo",
                "trade_allowed": self.trade_allowed,
            },
        )()

    # -- symbols -------------------------------------------------------------
    def symbol_info(self, name: str) -> FakeSymbol | None:
        return self.symbols.get(name)

    def symbol_select(self, name: str, enable: bool = True) -> bool:
        sym = self.symbols.get(name)
        if sym is None:
            return False
        sym.visible = enable
        return True

    def symbol_info_tick(self, name: str) -> Any:
        sym = self.symbols.get(name)
        if sym is None or not sym.visible:
            return None
        return type(
            "Tick", (), {"time": int(_time.time()), "bid": sym.bid, "ask": sym.ask}
        )()

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any:
        import numpy as np

        sym = self.symbols.get(symbol)
        if sym is None:
            return None
        now = int(_time.time())
        step = 900
        rows = []
        for i in range(count):
            t = now - (count - i) * step
            base = sym.bid + i * 0.1
            rows.append((t, base, base + 1.0, base - 1.0, base + 0.5, 100 + i, 2, 0))
        return np.array(
            rows,
            dtype=[
                ("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"),
                ("real_volume", "i8"),
            ],
        )

    # -- trading -------------------------------------------------------------
    def positions_get(self, symbol: str | None = None) -> list[_Position]:
        if symbol is None:
            return list(self._positions)
        return [p for p in self._positions if p.symbol == symbol]

    def order_send(self, request: dict[str, Any]) -> _Result | None:
        self.sent_requests.append(dict(request))

        if self.order_script:
            scripted = self.order_script.pop(0)
            if scripted is not None:
                return _Result(retcode=scripted, comment="scripted rejection")

        if request["action"] == TRADE_ACTION_SLTP:
            for p in self._positions:
                if p.ticket == request["position"]:
                    p.sl = request["sl"]
                    p.tp = request["tp"]
                    return _Result(retcode=TRADE_RETCODE_DONE, order=p.ticket)
            return _Result(retcode=10013, comment="invalid position")

        # Closing an existing position?
        if "position" in request:
            for p in list(self._positions):
                if p.ticket == request["position"]:
                    closing = min(request["volume"], p.volume)
                    if closing >= p.volume:
                        self._positions.remove(p)
                    else:
                        p.volume -= closing
                    return _Result(
                        retcode=TRADE_RETCODE_DONE, order=p.ticket,
                        price=request["price"], volume=closing,
                    )
            return _Result(retcode=10013, comment="invalid position")

        ticket = self._next_ticket
        self._next_ticket += 1
        self._positions.append(
            _Position(
                ticket=ticket, symbol=request["symbol"], type=request["type"],
                volume=request["volume"], price_open=request["price"],
                sl=request.get("sl", 0.0), tp=request.get("tp", 0.0), profit=0.0,
                magic=request.get("magic", 0), comment=request.get("comment", ""),
                time=int(_time.time()),
            )
        )
        return _Result(
            retcode=TRADE_RETCODE_DONE, order=ticket,
            price=request["price"], volume=request["volume"],
        )
