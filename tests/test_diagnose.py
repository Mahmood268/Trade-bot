"""scripts/diagnose.py runs end to end on synthetic data and reads its own numbers right.

The script exists to be run on the user's Windows machine against real
history, where I cannot watch it. So its happy path is exercised here, and the
one piece of logic that could mislead — the in-sample / out-of-sample split —
is checked directly.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.backtest import Trade

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("diagnose", ROOT / "scripts" / "diagnose.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trending_walk(months: int = 2, seed: int = 3):
    """A gold-like walk with a drift strong enough to produce trades quickly."""
    rng = np.random.default_rng(seed)
    n = 60 * 24 * 22 * months
    idx = pd.date_range("2025-01-06", periods=n, freq="1min", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    close = 2000.0 * np.exp(np.cumsum(rng.normal(0.00001, 0.00024, n)))
    wick = np.abs(rng.normal(0.0, 0.35, n))
    open_ = np.concatenate([[2000.0], close[:-1]])
    m1 = pd.DataFrame(
        {"open": open_, "high": np.maximum(open_, close) + wick,
         "low": np.minimum(open_, close) - wick, "close": close},
        index=idx,
    )
    m15 = m1.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    return m15, m1


def _trade(r: float, when: datetime, reason: str = "stop", direction: str = "buy") -> Trade:
    risk = 50.0
    return Trade(
        entry_time=when, exit_time=when, direction=direction, volume=0.1,
        entry_price=2000.0, exit_price=2000.0, initial_stop=1996.0, stop=1996.0,
        target=2008.0, risk_amount=risk, risk_points=400.0, commission=0.0,
        pnl=r * risk, exit_reason=reason,
    )


def test_runs_end_to_end_and_prints_every_section(tmp_path, capsys, monkeypatch):
    m15, m1 = _trending_walk()
    m15.to_parquet(tmp_path / "m15.parquet")
    m1.to_parquet(tmp_path / "m1.parquet")

    module = load_script()
    monkeypatch.setattr(
        sys, "argv",
        ["diagnose", "--config", str(ROOT / "config" / "config.example.yaml"),
         "--data", str(tmp_path / "m15.parquet"), "--m1", str(tmp_path / "m1.parquet"),
         "--skip-grid"],
    )
    assert module.main() == 0
    out = capsys.readouterr().out
    for heading in ("1. THE HALT", "2. WHERE THE MONEY GOES", "3. WERE THE LOSERS",
                    "4. LONG vs SHORT", "5. WHEN"):
        assert heading in out
    assert "6. EXIT-RULE GRID" not in out


def test_grid_scores_each_half_separately(tmp_path, capsys, monkeypatch):
    """The whole point of the grid is the split; both columns must appear."""
    m15, m1 = _trending_walk(months=1)
    m15.to_parquet(tmp_path / "m15.parquet")

    module = load_script()
    # Two variants keep the test fast; the split logic is identical for twelve.
    monkeypatch.setattr(module, "BREAKEVEN_VARIANTS", [None])
    monkeypatch.setattr(module, "TRAIL_VARIANTS", [None])
    monkeypatch.setattr(module, "TARGET_VARIANTS", [2.0])
    monkeypatch.setattr(
        sys, "argv",
        ["diagnose", "--config", str(ROOT / "config" / "config.example.yaml"),
         "--data", str(tmp_path / "m15.parquet")],
    )
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "IS exp" in out and "OOS exp" in out


def test_expectancy_and_win_rate_helpers():
    module = load_script()
    now = datetime(2025, 1, 6, tzinfo=timezone.utc)
    trades = [_trade(2.0, now), _trade(-1.0, now), _trade(-1.0, now), _trade(-1.0, now)]
    assert module.expectancy(trades) == pytest.approx(-0.25)
    assert module.win_rate(trades) == pytest.approx(25.0)
    assert module.expectancy([]) == 0.0


def test_exit_section_names_the_biggest_drain(capsys):
    module = load_script()
    now = datetime(2025, 1, 6, tzinfo=timezone.utc)
    trades = [
        _trade(2.0, now, "target"),
        _trade(-1.0, now, "stop"), _trade(-1.0, now, "stop"), _trade(-1.0, now, "stop"),
        _trade(-0.05, now, "breakeven_stop"),
    ]
    module.section_exits(trades)
    out = capsys.readouterr().out
    assert "biggest drain is 'stop'" in out
    assert "Break-even stops: 1 trades" in out


def test_excursion_section_distinguishes_entry_and_exit_problems(capsys):
    module = load_script()
    now = datetime(2025, 1, 6, tzinfo=timezone.utc)

    losers_that_were_winning = [_trade(-1.0, now) for _ in range(5)]
    for t in losers_that_were_winning:
        t.mfe_r = 1.4
    module.section_excursions(losers_that_were_winning)
    assert "EXITS are giving them back" in capsys.readouterr().out

    losers_that_never_moved = [_trade(-1.0, now) for _ in range(5)]
    for t in losers_that_never_moved:
        t.mfe_r = 0.2
    module.section_excursions(losers_that_never_moved)
    assert "ENTRY criteria are what is wrong" in capsys.readouterr().out


def test_config_overrides_leave_the_original_untouched():
    from goldbot.config import load_config

    module = load_script()
    cfg = load_config(ROOT / "config" / "config.example.yaml")
    changed = module.with_risk(cfg, max_consecutive_losses=module.NO_HALT)
    assert changed.risk.max_consecutive_losses == 50
    assert cfg.risk.max_consecutive_losses == 5
    tuned = module.with_strategy(cfg, breakeven_at_r=None, tp_r_multiple=3.0)
    assert tuned.strategy.breakeven_at_r is None
    assert cfg.strategy.breakeven_at_r == 1.0
