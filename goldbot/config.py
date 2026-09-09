"""Configuration schema and loader.

Every tunable number in the system lives here and nowhere else. The schema is
validated with pydantic at startup: the bot refuses to run on a config that is
internally inconsistent or dangerous, rather than discovering the problem with
real money on the line.

Design rules enforced by this module:

* Secrets are never committed. ``config/config.yaml`` is gitignored; only
  ``config/config.example.yaml`` is in version control.
* Live trading requires two independent opt-ins (``dry_run: false`` *and* the
  exact ``live_confirm`` phrase). One typo cannot arm real money.
* ``weekly_target_pct`` is a REPORTING benchmark only. Nothing in the trading
  path reads it, so a return goal can never push the bot into over-trading to
  "catch up".
"""

from __future__ import annotations

import os
import re
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# The exact phrase that must appear in config to permit real-money orders.
LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THIS_TRADES_REAL_MONEY"

# Absolute ceiling on risk per trade. Not configurable: a value above this is
# treated as a typo or a moment of bad judgement, and the bot refuses to start.
MAX_ALLOWED_RISK_PCT = 5.0

_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MT5Config(_Base):
    """Connection details for the MetaTrader 5 terminal."""

    login: int | None = Field(
        default=None, description="Demo/live account number. None = use the terminal's current login."
    )
    password: str | None = Field(default=None, description="Account password (supports ${ENV_VAR}).")
    server: str | None = Field(default=None, description="Broker server name, e.g. 'ICMarkets-Demo'.")
    terminal_path: str | None = Field(
        default=None, description="Path to terminal64.exe. None = MT5 auto-discovers it."
    )

    symbol: str | None = Field(
        default=None,
        description="Explicit gold symbol. None = auto-discover from symbol_candidates.",
    )
    symbol_candidates: tuple[str, ...] = Field(
        default=(
            "XAUUSD",
            "XAUUSD.m",
            "XAUUSDm",
            "XAUUSD.raw",
            "XAUUSD_o",
            "XAUUSD.a",
            "XAUUSDc",
            "GOLD",
            "GOLD.m",
            "Gold",
        ),
        description="Probed in order; the first tradable match wins. Brokers name gold differently.",
    )

    magic: int = Field(
        default=20260907,
        ge=1,
        description="Magic number tagging our orders. The bot only ever touches positions "
        "carrying this number, so your manual trades are invisible to it.",
    )
    deviation_points: int = Field(
        default=20, ge=0, le=500, description="Max slippage tolerated on market orders, in points."
    )
    value_per_point_override: float | None = Field(
        default=None,
        gt=0,
        description="Account-currency value of a 1-point move on 1.00 lot. Leave null: the "
        "bot asks the broker's own calculator. Set it ONLY when check_connection reports "
        "that the broker's contract specification is self-contradictory, and only to a "
        "value you have verified — it overrides every position size in the system.",
    )
    connect_timeout_s: float = Field(default=30.0, gt=0)


class RiskConfig(_Base):
    """Hard risk limits. The Risk Warden enforces these; no agent can override them."""

    risk_per_trade_pct: float = Field(
        default=1.0,
        gt=0,
        le=MAX_ALLOWED_RISK_PCT,
        description="Percent of equity risked per trade, measured to the stop loss.",
    )
    daily_loss_limit_pct: float = Field(
        default=4.0,
        gt=0,
        le=20.0,
        description="Realised loss vs start-of-day equity that halts trading for the day.",
    )
    equity_floor_pct: float = Field(
        default=80.0,
        gt=0,
        lt=100.0,
        description="Percent of starting balance below which the bot halts permanently "
        "until manually cleared.",
    )
    max_consecutive_losses: int = Field(
        default=5, ge=1, le=50, description="Halt and alert after this many losses in a row."
    )
    max_open_positions: int = Field(default=2, ge=1, le=10)
    max_lot: float = Field(
        default=1.0, gt=0, description="Absolute lot ceiling, whatever the sizing math says."
    )
    max_spread_points: int = Field(
        default=30,
        gt=0,
        description="Skip entries when the spread exceeds this. Gold spreads blow out around "
        "news and in the Asian session; this is the single most important scalping filter.",
    )
    max_daily_trades: int = Field(
        default=10, ge=1, le=200, description="Hard cap on entries per day. Anti-overtrading."
    )
    min_stop_points: int = Field(
        default=100,
        gt=0,
        description="Reject setups whose stop is tighter than this — spread noise would "
        "stop them out regardless of direction.",
    )


