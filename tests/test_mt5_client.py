"""Tests for the MT5 adapter, driven against a fake terminal.

These cover the failure modes that are hard to reproduce on a live account but
that will absolutely happen in production: brokers naming gold differently,
filling-mode rejection, requotes, and stops placed too close to price.
"""

from __future__ import annotations

import pytest

from goldbot.config import MT5Config
from goldbot.mt5_client import MT5Client, MT5Error
from tests.fake_mt5 import (
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID_FILL,
    TRADE_RETCODE_REQUOTE,
    FakeMT5,
    FakeSymbol,
)


def make_client(fake: FakeMT5, **cfg_kwargs) -> MT5Client:
    return MT5Client(MT5Config(**cfg_kwargs), mt5_module=fake)


# --- symbol discovery -------------------------------------------------------


def test_discovers_plain_xauusd():
    client = make_client(FakeMT5())
    assert client.discover_symbol() == "XAUUSD"


def test_discovers_broker_specific_suffix():
    """Many brokers use XAUUSD.m or GOLD — hardcoding one name breaks on them."""
    fake = FakeMT5(symbols={"XAUUSD.m": FakeSymbol("XAUUSD.m")})
    assert make_client(fake).discover_symbol() == "XAUUSD.m"


def test_skips_symbol_that_exists_but_is_disabled():
    """A symbol can be listed but untradable; picking it would fail at order time."""
    from tests.fake_mt5 import SYMBOL_TRADE_MODE_DISABLED

    fake = FakeMT5(
        symbols={
            "XAUUSD": FakeSymbol("XAUUSD", trade_mode=SYMBOL_TRADE_MODE_DISABLED),
            "GOLD": FakeSymbol("GOLD"),
        }
    )
    assert make_client(fake).discover_symbol() == "GOLD"


def test_selects_hidden_symbol_before_use():
    """Symbols not in Market Watch return no quotes until selected."""
    fake = FakeMT5(symbols={"XAUUSD": FakeSymbol("XAUUSD", visible=False)})
    client = make_client(fake)
    assert client.discover_symbol() == "XAUUSD"
    assert fake.symbols["XAUUSD"].visible is True


def test_no_gold_symbol_gives_actionable_error():
    fake = FakeMT5(symbols={"EURUSD": FakeSymbol("EURUSD")})
    with pytest.raises(MT5Error, match="Market Watch"):
        make_client(fake).discover_symbol()


def test_explicit_symbol_overrides_discovery():
    fake = FakeMT5(symbols={"XAUUSD": FakeSymbol("XAUUSD"), "GOLD": FakeSymbol("GOLD")})
    assert make_client(fake, symbol="GOLD").discover_symbol() == "GOLD"


# --- contract specs and normalisation ---------------------------------------


def test_value_per_point_uses_broker_tick_values():
    """1.00 lot of gold = 100oz, so a $1 move is $100; one point (0.01) is $1."""
    client = make_client(FakeMT5())
    client.discover_symbol()
    assert client.symbol_spec().value_per_point_per_lot == pytest.approx(1.0)


def test_volume_floors_to_step_never_rounds_up():
    """Rounding up would risk more than the Risk Warden authorised."""
    client = make_client(FakeMT5())
    client.discover_symbol()
    spec = client.symbol_spec()
    assert spec.normalize_volume(0.1279) == 0.12
    assert spec.normalize_volume(0.019) == 0.01
    assert spec.normalize_volume(999.0) == spec.volume_max


def test_volume_below_broker_minimum_is_rejected_not_rounded_up():
    client = make_client(FakeMT5())
    client.discover_symbol()
    result = client.market_order("buy", volume=0.004, sl=2640.0, tp=2670.0)
    assert result.ok is False
    assert "below the broker minimum" in result.comment


# --- stop validation --------------------------------------------------------


def test_rejects_stop_on_wrong_side_of_price():
    """A stop above entry on a buy is a strategy bug; it must surface loudly."""
    client = make_client(FakeMT5())
    client.discover_symbol()
    with pytest.raises(MT5Error, match="must be below entry"):
        client.validate_stops("buy", entry=2650.0, sl=2660.0, tp=2670.0)
    with pytest.raises(MT5Error, match="must be above entry"):
        client.validate_stops("sell", entry=2650.0, sl=2640.0, tp=2630.0)


