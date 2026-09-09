"""Config schema tests.

Two things are being protected here.

First, the **cross-field validators**. Each one exists because the combination it
rejects is a plausible typo that would only reveal itself with money on the line
— a daily cap below the per-trade risk, a stop-trailing rule that widens risk, a
Telegram kill switch that was never actually wired up.

Second, the **agent model defaults**. Model choice is a deliberate decision, not
an incidental one, and a silent downgrade would be invisible in a diff review
and invisible at runtime — the bot would keep trading, just with worse judgement
behind every veto. These tests make that change loud.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from goldbot.config import (
    LIVE_CONFIRM_PHRASE,
    AgentsConfig,
    Config,
    RiskConfig,
    StrategyConfig,
    TelegramConfig,
    load_config,
)

AGENTS = (
    "session_supervisor",
    "news_scout",
    "regime_analyst",
    "decision",
    "devils_advocate",
    "trade_manager",
    "day_auditor",
    "reviewer",
)


class TestAgentDefaults:
    def test_every_agent_runs_on_opus_5(self):
        agents = AgentsConfig()
        for name in AGENTS:
            assert getattr(agents, name).model == "claude-opus-5", (
                f"{name} is not on Opus 5. Every agent either reads ambiguous "
                "information or touches a money decision — both are what the "
                "cheaper tiers are worse at."
            )

    def test_money_decisions_get_the_higher_effort(self):
        # The agent that sets the day plan, the two deciding whether a trade
        # happens, and the weekly review that proposes parameter changes: the
        # four whose output is hardest to check after the fact.
        agents = AgentsConfig()
        for name in ("session_supervisor", "decision", "devils_advocate", "reviewer"):
            assert getattr(agents, name).effort == "xhigh"

    def test_remaining_agents_run_at_high_effort(self):
        agents = AgentsConfig()
        for name in ("news_scout", "regime_analyst", "trade_manager", "day_auditor"):
            assert getattr(agents, name).effort == "high"

    def test_the_day_is_bracketed_by_one_plan_and_one_audit(self):
        # Supervisor output lasts the whole day; the auditor runs once after it.
        agents = AgentsConfig()
        assert agents.session_supervisor.cache_ttl_s >= 12 * 3600
        assert agents.day_auditor.max_tokens >= 4000

    def test_agents_are_disabled_until_deliberately_enabled(self):
        agents = AgentsConfig()
        assert not any(getattr(agents, name).enabled for name in AGENTS)

    def test_agents_fail_closed_by_default(self):
        # Missing a trade is free. An unreviewed trade is not.
        assert AgentsConfig().fail_closed is True

    def test_the_api_key_is_only_ever_an_env_var_name(self):
        agents = AgentsConfig()
        assert agents.api_key_env == "ANTHROPIC_API_KEY"
        assert not hasattr(agents, "api_key")

    def test_a_daily_cost_ceiling_exists(self):
        # On a small account API spend is a real drag on returns, so an unbounded
        # bill must not be possible by omission.
        assert AgentsConfig().daily_cost_limit_usd > 0


class TestLiveTradingGate:
    def test_dry_run_is_the_shipped_default(self):
        assert Config().dry_run is True
        assert Config().live_orders_armed is False

    def test_live_needs_both_opt_ins(self):
        with pytest.raises(ValidationError, match="live_confirm"):
            Config(dry_run=False)
        with pytest.raises(ValidationError, match="live_confirm"):
            Config(dry_run=False, live_confirm="yes")

        armed = Config(dry_run=False, live_confirm=LIVE_CONFIRM_PHRASE)
        assert armed.live_orders_armed is True

    def test_the_phrase_alone_does_not_arm_anything(self):
        assert Config(dry_run=True, live_confirm=LIVE_CONFIRM_PHRASE).live_orders_armed is False


class TestRiskValidators:
    def test_risk_above_the_hard_ceiling_is_refused(self):
        with pytest.raises(ValidationError):
            RiskConfig(risk_per_trade_pct=6.0)

    def test_daily_cap_must_exceed_per_trade_risk(self):
        # Otherwise one ordinary losing trade halts the day.
        with pytest.raises(ValidationError, match="daily_loss_limit_pct"):
            Config(risk=RiskConfig(risk_per_trade_pct=4.0, daily_loss_limit_pct=4.0))

    def test_concurrent_risk_cannot_exceed_the_daily_cap(self):
        # Three positions at 2% each is 6% simultaneously against a 5% cap: one
        # adverse move blows through the limit before it can halt anything.
        with pytest.raises(ValidationError, match="max_open_positions"):
            Config(risk=RiskConfig(
                risk_per_trade_pct=2.0, max_open_positions=3, daily_loss_limit_pct=5.0
            ))

    def test_equity_floor_must_leave_room_for_one_days_loss(self):
        with pytest.raises(ValidationError, match="equity_floor_pct"):
            Config(risk=RiskConfig(equity_floor_pct=97.0, daily_loss_limit_pct=4.0))


class TestStrategyValidators:
    def test_emas_must_be_strictly_increasing(self):
        with pytest.raises(ValidationError, match="strictly increasing"):
            Config(strategy=StrategyConfig(fast_ema=50, trend_ema=20, slow_ema=200))

    def test_breakeven_must_fire_before_the_target(self):
        with pytest.raises(ValidationError, match="breakeven_at_r"):
            Config(strategy=StrategyConfig(breakeven_at_r=2.0, tp_r_multiple=2.0))

    def test_trailing_must_not_start_before_breakeven(self):
        # Trailing from below break-even would widen risk, not reduce it.
        with pytest.raises(ValidationError, match="trail_after_r"):
            Config(strategy=StrategyConfig(breakeven_at_r=1.5, trail_after_r=1.0))

    def test_momentum_window_cannot_be_empty(self):
        with pytest.raises(ValidationError, match="rsi_extreme"):
            Config(strategy=StrategyConfig(rsi_midline=70.0, rsi_extreme=60.0))

    def test_both_scalping_timeframes_are_accepted(self):
        for timeframe in ("M5", "M15"):
            assert StrategyConfig(timeframe=timeframe).timeframe == timeframe

    def test_an_unsupported_timeframe_is_refused(self):
        with pytest.raises(ValidationError):
            StrategyConfig(timeframe="M3")


class TestRoutineValidators:
    def test_defaults_are_consistent(self):
        cfg = Config()
        assert cfg.routine.enabled
        assert cfg.sessions.flatten_daily

    def test_preflight_must_leave_room_before_the_open(self):
        # 07:50 for an 08:00 open is ten minutes for a web search and a regime
        # read — not enough, and a plan produced in a hurry is a bad plan.
        from goldbot.config import RoutineConfig
        with pytest.raises(ValidationError, match="preflight"):
            Config(routine=RoutineConfig(preflight="07:50"))

    def test_preflight_after_the_open_is_refused(self):
        from goldbot.config import RoutineConfig
        with pytest.raises(ValidationError, match="preflight"):
            Config(routine=RoutineConfig(preflight="09:00"))

    def test_daily_close_must_follow_the_last_window(self):
        from goldbot.config import SessionConfig, SessionWindow
        with pytest.raises(ValidationError, match="daily_close"):
            SessionConfig(
                windows=(SessionWindow(name="w", start="08:00", end="17:00"),),
                daily_close="16:00",
            )

    def test_daily_close_is_not_checked_when_daily_flatten_is_off(self):
        from goldbot.config import SessionConfig, SessionWindow
        cfg = SessionConfig(
            windows=(SessionWindow(name="w", start="00:00", end="23:59"),),
            flatten_daily=False,
            daily_close="19:30",
        )
        assert not cfg.flatten_daily


class TestTelegram:
    def test_enabled_without_credentials_is_refused(self):
        # A kill switch you believe exists but does not is worse than none.
        with pytest.raises(ValidationError, match="bot_token"):
            Config(telegram=TelegramConfig(enabled=True))

    def test_enabled_with_credentials_is_accepted(self):
        cfg = Config(telegram=TelegramConfig(enabled=True, bot_token="t", chat_id="123"))
        assert cfg.telegram.enabled


class TestTypoSafety:
    def test_an_unknown_risk_key_is_an_error_not_a_silent_default(self):
        # `extra="forbid"`. A misspelled risk key that silently fell back to the
        # default would mean trading with limits you believe you changed.
        with pytest.raises(ValidationError):
            RiskConfig(risk_per_trade_percent=1.0)

    def test_config_objects_are_immutable(self):
        cfg = Config()
        with pytest.raises(ValidationError):
            cfg.dry_run = False


class TestSecretsHandling:
    def test_env_vars_are_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOLDBOT_TEST_PW", "s3cret")
        path = tmp_path / "c.yaml"
        path.write_text("mt5:\n  password: ${GOLDBOT_TEST_PW}\n")
        assert load_config(path).mt5.password == "s3cret"

    def test_an_unset_env_var_is_an_error_not_an_empty_string(self, tmp_path, monkeypatch):
        # Silently becoming "" would surface much later as a puzzling broker
        # login failure, with nothing pointing at the config.
        monkeypatch.delenv("GOLDBOT_TEST_MISSING", raising=False)
        path = tmp_path / "c.yaml"
        path.write_text("mt5:\n  password: ${GOLDBOT_TEST_MISSING}\n")
        with pytest.raises(ValueError, match="GOLDBOT_TEST_MISSING"):
            load_config(path)

    def test_a_missing_config_says_what_to_do(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="config.example.yaml"):
            load_config(tmp_path / "nope.yaml")

    def test_the_shipped_example_config_loads_with_no_environment_at_all(self, monkeypatch):
        """A fresh copy of the example must work before anything is configured.

        This previously set all three variables, which hid a real bug: the
        example referenced ${TELEGRAM_BOT_TOKEN} while Telegram was disabled, so
        copying it and running the connection check failed on a credential for a
        feature that was off. Credentials now ship as null and are filled in
        when each feature is turned on.
        """
        for var in ("MT5_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            monkeypatch.delenv(var, raising=False)
        cfg = load_config("config/config.example.yaml")
        assert cfg.dry_run is True
        assert cfg.live_orders_armed is False
        assert not any(getattr(cfg.agents, name).enabled for name in AGENTS)

    def test_the_example_references_no_unset_variables(self, monkeypatch):
        # Every ${VAR} in the shipped example must belong to a feature that is
        # switched on; anything else is a trap for a first-time setup.
        for var in ("MT5_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            monkeypatch.delenv(var, raising=False)
        text = open("config/config.example.yaml").read()
        active = [
            line for line in text.splitlines()
            if "${" in line and not line.lstrip().startswith("#")
        ]
        assert active == [], f"example config references env vars on live lines: {active}"

    def test_the_example_config_contains_no_literal_secrets(self):
        # It is committed. Anything that looks like a real credential in here is
        # a credential that has been published.
        text = open("config/config.example.yaml").read()
        for line in text.splitlines():
            key = line.strip()
            if key.startswith(("password:", "bot_token:", "chat_id:")):
                value = key.split(":", 1)[1].strip()
                assert value.startswith("${") or value == "null", (
                    f"{key!r} holds a literal value; secrets belong in env vars"
                )
