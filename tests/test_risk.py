"""Risk Warden tests.

The sizing numbers here are computed by hand in the comments, against the
account this bot is actually being built for: $5,000, 1% per trade, standard
100oz gold. If any of these ever change silently, every position the bot opens
is the wrong size.

Every veto has its own test. A risk limit that is never exercised in a test is
a risk limit you find out about during a drawdown.
"""

from __future__ import annotations

import pytest

from goldbot.config import RiskConfig
from goldbot.mt5_client import SymbolSpec
from goldbot.risk import RiskState, RiskWarden
from goldbot.strategy.base import Signal


def spec(**overrides) -> SymbolSpec:
    """A standard 100oz XAUUSD contract: 1 point (0.01) = $1.00 per lot."""
    base = dict(
        name="XAUUSD",
        digits=2,
        point=0.01,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level_points=0,
        freeze_level_points=0,
        tick_value=1.0,
        tick_size=0.01,
        filling_modes=1,
        currency_profit="USD",
    )
    base.update(overrides)
    return SymbolSpec(**base)


def signal(stop_distance: float = 4.0, direction: str = "buy") -> Signal:
    price = 2000.0
    if direction == "buy":
        stop, target = price - stop_distance, price + stop_distance * 2
    else:
        stop, target = price + stop_distance, price - stop_distance * 2
    return Signal(
        time=None,
        direction=direction,
        reference_price=price,
        stop=stop,
        target=target,
        atr=stop_distance / 1.5,
        reason="test",
    )


def state(**overrides) -> RiskState:
    base = dict(
        equity=5000.0,
        balance=5000.0,
        starting_balance=5000.0,
        day_start_equity=5000.0,
    )
    base.update(overrides)
    return RiskState(**base)


@pytest.fixture
def warden():
    return RiskWarden(RiskConfig())


class TestSizing:
    def test_the_headline_case(self, warden):
        #   risk budget  = $5,000 x 1%          = $50.00
        #   stop         = $4.00 = 400 points
        #   point value  = $1.00 per lot
        #   raw lots     = 50 / (400 x 1)       = 0.125
        #   floored      =                        0.12 lots
        #   actual risk  = 0.12 x 400 x 1       = $48.00  (0.96% of equity)
        decision = warden.evaluate(signal(), spec(), state())
        assert decision.approved
        assert decision.volume == pytest.approx(0.12)
        assert decision.risk_amount == pytest.approx(48.0)
        assert decision.risk_pct == pytest.approx(0.96)

    def test_lots_are_floored_never_rounded_up(self, warden):
        # A stop of $3.00 gives 50/300 = 0.1667 lots. Rounding to 0.17 would risk
        # $51 against a $50 budget: over-risking by rounding is still over-risking.
        decision = warden.evaluate(signal(stop_distance=3.0), spec(), state())
        assert decision.volume == pytest.approx(0.16)
        assert decision.risk_amount <= 50.0

    def test_wider_stop_gives_smaller_position_for_the_same_risk(self, warden):
        tight = warden.evaluate(signal(stop_distance=4.0), spec(), state())
        wide = warden.evaluate(signal(stop_distance=8.0), spec(), state())
        assert wide.volume < tight.volume
        assert wide.risk_amount <= 50.0 and tight.risk_amount <= 50.0

    def test_shorts_size_identically_to_longs(self, warden):
        long = warden.evaluate(signal(direction="buy"), spec(), state())
        short = warden.evaluate(signal(direction="sell"), spec(), state())
        assert long.volume == short.volume

    def test_uses_broker_tick_value_not_the_contract_size(self):
        # Some brokers quote gold in cents-per-tick. If the warden ignored
        # tick_value it would size 10x wrong and never notice. Run with a raised
        # lot ceiling so this measures the tick maths, not the clamp.
        loose = RiskWarden(RiskConfig(max_lot=5.0))
        decision = loose.evaluate(signal(), spec(tick_value=0.1), state())
        assert decision.volume == pytest.approx(1.25)
        assert decision.risk_amount == pytest.approx(50.0)

    def test_bigger_account_scales_the_position(self, warden):
        decision = warden.evaluate(signal(), spec(), state(equity=20000.0))
        # $200 budget / 400 points = 0.5 lots exactly.
        assert decision.volume == pytest.approx(0.5)


