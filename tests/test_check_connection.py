"""End-to-end test of scripts/check_connection.py against a fake terminal.

This is the first script the user runs on Windows, where I cannot test it, so
its happy path and its failure paths are exercised here instead.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import goldbot.mt5_client as mt5_client
from tests.fake_mt5 import FakeMT5, FakeSymbol

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "check_connection", ROOT / "scripts" / "check_connection.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MT5_PASSWORD", "demo")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    target = tmp_path / "config.yaml"
    target.write_text((ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"),
                      encoding="utf-8")
    return target


def run_script(monkeypatch, config_file, fake, argv_extra=()):
    monkeypatch.setattr(mt5_client, "_import_mt5", lambda: fake)
    module = load_script()
    monkeypatch.setattr(
        sys, "argv", ["check_connection.py", "--config", str(config_file), *argv_extra]
    )
    return module.main()


def test_happy_path_reports_everything_and_trades_nothing(monkeypatch, config_file, capsys):
    fake = FakeMT5(balance=5000.0)
    code = run_script(monkeypatch, config_file, fake)
    out = capsys.readouterr().out

    assert code == 0
    assert "gold symbol resolved: XAUUSD" in out
    assert "algorithmic trading is enabled" in out
    assert "All checks passed" in out
    # The critical guarantee: a read-only script sends no orders.
    assert fake.sent_requests == []


def test_sizing_math_matches_hand_calculation(monkeypatch, config_file, capsys):
    """$5,000 at 1% = $50 risk. A $4.00 gold stop is 400 points at $1/point/lot,
    so 50 / 400 = 0.125 lots, floored to the 0.01 step = 0.12."""
    fake = FakeMT5(balance=5000.0)
    run_script(monkeypatch, config_file, fake)
    out = capsys.readouterr().out
    assert "-> 0.12 lots" in out
    assert "48.00 USD (0.96% of equity)" in out


def test_warns_when_account_too_small_to_size_a_trade(monkeypatch, config_file, capsys):
    """On a tiny account, 1% cannot cover the broker's minimum lot — the bot would
    skip nearly every trade, so the script must say so rather than look healthy."""
    fake = FakeMT5(balance=200.0)
    code = run_script(monkeypatch, config_file, fake)
    out = capsys.readouterr().out
    assert code == 1
    assert "below the broker minimum" in out
    assert "too small" in out


def test_flags_disabled_algo_trading(monkeypatch, config_file, capsys):
    fake = FakeMT5(trade_allowed=False)
    run_script(monkeypatch, config_file, fake)
    out = capsys.readouterr().out
    assert "algorithmic trading is DISABLED" in out
    assert "Algo Trading" in out


def test_warns_on_wide_spread(monkeypatch, config_file, capsys):
    """Wide spread is the normal state outside session hours; the user needs to
    understand why the bot would decline to trade."""
    fake = FakeMT5(symbols={"XAUUSD": FakeSymbol("XAUUSD", bid=2650.00, ask=2651.00)})
    run_script(monkeypatch, config_file, fake)
    out = capsys.readouterr().out
    assert "100 points" in out
    assert "exceeds your max_spread_points" in out


def test_reports_missing_gold_symbol(monkeypatch, config_file, capsys):
    fake = FakeMT5(symbols={"EURUSD": FakeSymbol("EURUSD")})
    code = run_script(monkeypatch, config_file, fake)
    out = capsys.readouterr().out
    assert code == 1
    assert "Could not find a tradable gold symbol" in out
