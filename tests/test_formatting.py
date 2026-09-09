import unittest
from datetime import datetime, timezone

from trade_bot.formatting import Signal, esc, format_error, format_report, format_signal

WHEN = datetime(2026, 9, 9, 12, 30, 0, tzinfo=timezone.utc)


class EscapeTests(unittest.TestCase):
    def test_html_metacharacters_are_escaped(self):
        self.assertEqual(esc("a < b & c > d"), "a &lt; b &amp; c &gt; d")

    def test_a_strategy_name_cannot_inject_markup(self):
        body = format_signal(Signal(symbol="btcusdt", side="buy", strategy="<b>pwn</b>", timestamp=WHEN))
        self.assertIn("&lt;b&gt;pwn&lt;/b&gt;", body)
        self.assertNotIn("<b>pwn</b>", body)


class SignalTests(unittest.TestCase):
    def test_symbol_and_side_are_normalised(self):
        signal = Signal(symbol="btcusdt", side="buy")
        self.assertEqual(signal.symbol, "BTCUSDT")
        self.assertEqual(signal.side, "BUY")

    def test_full_signal_renders_every_field(self):
        body = format_signal(
            Signal(
                symbol="BTCUSDT",
                side="BUY",
                price=64150.25,
                quantity=0.015,
                stop_loss=63200.0,
                take_profit=66000.0,
                strategy="ema-cross",
                confidence=0.72,
                note="breakout confirmed",
                timestamp=WHEN,
                extra={"time_frame": "4h"},
            )
        )
        self.assertIn("<b>BUY BTCUSDT</b>", body)
        self.assertIn("64,150.25", body)
        self.assertIn("0.015", body)
        self.assertIn("63,200", body)
        self.assertIn("72%", body)
        self.assertIn("ema-cross", body)
        self.assertIn("Time frame: 4h", body)
        self.assertIn("breakout confirmed", body)
        self.assertIn("2026-09-09 12:30:00 UTC", body)

    def test_optional_fields_are_omitted_not_blanked(self):
        body = format_signal(Signal(symbol="ETHUSDT", side="SELL", timestamp=WHEN))
        self.assertNotIn("Stop loss", body)
        self.assertNotIn("Confidence", body)
        self.assertIn("<b>SELL ETHUSDT</b>", body)

    def test_naive_timestamps_are_treated_as_utc(self):
        body = format_signal(Signal(symbol="X", side="BUY", timestamp=datetime(2026, 1, 2, 3, 4, 5)))
        self.assertIn("2026-01-02 03:04:05 UTC", body)


class ReportTests(unittest.TestCase):
    def test_metrics_and_table_are_rendered(self):
        body = format_report(
            "Daily report",
            {"trades": 7, "win_rate": "57%"},
            rows=[["BTCUSDT", "LONG", "+38.10"]],
            headers=["Symbol", "Side", "PnL"],
            footer="end of day",
            timestamp=WHEN,
        )
        self.assertIn("<b>Daily report</b>", body)
        self.assertIn("win rate", body)
        self.assertIn("<pre>", body)
        self.assertIn("BTCUSDT", body)
        self.assertIn("end of day", body)

    def test_ragged_rows_do_not_crash(self):
        body = format_report("R", rows=[["a"], ["b", "c", "d"]], headers=["one", "two"])
        self.assertIn("<pre>", body)

    def test_report_without_metrics_or_rows(self):
        self.assertIn("<b>Empty</b>", format_report("Empty", timestamp=WHEN))


class ErrorTests(unittest.TestCase):
    def test_exception_type_and_message_are_shown(self):
        body = format_error("order failed", ValueError("bad size"), timestamp=WHEN)
        self.assertIn("order failed", body)
        self.assertIn("ValueError: bad size", body)

    def test_plain_string_errors_work_too(self):
        self.assertIn("connection lost", format_error("feed", "connection lost", timestamp=WHEN))


if __name__ == "__main__":
    unittest.main()