class TestHalts:
    def test_manual_halt_blocks_everything(self, warden):
        st = state(halted=True, halt_reason="kill switch pulled from Telegram")
        assert warden.halt_reason(st) == "kill switch pulled from Telegram"
        assert warden.evaluate(signal(), spec(), st).rejected

    def test_equity_floor_is_a_permanent_halt(self, warden):
        # Floor is 80% of the $5,000 starting balance = $4,000.
        st = state(equity=3999.0, balance=3999.0)
        reason = warden.halt_reason(st)
        assert "equity floor" in reason and "permanent halt" in reason

    def test_equity_just_above_the_floor_still_trades(self, warden):
        assert warden.halt_reason(state(equity=4001.0, balance=4001.0)) is None

    def test_daily_loss_limit_halts_the_day(self, warden):
        # 4% of $5,000 start-of-day equity = $200.
        assert warden.halt_reason(state(realised_pnl_today=-199.0)) is None
        reason = warden.halt_reason(state(realised_pnl_today=-200.0))
        assert "daily loss" in reason

    def test_daily_limit_uses_start_of_day_equity_not_current(self, warden):
        # Equity has fallen to 4,800 but the day started at 5,000: the budget is
        # still $200, so a $150 loss has not yet exhausted it.
        st = state(equity=4850.0, day_start_equity=5000.0, realised_pnl_today=-150.0)
        assert warden.halt_reason(st) is None

    def test_consecutive_losses_halt_pending_manual_resume(self, warden):
        assert warden.halt_reason(state(consecutive_losses=4)) is None
        reason = warden.halt_reason(state(consecutive_losses=5))
        assert "consecutive losses" in reason and "/resume" in reason


class TestVetoes:
    def test_position_cap(self, warden):
        decision = warden.evaluate(signal(), spec(), state(open_positions=2))
        assert decision.rejected and "limit is 2" in decision.reason

    def test_daily_trade_cap(self, warden):
        decision = warden.evaluate(signal(), spec(), state(trades_today=10))
        assert decision.rejected and "daily cap" in decision.reason

    def test_spread_gate(self, warden):
        decision = warden.evaluate(signal(), spec(), state(spread_points=31))
        assert decision.rejected and "spread" in decision.reason
        assert warden.evaluate(signal(), spec(), state(spread_points=30)).approved

    def test_stop_below_the_configured_minimum(self, warden):
        # 0.50 of gold is 50 points, under the 100-point floor.
        decision = warden.evaluate(signal(stop_distance=0.5), spec(), state())
        assert decision.rejected and "below the 100-point minimum" in decision.reason

    def test_stop_inside_the_brokers_stops_level(self, warden):
        # The broker would reject the order outright (retcode 10016); catching it
        # here means one fewer failed order in the log and no unprotected fill.
        decision = warden.evaluate(signal(stop_distance=2.0), spec(stops_level_points=250), state())
        assert decision.rejected and "invalid stops" in decision.reason

    def test_position_smaller_than_the_broker_minimum_is_skipped(self, warden):
        # A $50 budget over a $40 stop is 0.0125 lots. The broker's minimum 0.05
        # would risk $200 — four times the budget. Skip, never round up.
        decision = warden.evaluate(signal(stop_distance=40.0), spec(volume_min=0.05), state())
        assert decision.rejected
        assert "below the broker minimum" in decision.reason
        assert "skipping the trade instead of over-risking" in decision.reason

    def test_lot_ceiling_clamps_rather_than_rejects(self, warden):
        big = state(
            equity=500_000.0,
            balance=500_000.0,
            starting_balance=500_000.0,
            day_start_equity=500_000.0,
        )
        # 1% of 500k over a 400-point stop is 12.5 lots; the ceiling is 1.0.
        decision = warden.evaluate(signal(), spec(), big)
        assert decision.approved
        assert decision.volume == pytest.approx(1.0)
        assert any("ceiling" in n for n in decision.notes)

    def test_unusable_contract_spec_is_a_veto_not_a_guess(self, warden):
        broken = spec(tick_value=0.0, tick_size=0.0, contract_size=0.0)
        decision = warden.evaluate(signal(), broken, state())
        assert decision.rejected and "refusing" in decision.reason

    def test_insufficient_margin(self, warden):
        decision = warden.evaluate(
            signal(), spec(), state(margin_free=50.0), margin_per_lot=2000.0
        )
        assert decision.rejected and "margin" in decision.reason

    def test_margin_check_is_skipped_when_unknown(self, warden):
        assert warden.evaluate(signal(), spec(), state(margin_free=50.0)).approved