class StrategyConfig(_Base):
    """Parameters for the deterministic signal generator."""

    timeframe: Literal["M5", "M15", "M30", "H1"] = Field(
        default="M15",
        description="M15 is the default: gold's spread-to-move ratio is more survivable "
        "here than on M5.",
    )
    fast_ema: int = Field(default=20, ge=2, le=200)
    trend_ema: int = Field(default=50, ge=5, le=400)
    slow_ema: int = Field(default=200, ge=20, le=1000)
    atr_period: int = Field(default=14, ge=2, le=100)
    adx_period: int = Field(default=14, ge=2, le=100)
    rsi_period: int = Field(default=14, ge=2, le=100)
    min_adx: float = Field(
        default=20.0, ge=0, le=100, description="Below this, treat the market as rangebound."
    )
    pullback_lookback: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Bars back in which price must have touched the fast EMA for the setup "
        "to count as a pullback rather than a chase.",
    )
    rsi_midline: float = Field(
        default=50.0,
        ge=0,
        le=100,
        description="Momentum floor: longs need RSI above this, shorts below.",
    )
    rsi_extreme: float = Field(
        default=70.0,
        ge=50,
        le=100,
        description="Overbought ceiling for longs (mirrored to 100-x for shorts). Blocks "
        "entering a blow-off move, where the pullback stop is furthest away.",
    )
    use_structural_stop: bool = Field(
        default=True,
        description="Place the stop beyond the pullback swing when that is further than the "
        "ATR stop. Never inside the swing the setup is built on.",
    )
    atr_stop_multiple: float = Field(default=1.5, gt=0, le=10)
    tp_r_multiple: float = Field(default=2.0, gt=0, le=20, description="Take profit in R multiples.")
    breakeven_at_r: float | None = Field(
        default=1.0, gt=0, description="Move stop to entry once price reaches this R. None = off."
    )
    trail_after_r: float | None = Field(
        default=1.5, gt=0, description="Begin ATR trailing after this R. None = off."
    )
    trail_atr_multiple: float = Field(default=2.0, gt=0, le=10)
    one_entry_per_bar: bool = Field(default=True)


class SessionWindow(_Base):
    """A trading window in exchange-neutral wall clock time (see SessionConfig.timezone)."""

    name: str
    start: str = Field(description="HH:MM, 24h")
    end: str = Field(description="HH:MM, 24h")

    @model_validator(mode="after")
    def _check_times(self) -> "SessionWindow":
        for label, value in (("start", self.start), ("end", self.end)):
            if not _TIME_RE.match(value):
                raise ValueError(f"session window {self.name!r}: {label}={value!r} is not HH:MM")
        if self.start_time >= self.end_time:
            raise ValueError(
                f"session window {self.name!r}: start {self.start} must be before end {self.end}"
            )
        return self

    @property
    def start_time(self) -> dt_time:
        h, m = self.start.split(":")
        return dt_time(int(h), int(m))

    @property
    def end_time(self) -> dt_time:
        h, m = self.end.split(":")
        return dt_time(int(h), int(m))


