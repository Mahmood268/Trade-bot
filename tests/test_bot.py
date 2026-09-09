import tempfile
import threading
import unittest
from pathlib import Path

from trade_bot.bot import TelegramBot
from trade_bot.client import TelegramClient, TelegramError
from trade_bot.commands import CommandRouter
from trade_bot.config import TelegramConfig
from trade_bot.formatting import Signal
from tests.fakes import FakeResponse, FakeSession, message_update, ok

TOKEN = "123456789:AAsecret"


def build_bot(responses=None, *, allowed=(42,), offset_file=None, router=None):
    config = TelegramConfig(
        token=TOKEN,
        chat_id=42,
        allowed_chat_ids=frozenset(allowed),
        poll_timeout=0,
        offset_file=offset_file,
    )
    session = FakeSession(responses)
    client = TelegramClient(TOKEN, session=session, sleep=lambda _s: None)
    return TelegramBot(config, client=client, router=router), session


class OutboundTests(unittest.TestCase):
    def test_send_signal_goes_to_the_default_chat(self):
        bot, session = build_bot()
        bot.send_signal(Signal(symbol="BTCUSDT", side="BUY", price=64150.25))
        payload = session.payloads("sendMessage")[0]
        self.assertEqual(payload["chat_id"], 42)
        self.assertIn("BUY BTCUSDT", payload["text"])

    def test_send_report_is_silent_by_default(self):
        bot, session = build_bot()
        bot.send_report("Daily", {"pnl": "+1.8%"})
        self.assertTrue(session.payloads("sendMessage")[0]["disable_notification"])

    def test_send_error_is_not_silent(self):
        bot, session = build_bot()
        bot.send_error("order failed", ValueError("bad size"))
        payload = session.payloads("sendMessage")[0]
        self.assertFalse(payload["disable_notification"])
        self.assertIn("ValueError: bad size", payload["text"])

    def test_notify_safely_swallows_telegram_failures(self):
        bot, _ = build_bot([FakeResponse(500, {"ok": False}, text="down")])
        self.assertFalse(bot.notify_safely("hello"))

    def test_notify_safely_reports_success(self):
        bot, _ = build_bot()
        self.assertTrue(bot.notify_safely("hello"))

    def test_send_failures_still_propagate_on_the_normal_path(self):
        bot, _ = build_bot([FakeResponse(500, {"ok": False}, text="down")])
        with self.assertRaises(TelegramError):
            bot.send_text("hello")


class InboundTests(unittest.TestCase):
    def test_a_command_is_answered_in_the_same_chat(self):
        bot, session = build_bot()
        bot.handle_update(message_update(1, "/ping"))
        payload = session.payloads("sendMessage")[0]
        self.assertEqual(payload["chat_id"], 42)
        self.assertIn("pong", payload["text"])
        self.assertEqual(payload["reply_to_message_id"], 10)

    def test_messages_from_other_chats_are_ignored_silently(self):
        bot, session = build_bot(allowed=(42,))
        self.assertIsNone(bot.handle_update(message_update(1, "/ping", chat_id=999)))
        self.assertEqual(session.payloads("sendMessage"), [])

    def test_a_second_allowed_chat_is_answered(self):
        bot, session = build_bot(allowed=(42, 77))
        bot.handle_update(message_update(1, "/ping", chat_id=77))
        self.assertEqual(session.payloads("sendMessage")[0]["chat_id"], 77)

    def test_edited_messages_are_handled(self):
        bot, session = build_bot()
        update = message_update(1, "/ping")
        update["edited_message"] = update.pop("message")
        bot.handle_update(update)
        self.assertIn("pong", session.payloads("sendMessage")[0]["text"])

    def test_non_message_updates_are_skipped(self):
        bot, session = build_bot()
        self.assertIsNone(bot.handle_update({"update_id": 1, "poll": {}}))
        self.assertEqual(session.payloads("sendMessage"), [])

    def test_a_message_without_text_is_skipped(self):
        bot, session = build_bot()
        update = message_update(1, "")
        update["message"]["text"] = ""
        self.assertIsNone(bot.handle_update(update))
        self.assertEqual(session.payloads("sendMessage"), [])

    def test_a_custom_command_can_be_registered(self):
        bot, session = build_bot()

        @bot.command("pnl", "Show pnl")
        def _pnl(context):
            return f"pnl for {context.argument_string or 'today'}"

        bot.handle_update(message_update(1, "/pnl week"))
        self.assertIn("pnl for week", session.payloads("sendMessage")[0]["text"])

    def test_the_handler_receives_the_bot(self):
        seen = {}
        bot, _ = build_bot()

        @bot.command("echo")
        def _echo(context):
            seen["bot"] = context.bot
            return "ok"

        bot.handle_update(message_update(1, "/echo"))
        self.assertIs(seen["bot"], bot)


class PollingTests(unittest.TestCase):
    def test_poll_once_advances_the_offset_past_the_batch(self):
        bot, session = build_bot([ok([message_update(5, "/ping"), message_update(6, "/ping")])])
        handled = bot.poll_once()
        self.assertEqual(handled, 2)
        self.assertEqual(bot.offset, 7)

    def test_a_failing_update_does_not_wedge_the_offset(self):
        router = CommandRouter()

        def boom(context):
            raise RuntimeError("kaboom")

        router.add("boom", boom)
        bot, _ = build_bot([ok([message_update(5, "/boom")])], router=router)
        bot.poll_once()
        self.assertEqual(bot.offset, 6)

    def test_the_offset_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "offset"
            bot, _ = build_bot([ok([message_update(5, "/ping")])], offset_file=path)
            bot.poll_once()
            self.assertEqual(path.read_text(), "6")

            resumed, session = build_bot([ok([])], offset_file=path)
            self.assertEqual(resumed.offset, 6)
            resumed.poll_once()
            self.assertEqual(session.payloads("getUpdates")[0]["offset"], 6)

    def test_an_unreadable_offset_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "offset"
            path.write_text("not a number")
            bot, _ = build_bot(offset_file=path)
            self.assertIsNone(bot.offset)

    def test_prepare_learns_the_username_and_publishes_commands(self):
        bot, session = build_bot([ok({"username": "my_trade_bot"})])
        bot.prepare()
        self.assertEqual(bot.bot_username, "my_trade_bot")
        self.assertIn("deleteWebhook", session.methods())
        self.assertIn("setMyCommands", session.methods())

    def test_poll_forever_stops_when_asked(self):
        bot, _ = build_bot([ok([])])
        stop = threading.Event()
        stop.set()
        bot.poll_forever(stop=stop, prepare=False)  # returns immediately

    def test_poll_forever_survives_a_telegram_outage(self):
        session = FakeSession([FakeResponse(503, {"ok": False}, text="down")])
        config = TelegramConfig(token=TOKEN, chat_id=42, poll_timeout=0)
        client = TelegramClient(TOKEN, session=session, sleep=lambda _s: None, max_attempts=1)
        bot = TelegramBot(config, client=client)

        stop = threading.Event()
        original_wait = stop.wait

        def wait_then_stop(timeout=None):
            stop.set()  # let the loop retry exactly once, then exit
            return original_wait(0)

        stop.wait = wait_then_stop
        bot.poll_forever(stop=stop, prepare=False)
        self.assertTrue(session.calls)


if __name__ == "__main__":
    unittest.main()