class TestDailyBudget:
    def test_open_positions_count_against_the_days_budget(self, warden):
        # $200 daily budget, $120 already lost and $60 at risk in an open trade
        # leaves $20 — so the new trade is shrunk to fit rather than taken full size.
        st = state(realised_pnl_today=-120.0, open_risk_amount=60.0, open_positions=1)
        assert warden.remaining_daily_budget(st) == pytest.approx(20.0)
        decision = warden.evaluate(signal(), spec(), st)
        assert decision.approved
        assert decision.risk_amount <= 20.0
        assert any("loss budget" in n for n in decision.notes)

    def test_exhausted_budget_rejects(self, warden):
        st = state(realised_pnl_today=-100.0, open_risk_amount=100.0, open_positions=1)
        decision = warden.evaluate(signal(), spec(), st)
        assert decision.rejected and "remaining loss budget" in decision.reason

    def test_intraday_equity_growth_cannot_outrun_the_days_budget(self, warden):
        # Equity has run from 5,000 to 500,000 within the day (a contrived case,
        # but the same shape as a big winner mid-session). Per-trade sizing would
        # ask for 12.5 lots; the day's $200 loss allowance still binds, so the
        # position is sized to that instead.
        st = state(equity=500_000.0, balance=500_000.0, day_start_equity=5000.0)
        decision = warden.evaluate(signal(), spec(), st)
        assert decision.approved
        assert decision.risk_amount == pytest.approx(200.0)
        assert decision.volume == pytest.approx(0.5)

    def test_profits_do_not_enlarge_the_days_budget(self, warden):
        # Being up on the day must not licence a bigger bet: the per-trade cap
        # still binds. This is the rule that stops a good morning funding a
        # catastrophic afternoon.
        decision = warden.evaluate(signal(), spec(), state(realised_pnl_today=+500.0))
        assert decision.risk_amount == pytest.approx(48.0)


class TestAgentAuthority:
    def test_agents_may_shrink_a_position(self, warden):
        decision = warden.evaluate(signal(), spec(), state(), size_multiplier=0.5)
        assert decision.approved
        # $25 budget / 400 points = 0.0625 -> floored to 0.06 lots = $24.00.
        assert decision.volume == pytest.approx(0.06)
        assert any("multiplier" in n for n in decision.notes)

    def test_agents_may_not_enlarge_a_position(self, warden):
        # Not clamped to 1.0 — rejected, so it shows up in the journal as the
        # anomaly it is. An agent asking to size up is a bug or an injection.
        decision = warden.evaluate(signal(), spec(), state(), size_multiplier=1.5)
        assert decision.rejected
        assert "may only reduce risk" in decision.reason

    def test_zero_multiplier_is_a_veto(self, warden):
        assert warden.evaluate(signal(), spec(), state(), size_multiplier=0.0).rejected


class TestInvariants:
    @pytest.mark.parametrize("stop_distance", [1.5, 2.0, 3.3, 4.0, 7.5, 12.0, 25.0])
    @pytest.mark.parametrize("equity", [1000.0, 5000.0, 12345.0, 50000.0])
    def test_realised_risk_never_exceeds_the_budget(self, warden, stop_distance, equity):
        st = state(equity=equity, balance=equity, day_start_equity=equity)
        decision = warden.evaluate(signal(stop_distance=stop_distance), spec(), st)
        if decision.approved:
            budget = equity * 0.01
            assert decision.risk_amount <= budget + 1e-9, (
                f"{decision.volume} lots risks {decision.risk_amount} against a {budget} budget"
            )

    def test_approved_volume_is_always_on_the_brokers_step(self, warden):
        for equity in (900.0, 5000.0, 33333.0):
            st = state(equity=equity, balance=equity, day_start_equity=equity)
            decision = warden.evaluate(signal(stop_distance=3.7), spec(), st)
            if decision.approved:
                assert abs(round(decision.volume / 0.01) * 0.01 - decision.volume) < 1e-9