class SessionConfig(_Base):
    """When the bot is allowed to open new positions."""

    timezone: str = Field(
        default="Europe/London", description="IANA timezone the windows below are expressed in."
    )
    windows: tuple[SessionWindow, ...] = Field(
        default=(
            SessionWindow(name="london", start="08:00", end="12:00"),
            SessionWindow(name="ny_overlap", start="13:30", end="17:00"),
        ),
        min_length=1,
    )
    trade_days: tuple[int, ...] = Field(
        default=(0, 1, 2, 3, 4),
        description="Weekdays the bot may enter trades (Mon=0 … Sun=6).",
    )
    flatten_daily: bool = Field(
        default=True,
        description="Close every position at daily_close, every trading day. The daily "
        "routine ends flat: no overnight holds, no Asian-session spread, and a clean "
        "day result to review. This is the intraday-scalper's discipline, and it makes "
        "every day's P&L independently attributable.",
    )
    daily_close: str = Field(
        default="19:30",
        description="HH:MM in `timezone`. Must fall after the last trading window ends.",
    )
    flatten_before_weekend: bool = Field(
        default=True, description="Close everything before the Friday close — no weekend gap risk."
    )
    friday_close: str = Field(
        default="20:00",
        description="HH:MM in `timezone`. Only matters when flatten_daily is off — with "
        "daily flattening on, Friday closes at daily_close like every other day.",
    )

    @model_validator(mode="after")
    def _check(self) -> "SessionConfig":
        for label, value in (("friday_close", self.friday_close), ("daily_close", self.daily_close)):
            if not _TIME_RE.match(value):
                raise ValueError(f"{label}={value!r} is not HH:MM")
        for day in self.trade_days:
            if not 0 <= day <= 6:
                raise ValueError(f"trade_days entry {day} out of range 0-6")
        if self.flatten_daily:
            last_end = max(w.end_time for w in self.windows)
            if self.daily_close_time <= last_end:
                raise ValueError(
                    f"daily_close {self.daily_close} must be after the last trading window "
                    f"ends ({last_end:%H:%M}); otherwise the bot flattens positions it has "
                    "only just opened."
                )
        return self

    @property
    def daily_close_time(self) -> dt_time:
        h, m = self.daily_close.split(":")
        return dt_time(int(h), int(m))

    @property
    def first_open_time(self) -> dt_time:
        return min(w.start_time for w in self.windows)


class RoutineConfig(_Base):
    """The daily schedule the agents run to.

    The day is a fixed sequence of phases, keyed to the London open in
    ``sessions.timezone``. Phases decide what is *permitted*; within a phase the
    engine is still event-driven. A fixed routine does two things a purely
    reactive design cannot: it makes the number of agent calls per day
    predictable (the API bill no longer scales with how noisy the market is),
    and it gives an unambiguous answer to "what is the bot allowed to do right
    now" at every moment of the day.

    PRE-FLIGHT → HUNT → HOLD → HUNT → WIND-DOWN → FLATTEN → DEBRIEF → CLOSED
    """

    enabled: bool = Field(default=True)
    preflight: str = Field(
        default="07:30",
        description="HH:MM in sessions.timezone. The Session Supervisor, News Scout and "
        "Regime Analyst run here and produce the day plan. Must be before the first "
        "trading window opens, with enough room for a web search to finish.",
    )
    preflight_min_lead_min: int = Field(
        default=15,
        ge=5,
        le=180,
        description="Minimum minutes between pre-flight and the first window. A plan "
        "produced 30 seconds before the open is a plan produced in a hurry.",
    )
    debrief_after_min: int = Field(
        default=15,
        ge=1,
        le=240,
        description="Minutes after daily_close the Day Auditor runs. It waits so that "
        "every fill has settled and the broker's own numbers can be reconciled.",
    )

    @model_validator(mode="after")
    def _check(self) -> "RoutineConfig":
        if not _TIME_RE.match(self.preflight):
            raise ValueError(f"preflight={self.preflight!r} is not HH:MM")
        return self

    @property
    def preflight_time(self) -> dt_time:
        h, m = self.preflight.split(":")
        return dt_time(int(h), int(m))