def test_rejects_stop_inside_broker_minimum_distance():
    fake = FakeMT5(symbols={"XAUUSD": FakeSymbol("XAUUSD", trade_stops_level=50)})
    client = make_client(fake)
    client.discover_symbol()
    # 50 points x 0.01 = $0.50 minimum distance; $0.20 is too close.
    with pytest.raises(MT5Error, match="minimum stop distance"):
        client.validate_stops("buy", entry=2650.0, sl=2649.8, tp=2670.0)
    client.validate_stops("buy", entry=2650.0, sl=2645.0, tp=2670.0)  # fine


# --- order execution --------------------------------------------------------


def test_market_order_attaches_server_side_stop():
    """The stop must reach the broker, so a crashed bot still has protection."""
    fake = FakeMT5()
    client = make_client(fake)
    client.discover_symbol()

    result = client.market_order("buy", volume=0.12, sl=2645.0, tp=2660.0)

    assert result.ok is True
    assert result.ticket is not None
    sent = fake.sent_requests[-1]
    assert sent["sl"] == 2645.0
    assert sent["tp"] == 2660.0
    assert sent["magic"] == client.cfg.magic


def test_retries_after_requote():
    fake = FakeMT5()
    fake.order_script = [TRADE_RETCODE_REQUOTE, None]
    client = make_client(fake)
    client.discover_symbol()

    result = client.market_order("buy", volume=0.10, sl=2645.0, tp=2660.0)

    assert result.ok is True
    assert len(fake.sent_requests) == 2


def test_negotiates_filling_mode_and_remembers_it():
    """Brokers reject unsupported filling modes with 10030; we walk the list."""
    fake = FakeMT5()
    fake.order_script = [TRADE_RETCODE_INVALID_FILL, None]
    client = make_client(fake)
    client.discover_symbol()

    result = client.market_order("buy", volume=0.10, sl=2645.0, tp=2660.0)
    assert result.ok is True
    first_mode = fake.sent_requests[0]["type_filling"]
    accepted_mode = fake.sent_requests[1]["type_filling"]
    assert first_mode != accepted_mode

    # The accepted mode is reused, so later orders do not re-probe.
    fake.sent_requests.clear()
    client.market_order("buy", volume=0.10, sl=2645.0, tp=2660.0)
    assert fake.sent_requests[0]["type_filling"] == accepted_mode


def test_structural_rejection_does_not_retry():
    """No money / market closed must fail fast, not hammer the trade server."""
    from goldbot.mt5_client import RETCODE_NO_MONEY

    fake = FakeMT5()
    fake.order_script = [RETCODE_NO_MONEY, None]
    client = make_client(fake)
    client.discover_symbol()

    result = client.market_order("buy", volume=0.10, sl=2645.0, tp=2660.0)
    assert result.ok is False
    assert "insufficient free margin" in result.reason
    assert len(fake.sent_requests) == 1


# --- position ownership -----------------------------------------------------


def test_only_sees_its_own_positions():
    """Trades placed by hand must be invisible to the bot."""
    fake = FakeMT5()
    client = make_client(fake, magic=777)
    client.discover_symbol()

    client.market_order("buy", volume=0.10, sl=2645.0, tp=2660.0)
    # A position opened by the human, carrying a different magic number.
    fake._positions.append(
        type(fake._positions[0])(
            ticket=9999, symbol="XAUUSD", type=0, volume=1.0, price_open=2600.0,
            sl=0.0, tp=0.0, profit=0.0, magic=0, comment="manual", time=0,
        )
    )

    tickets = [p.ticket for p in client.positions()]
    assert 9999 not in tickets
    assert len(tickets) == 1
    assert len(client.positions(all_magics=True)) == 2


def test_close_all_flattens_only_bot_positions():
    fake = FakeMT5()
    client = make_client(fake)
    client.discover_symbol()
    client.market_order("buy", volume=0.10, sl=2645.0, tp=2660.0)
    client.market_order("sell", volume=0.10, sl=2655.0, tp=2640.0)

    results = client.close_all()
    assert all(r.ok for r in results)
    assert client.positions() == []


def test_partial_close_leaves_remainder_open():
    """Partial closes are how the Trade Manager banks profit and lets the rest run."""
    fake = FakeMT5()
    client = make_client(fake)
    client.discover_symbol()
    client.market_order("buy", volume=0.10, sl=2645.0, tp=2660.0)
    ticket = client.positions()[0].ticket

    result = client.close_position(ticket, volume=0.04)

    assert result.ok is True
    remaining = client.positions()[0]
    assert remaining.volume == pytest.approx(0.06)


