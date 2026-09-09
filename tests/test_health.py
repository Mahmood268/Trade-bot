import unittest

from trade_bot.health import HealthConfig, HealthMonitor, _fmt_duration


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingBot:
    """Stands in for TelegramBot, capturing what the monitor would send."""

    def __init__(self, username="my_trade_bot", fail=False):
        self.sent = []
        self._username = username
        self._fail = fail
        self.client = self

    def notify_safely(self, text, **kwargs):
        self.sent.append(text)
        return True

    def get_me(self):
        if self._fail:
            raise ConnectionError("telegram unreachable")
        return {"username": self._username}

    def texts(self):
        return "\n---\n".join(self.sent)


def build(bot=None, **config):
    clock = FakeClock()
    settings = {"notify_on_start": False, "watchdog_timeout": 300, "heartbeat_interval": 0}
    settings.update(config)
    monitor = HealthMonitor(bot, HealthConfig(**settings), clock=clock)
    return monitor, clock


class DurationTests(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(_fmt_duration(45), "45s")

    def test_minutes_and_seconds(self):
        self.assertEqual(_fmt_duration(125), "2m 5s")

    def test_days_hours_minutes(self):
        self.assertEqual(_fmt_duration(90_061), "1d 1h 1m 1s")

    def test_none_and_negative(self):
        self.assertEqual(_fmt_duration(None), "-")
        self.assertEqual(_fmt_duration(-5), "0s")


class BeatTests(unittest.TestCase):
    def test_no_beat_yet_means_no_age(self):
        monitor, _ = build()
        self.assertIsNone(monitor.last_beat_age)

    def test_beat_records_age_and_count(self):
        monitor, clock = build()
        monitor.beat()
        clock.advance(30)
        self.assertEqual(monitor.last_beat_age, 30)
        monitor.beat()
        self.assertEqual(monitor.last_beat_age, 0)
        self.assertEqual(monitor.snapshot(run_checks=False).beats, 2)

    def test_uptime_tracks_the_clock(self):
        monitor, clock = build()
        clock.advance(3600)
        self.assertEqual(monitor.uptime, 3600)


class WatchdogTests(unittest.TestCase):
    def test_silent_while_the_loop_ticks(self):
        bot = RecordingBot()
        monitor, clock = build(bot)
        monitor.beat()
        clock.advance(100)
        monitor.tick()
        self.assertEqual(bot.sent, [])

    def test_alerts_once_when_the_loop_stalls(self):
        bot = RecordingBot()
        monitor, clock = build(bot)
        monitor.beat()
        clock.advance(400)
        monitor.tick()
        monitor.tick()
        monitor.tick()
        self.assertEqual(len(bot.sent), 1, "the watchdog must not repeat every tick")
        self.assertIn("stalled", bot.sent[0])

    def test_recovery_is_announced_when_beats_resume(self):
        bot = RecordingBot()
        monitor, clock = build(bot)
        monitor.beat()
        clock.advance(400)
        monitor.tick()
        monitor.beat()
        self.assertEqual(len(bot.sent), 2)
        self.assertIn("Recovered", bot.sent[1])

    def test_it_can_alert_again_after_recovering(self):
        bot = RecordingBot()
        monitor, clock = build(bot)
        monitor.beat()
        clock.advance(400)
        monitor.tick()
        monitor.beat()
        clock.advance(400)
        monitor.tick()
        self.assertEqual(sum("stalled" in text for text in bot.sent), 2)

    def test_a_loop_that_never_beats_is_never_falsely_alerted(self):
        bot = RecordingBot()
        monitor, clock = build(bot)
        clock.advance(10_000)
        monitor.tick()
        self.assertEqual(bot.sent, [], "the watchdog arms on the first beat")

    def test_watchdog_can_be_disabled(self):
        bot = RecordingBot()
        monitor, clock = build(bot, watchdog_timeout=0)
        monitor.beat()
        clock.advance(10_000)
        monitor.tick()
        self.assertEqual(bot.sent, [])

    def test_recovery_notice_can_be_suppressed(self):
        bot = RecordingBot()
        monitor, clock = build(bot, notify_on_recovery=False)
        monitor.beat()
        clock.advance(400)
        monitor.tick()
        monitor.beat()
        self.assertEqual(len(bot.sent), 1)


class HeartbeatTests(unittest.TestCase):
    def test_disabled_by_default(self):
        bot = RecordingBot()
        monitor, clock = build(bot)
        clock.advance(100_000)
        monitor.tick()
        self.assertEqual(bot.sent, [])

    def test_fires_on_the_configured_interval(self):
        bot = RecordingBot()
        monitor, clock = build(bot, heartbeat_interval=3600)
        clock.advance(1800)
        monitor.tick()
        self.assertEqual(bot.sent, [])
        clock.advance(1900)
        monitor.tick()
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("Healthy", bot.sent[0])

    def test_start_notice_is_sent_when_enabled(self):
        bot = RecordingBot()
        monitor, _ = build(bot, notify_on_start=True)
        monitor.start()
        monitor.stop()
        self.assertIn("started", bot.sent[0])


class CheckTests(unittest.TestCase):
    def test_the_telegram_check_is_registered_with_a_bot(self):
        bot = RecordingBot()
        monitor, _ = build(bot)
        report = monitor.snapshot()
        self.assertTrue(report.ok)
        self.assertEqual(report.checks[0].name, "telegram api")
        self.assertIn("@my_trade_bot", report.checks[0].detail)

    def test_an_unreachable_telegram_makes_the_report_unhealthy(self):
        bot = RecordingBot(fail=True)
        monitor, _ = build(bot)
        report = monitor.snapshot()
        self.assertFalse(report.ok)
        self.assertIn("ConnectionError", report.checks[0].detail)

    def test_a_raising_custom_check_fails_instead_of_propagating(self):
        monitor, _ = build()

        def broken():
            raise RuntimeError("db down")

        monitor.register("database", broken)
        report = monitor.snapshot()
        self.assertFalse(report.ok)
        self.assertIn("RuntimeError: db down", report.checks[0].detail)

    def test_a_check_can_return_a_detail(self):
        monitor, _ = build()
        monitor.register("exchange", lambda: (True, "binance ok"))
        report = monitor.snapshot()
        self.assertTrue(report.ok)
        self.assertEqual(report.checks[0].detail, "binance ok")

    def test_a_bare_false_fails_the_report(self):
        monitor, _ = build()
        monitor.register("feed", lambda: False)
        self.assertFalse(monitor.snapshot().ok)

    def test_a_stale_loop_makes_the_report_unhealthy(self):
        monitor, clock = build()
        monitor.beat()
        clock.advance(400)
        report = monitor.snapshot()
        self.assertFalse(report.ok)
        self.assertIn("no tick", report.note)


class ReportRenderingTests(unittest.TestCase):
    def test_healthy_message_mentions_uptime(self):
        monitor, clock = build(RecordingBot())
        clock.advance(90_061)
        message = monitor.snapshot().to_message()
        self.assertIn("Healthy", message)
        self.assertIn("1d 1h 1m 1s", message)

    def test_unhealthy_message_lists_the_failed_check(self):
        monitor, _ = build(RecordingBot(fail=True))
        message = monitor.snapshot().to_message()
        self.assertIn("Unhealthy", message)
        self.assertIn("telegram api", message)

    def test_check_details_are_html_escaped(self):
        monitor, _ = build()
        monitor.register("exchange", lambda: (False, "<b>down</b>"))
        self.assertIn("&lt;b&gt;down&lt;/b&gt;", monitor.snapshot().to_message())

    def test_as_dict_is_json_shaped(self):
        monitor, _ = build(RecordingBot())
        monitor.beat()
        payload = monitor.snapshot().as_dict()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["beats"], 1)
        self.assertEqual(payload["checks"][0]["status"], "ok")
        self.assertIn("uptime", payload)