class CalendarConfig(_Base):
    """Deterministic economic-event blackouts. No LLM involved — timing is a lookup."""

    enabled: bool = Field(default=True)
    feed_url: str = Field(default="https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    refresh_hours: int = Field(
        default=24,
        ge=1,
        description="The feed rate-limits aggressive downloading, so it is cached on disk "
        "and refreshed at most this often.",
    )
    impacts: tuple[str, ...] = Field(
        default=("High",), description="Impact levels that trigger a blackout."
    )
    currencies: tuple[str, ...] = Field(
        default=("USD",), description="Gold is priced in USD; USD events move it most."
    )
    blackout_before_min: int = Field(default=15, ge=0, le=240)
    blackout_after_min: int = Field(default=15, ge=0, le=240)
    close_positions_before_event: bool = Field(
        default=False,
        description="If true, flatten open positions ahead of a high-impact event rather "
        "than merely blocking new entries.",
    )


class AgentConfig(_Base):
    """One LLM agent's model, cadence and failure policy."""

    enabled: bool = Field(default=False)
    model: str = Field(default="claude-opus-5")
    timeout_s: float = Field(default=45.0, gt=0, le=600)
    max_tokens: int = Field(default=4000, ge=256, le=64000)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = Field(default="high")
    min_interval_s: int = Field(
        default=0,
        ge=0,
        description="Floor between calls. Agents are event-driven; this is a cost backstop "
        "so a noisy trigger cannot run up the bill.",
    )
    cache_ttl_s: int = Field(
        default=3600,
        ge=0,
        description="How long this agent's output stays usable. Past it the output is treated "
        "as 'no view' — never silently reused as if fresh.",
    )


class AgentsConfig(_Base):
    """The agent roster.

    Every agent runs on Opus 5. Each one either reads ambiguous information or
    touches a money decision, and both are exactly what the cheaper tiers are
    worse at — a news brief that misreads a Fed statement costs more than the
    tokens it saved.

    Effort is raised to ``xhigh`` on the four agents whose output is hardest to
    check after the fact: the one that sets the day's plan, the two that decide
    whether a trade happens, and the weekly review that proposes parameter
    changes. The rest run at ``high``.

    Two agents bracket the trading day. The Session Supervisor runs once at
    pre-flight and sets the plan — normal, reduced size, or stand aside — with
    authority only to reduce. The Day Auditor runs once after the flatten and
    has no authority at all: it reconciles the day and writes the report. Between
    them the day has a beginning and an end, which is what makes each day's
    result independently attributable.

    Cost is controlled by *cadence*, not by model choice: agents are triggered by
    events, and ``daily_cost_limit_usd`` is the hard backstop. See the note on
    that field — on a small account, API spend is a real drag on returns.
    """

    api_key_env: str = Field(
        default="ANTHROPIC_API_KEY",
        description="Environment variable holding the Anthropic key. The key itself is never "
        "written to config.",
    )

    session_supervisor: AgentConfig = Field(
        default=AgentConfig(model="claude-opus-5", effort="xhigh", cache_ttl_s=86400)
    )
    news_scout: AgentConfig = Field(
        default=AgentConfig(model="claude-opus-5", effort="high", cache_ttl_s=3600)
    )
    regime_analyst: AgentConfig = Field(
        default=AgentConfig(model="claude-opus-5", effort="high", cache_ttl_s=14400)
    )
    decision: AgentConfig = Field(default=AgentConfig(model="claude-opus-5", effort="xhigh"))
    devils_advocate: AgentConfig = Field(
        default=AgentConfig(model="claude-opus-5", effort="xhigh")
    )
    trade_manager: AgentConfig = Field(
        default=AgentConfig(model="claude-opus-5", effort="high", min_interval_s=300)
    )
    day_auditor: AgentConfig = Field(
        default=AgentConfig(model="claude-opus-5", effort="high", max_tokens=8000, timeout_s=120.0)
    )
    reviewer: AgentConfig = Field(
        default=AgentConfig(model="claude-opus-5", effort="xhigh", max_tokens=16000)
    )

    daily_cost_limit_usd: float = Field(
        default=8.0,
        gt=0,
        description="Stop calling agents once the day's estimated API spend exceeds this. "
        "With all six on Opus 5 the realistic spend is roughly 4-8 USD/day, which on a "
        "5,000 USD account is 2.5-5% of equity per month in costs alone — a larger drag "
        "than most strategies produce in edge. Cadence, not model choice, is the lever: "
        "the trade manager's polling interval and the news scout's TTL dominate the bill. "
        "Note the interaction with fail_closed: once this cap trips, the agents stop "
        "answering and no new trade is approved for the rest of the day.",
    )
    fail_closed: bool = Field(
        default=True,
        description="API error, timeout, malformed output or a refusal ⇒ no trade. Missing a "
        "trade is free; an unreviewed trade is not.",
    )