def test_modify_position_moves_stop_to_breakeven():
    fake = FakeMT5()
    client = make_client(fake)
    client.discover_symbol()
    client.market_order("buy", volume=0.10, sl=2645.0, tp=2660.0)
    pos = client.positions()[0]

    result = client.modify_position(pos.ticket, sl=pos.open_price)

    assert result.ok is True
    assert client.positions()[0].sl == pytest.approx(pos.open_price)


def test_cannot_modify_a_position_it_does_not_own():
    fake = FakeMT5()
    client = make_client(fake)
    client.discover_symbol()
    result = client.modify_position(4242, sl=2600.0)
    assert result.ok is False
    assert "not found" in result.comment


# --- connection -------------------------------------------------------------


def test_connect_failure_explains_the_usual_causes():
    fake = FakeMT5()
    fake.init_should_fail = True
    with pytest.raises(MT5Error, match="Algo Trading"):
        make_client(fake).connect()


def test_is_connected_never_raises():
    """The watchdog depends on this being safe even when everything else is broken."""
    client = make_client(FakeMT5())
    assert client.is_connected() is False
    client.connect()
    assert client.is_connected() is True


def test_closed_bars_exclude_the_forming_bar():
    """Acting on a forming bar is the classic live/backtest divergence."""
    client = make_client(FakeMT5())
    client.discover_symbol()
    assert len(client.get_bars("M15", 50)) == 50
    assert len(client.get_closed_bars("M15", 50)) == 50
    all_bars = client.get_bars("M15", 51)
    closed = client.get_closed_bars("M15", 50)
    assert closed.index[-1] < all_bars.index[-1]


class TestPointValue:
    """The multiplier every position size is divided by.

    Getting it wrong mis-sizes every trade by that factor, silently. An
    understated value oversizes, so the account risks a multiple of what the
    Risk Warden authorised while every number on screen still reads 1%.

    These reproduce a real MetaQuotes demo that quoted 0.10 GBP per point on a
    100oz gold contract whose true value was about 0.79 — an 8x oversize.
    """

    def client(self, fake):
        c = MT5Client(MT5Config(), mt5_module=fake)
        c.connect()
        c.discover_symbol()
        return c

    def test_the_brokers_calculator_wins_over_a_wrong_tick_value(self):
        fake = FakeMT5()
        fake.symbols["XAUUSD"].trade_tick_value = 0.10   # what the demo claimed
        fake.profit_rate = 0.79                          # GBP per USD
        spec = self.client(fake).symbol_spec()
        # 100 oz x 0.01 x 0.79 = 0.79 GBP per point, not the 0.10 quoted.
        assert spec.value_per_point_per_lot == pytest.approx(0.79, abs=1e-9)
        assert spec.tick_value_per_point == pytest.approx(0.10)

    def test_falls_back_to_tick_value_when_the_calculator_is_unavailable(self):
        fake = FakeMT5()
        fake.calc_profit_available = False
        fake.symbols["XAUUSD"].trade_tick_value = 1.0
        spec = self.client(fake).symbol_spec()
        assert spec.value_per_point_per_lot == pytest.approx(1.0)

    def test_sanity_check_passes_on_a_normal_usd_account(self):
        fake = FakeMT5()
        ok, why = self.client(fake).contract_sanity()
        assert ok, why

    def test_sanity_check_rejects_a_value_that_contradicts_the_contract(self):
        # Same currency, so the two must agree — and 0.10 against 1.00 does not.
        fake = FakeMT5()
        fake.calc_profit_available = False
        fake.symbols["XAUUSD"].trade_tick_value = 0.10
        ok, why = self.client(fake).contract_sanity()
        assert not ok
        assert "disagrees" in why and "ratio" in why

    def test_sanity_check_accepts_a_genuine_fx_conversion(self):
        fake = FakeMT5()
        fake.profit_rate = 0.79
        fake.account_currency = "GBP"
        ok, why = self.client(fake).contract_sanity()
        assert ok, why
        assert "plausible" in why

    def test_sanity_check_rejects_an_implausible_rate(self):
        fake = FakeMT5()
        fake.calc_profit_available = False
        fake.account_currency = "GBP"
        fake.symbols["XAUUSD"].trade_tick_value = 0.0001
        ok, why = self.client(fake).contract_sanity()
        assert not ok
        assert "not a" in why and "exchange rate" in why

    def test_a_configured_override_beats_every_other_source(self):
        # The escape hatch for a broker whose own numbers contradict each other.
        fake = FakeMT5()
        fake.profit_rate = 0.79
        c = MT5Client(MT5Config(value_per_point_override=0.85), mt5_module=fake)
        c.connect()
        c.discover_symbol()
        assert c.symbol_spec().value_per_point_per_lot == pytest.approx(0.85)