class ConfigTests(unittest.TestCase):
    def test_defaults_are_quiet_when_healthy(self):
        config = HealthConfig.from_env({})
        self.assertEqual(config.heartbeat_interval, 0, "heartbeat is opt-in")
        self.assertEqual(config.watchdog_timeout, 300, "watchdog is on by default")
        self.assertIsNone(config.http_port)

    def test_values_are_read_from_the_environment(self):
        config = HealthConfig.from_env(
            {
                "HEALTH_HEARTBEAT_INTERVAL": "3600",
                "HEALTH_WATCHDOG_TIMEOUT": "120",
                "HEALTH_HTTP_PORT": "8080",
                "HEALTH_HTTP_HOST": "0.0.0.0",
            }
        )
        self.assertEqual(config.heartbeat_interval, 3600)
        self.assertEqual(config.watchdog_timeout, 120)
        self.assertEqual(config.http_port, 8080)
        self.assertEqual(config.http_host, "0.0.0.0")

    def test_a_bad_value_falls_back_to_the_default(self):
        config = HealthConfig.from_env({"HEALTH_WATCHDOG_TIMEOUT": "soon"})
        self.assertEqual(config.watchdog_timeout, 300)

    def test_health_command_is_attached_to_a_router(self):
        from trade_bot.commands import CommandContext, CommandRouter

        monitor, _ = build(RecordingBot())
        router = monitor.attach(CommandRouter())
        reply = router.dispatch(
            CommandContext(command="health", args=[], text="/health", chat_id=1)
        )
        self.assertIn("Healthy", reply)


if __name__ == "__main__":
    unittest.main()