class TelegramConfig(_Base):
    enabled: bool = Field(default=False)
    bot_token: str | None = Field(default=None, description="From @BotFather (supports ${ENV_VAR}).")
    chat_id: str | None = Field(default=None, description="From @userinfobot.")
    alert_on_trades: bool = Field(default=True)
    alert_on_errors: bool = Field(default=True)
    allow_commands: bool = Field(
        default=True, description="Enable /status /pause /resume /flat /pnl from your phone."
    )


class ReportingConfig(_Base):
    """Benchmarks and output paths. Nothing here is read by the trading path."""

    weekly_target_pct_min: float = Field(
        default=3.0,
        ge=0,
        description="Your stated weekly goal, reported against — never acted on. The trading "
        "logic cannot see this value.",
    )
    weekly_target_pct_max: float = Field(default=5.0, ge=0)
    journal_db: str = Field(default="data/journal.db")
    reports_dir: str = Field(default="reports")
    log_dir: str = Field(default="logs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    @model_validator(mode="after")
    def _check(self) -> "ReportingConfig":
        if self.weekly_target_pct_max < self.weekly_target_pct_min:
            raise ValueError("weekly_target_pct_max must be >= weekly_target_pct_min")
        return self


class Config(_Base):
    """Root configuration."""

    dry_run: bool = Field(
        default=True,
        description="True = full pipeline runs and journals decisions but sends NO orders. "
        "This is the shipped default and where you should spend your first days.",
    )
    live_confirm: str | None = Field(
        default=None,
        description=f"Must be exactly {LIVE_CONFIRM_PHRASE!r} to permit real orders. "
        "Second independent opt-in alongside dry_run.",
    )

    mt5: MT5Config = Field(default_factory=MT5Config)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    sessions: SessionConfig = Field(default_factory=SessionConfig)
    routine: RoutineConfig = Field(default_factory=RoutineConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)

    @model_validator(mode="after")
    def _cross_field_checks(self) -> "Config":
        risk = self.risk

        # Arming real money takes two independent, deliberate actions.
        if not self.dry_run and self.live_confirm != LIVE_CONFIRM_PHRASE:
            raise ValueError(
                "dry_run is false but live_confirm is not set to the exact phrase "
                f"{LIVE_CONFIRM_PHRASE!r}. Refusing to start: live trading requires two "
                "independent confirmations so a single typo cannot arm real money."
            )

        # A daily cap at or below single-trade risk means one normal loss halts the
        # day — almost always a misconfiguration rather than an intent.
        if risk.daily_loss_limit_pct <= risk.risk_per_trade_pct:
            raise ValueError(
                f"daily_loss_limit_pct ({risk.daily_loss_limit_pct}) must exceed "
                f"risk_per_trade_pct ({risk.risk_per_trade_pct}); otherwise a single "
                "ordinary losing trade halts trading for the day."
            )

        # Concurrent positions each risk the full per-trade amount. If their combined
        # risk exceeds the daily cap, the cap can be breached in one adverse move.
        combined = risk.risk_per_trade_pct * risk.max_open_positions
        if combined > risk.daily_loss_limit_pct:
            raise ValueError(
                f"max_open_positions ({risk.max_open_positions}) x risk_per_trade_pct "
                f"({risk.risk_per_trade_pct}) = {combined:.2f}% of equity at risk "
                f"simultaneously, which exceeds daily_loss_limit_pct "
                f"({risk.daily_loss_limit_pct}). One adverse move would blow through the "
                "daily cap before it can halt anything."
            )

        if risk.equity_floor_pct >= 100 - risk.daily_loss_limit_pct:
            raise ValueError(
                f"equity_floor_pct ({risk.equity_floor_pct}) leaves less room than a single "
                f"day's permitted loss ({risk.daily_loss_limit_pct}%); the floor would trip "
                "on day one."
            )

        strat = self.strategy
        if strat.rsi_extreme <= strat.rsi_midline:
            raise ValueError(
                f"rsi_extreme ({strat.rsi_extreme}) must exceed rsi_midline "
                f"({strat.rsi_midline}); otherwise the momentum window is empty and no "
                "signal can ever pass."
            )
        if strat.fast_ema >= strat.trend_ema or strat.trend_ema >= strat.slow_ema:
            raise ValueError(
                f"EMA periods must be strictly increasing: fast ({strat.fast_ema}) < "
                f"trend ({strat.trend_ema}) < slow ({strat.slow_ema})"
            )
        if strat.breakeven_at_r is not None and strat.breakeven_at_r >= strat.tp_r_multiple:
            raise ValueError(
                f"breakeven_at_r ({strat.breakeven_at_r}) must be below tp_r_multiple "
                f"({strat.tp_r_multiple}), or the take profit fires first and break-even is dead code."
            )
        if (
            strat.trail_after_r is not None
            and strat.breakeven_at_r is not None
            and strat.trail_after_r < strat.breakeven_at_r
        ):
            raise ValueError(
                f"trail_after_r ({strat.trail_after_r}) must be at or above breakeven_at_r "
                f"({strat.breakeven_at_r}); trailing before break-even would widen risk."
            )

        if self.routine.enabled:
            from datetime import datetime, timedelta

            anchor = datetime(2000, 1, 3)
            preflight = anchor.replace(
                hour=self.routine.preflight_time.hour, minute=self.routine.preflight_time.minute
            )
            first_open = anchor.replace(
                hour=self.sessions.first_open_time.hour, minute=self.sessions.first_open_time.minute
            )
            lead = timedelta(minutes=self.routine.preflight_min_lead_min)
            if preflight + lead > first_open:
                raise ValueError(
                    f"routine.preflight {self.routine.preflight} must be at least "
                    f"{self.routine.preflight_min_lead_min} minutes before the first trading "
                    f"window opens ({self.sessions.first_open_time:%H:%M}). The day plan needs "
                    "a news search and a regime read to finish before the hunt starts."
                )

        if self.telegram.enabled and not (self.telegram.bot_token and self.telegram.chat_id):
            raise ValueError(
                "telegram.enabled is true but bot_token/chat_id are missing. Set them, or "
                "disable Telegram — a kill switch you think exists but doesn't is worse than none."
            )

        return self

    @property
    def live_orders_armed(self) -> bool:
        """True only when both independent live-trading opt-ins are satisfied."""
        return not self.dry_run and self.live_confirm == LIVE_CONFIRM_PHRASE


def _expand_env(value: Any) -> Any:
    """Recursively replace exact ``${VAR}`` strings with their environment value.

    Keeps secrets out of the config file: write ``password: ${MT5_PASSWORD}`` and
    set the variable in the OS. A referenced-but-unset variable is an error rather
    than a silent empty string, which would otherwise surface as a puzzling login
    failure at the broker.
    """
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value.strip())
        if match:
            name = match.group(1)
            if name not in os.environ:
                raise ValueError(
                    f"config references environment variable ${{{name}}} but it is not set"
                )
            return os.environ[name]
    return value


def load_config(path: str | Path = "config/config.yaml") -> Config:
    """Load, env-expand and validate the config file.

    Raises ``FileNotFoundError`` with actionable guidance if the file is missing,
    and ``pydantic.ValidationError`` (with the specific field named) if any value
    or combination of values is unsafe.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found at {config_path}. Copy config/config.example.yaml to "
            f"{config_path} and fill in your MT5 credentials. The real config is "
            "gitignored so your credentials never reach the repository."
        )

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"{config_path} must contain a YAML mapping at the top level")

    return Config.model_validate(_expand_env(raw))
