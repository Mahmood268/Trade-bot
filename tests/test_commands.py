import unittest

from trade_bot.commands import CommandContext, CommandRouter, parse_command
from trade_bot.handlers import TradingBridge, register_default_commands


def context(text, **kwargs):
    command, args = parse_command(text, bot_username=kwargs.pop("bot_username", None))
    return CommandContext(command=command or "", args=args, text=text, chat_id=kwargs.pop("chat_id", 42), **kwargs)


class ParseCommandTests(unittest.TestCase):
    def test_plain_text_is_not_a_command(self):
        self.assertEqual(parse_command("how are we doing?"), (None, []))

    def test_command_without_arguments(self):
        self.assertEqual(parse_command("/status"), ("status", []))

    def test_command_is_lowercased(self):
        self.assertEqual(parse_command("/STATUS"), ("status", []))

    def test_arguments_are_split(self):
        self.assertEqual(parse_command("/report week detailed"), ("report", ["week", "detailed"]))

    def test_quoted_arguments_stay_together(self):
        self.assertEqual(parse_command('/note "trend is up"'), ("note", ["trend is up"]))

    def test_unbalanced_quotes_fall_back_to_whitespace(self):
        self.assertEqual(parse_command('/note "oops'), ("note", ['"oops']))

    def test_group_mention_addressed_to_us(self):
        self.assertEqual(parse_command("/status@my_bot", bot_username="my_bot"), ("status", []))

    def test_group_mention_addressed_to_another_bot_is_ignored(self):
        self.assertEqual(parse_command("/status@other_bot", bot_username="my_bot"), (None, []))

    def test_bare_slash_is_not_a_command(self):
        self.assertEqual(parse_command("/"), (None, []))


class RouterTests(unittest.TestCase):
    def test_dispatch_reaches_the_handler(self):
        router = CommandRouter()
        router.add("ping", lambda ctx: "pong", "check")
        self.assertEqual(router.dispatch(context("/ping")), "pong")

    def test_aliases_share_a_handler_but_not_the_help_list(self):
        router = CommandRouter()
        router.add("positions", lambda ctx: "none", "list", aliases=("pos",))
        self.assertEqual(router.dispatch(context("/pos")), "none")
        self.assertEqual([name for name, _ in router], ["positions"])

    def test_duplicate_registration_is_rejected(self):
        router = CommandRouter()
        router.add("ping", lambda ctx: "pong")
        with self.assertRaises(ValueError):
            router.add("ping", lambda ctx: "again")

    def test_unknown_command_falls_back(self):
        router = CommandRouter()
        router.fallback(lambda ctx: "unknown")
        self.assertEqual(router.dispatch(context("/nope")), "unknown")

    def test_without_a_fallback_unknown_commands_are_silent(self):
        self.assertIsNone(CommandRouter().dispatch(context("/nope")))

    def test_a_raising_handler_becomes_an_error_reply_not_a_crash(self):
        router = CommandRouter()

        def boom(ctx):
            raise RuntimeError("kaboom")

        router.add("boom", boom)
        reply = router.dispatch(context("/boom"))
        self.assertIn("RuntimeError: kaboom", reply)

    def test_argument_string_keeps_the_raw_text(self):
        ctx = context("/note the trend is up")
        self.assertEqual(ctx.argument_string, "the trend is up")

    def test_telegram_commands_skip_undocumented_entries(self):
        router = CommandRouter()
        router.add("shown", lambda ctx: "", "described")
        router.add("hidden", lambda ctx: "")
        self.assertEqual(router.telegram_commands(), [("shown", "described")])


class WiredBridge(TradingBridge):
    def status(self):
        return {"state": "running", "equity": "10,183.40"}

    def positions(self):
        return [["BTCUSDT", "LONG", "0.015", "64150.25", "+38.10"]]

    def report(self, period):
        return {"period": period, "pnl": "+1.8%"}

    def pause(self):
        return "paused"

    def resume(self):
        return "resumed"


class DefaultCommandTests(unittest.TestCase):
    def test_help_lists_registered_commands(self):
        router = register_default_commands(CommandRouter())
        reply = router.dispatch(context("/help"))
        for expected in ("/status", "/report", "/positions", "/pause"):
            self.assertIn(expected, reply)

    def test_ping_answers(self):
        router = register_default_commands(CommandRouter())
        self.assertIn("pong", router.dispatch(context("/ping")))

    def test_id_reports_the_chat(self):
        router = register_default_commands(CommandRouter())
        reply = router.dispatch(context("/id", chat_id=-100123, user_id=7, username="trader"))
        self.assertIn("-100123", reply)
        self.assertIn("@trader", reply)

    def test_unwired_bridge_says_so_instead_of_faking_data(self):
        router = register_default_commands(CommandRouter())
        reply = router.dispatch(context("/status"))
        self.assertIn("Not wired up yet", reply)
        self.assertIn("status()", reply)

    def test_wired_bridge_answers_status(self):
        router = register_default_commands(CommandRouter(), WiredBridge())
        reply = router.dispatch(context("/status"))
        self.assertIn("running", reply)
        self.assertIn("10,183.40", reply)

    def test_report_defaults_to_today_and_accepts_a_period(self):
        router = register_default_commands(CommandRouter(), WiredBridge())
        self.assertIn("today", router.dispatch(context("/report")))
        self.assertIn("week", router.dispatch(context("/report week")))

    def test_positions_renders_a_table(self):
        router = register_default_commands(CommandRouter(), WiredBridge())
        reply = router.dispatch(context("/positions"))
        self.assertIn("BTCUSDT", reply)
        self.assertIn("Symbol", reply)

    def test_pause_and_resume_confirm(self):
        router = register_default_commands(CommandRouter(), WiredBridge())
        self.assertIn("paused", router.dispatch(context("/pause")))
        self.assertIn("resumed", router.dispatch(context("/resume")))

    def test_plain_text_gets_pointed_at_help(self):
        router = register_default_commands(CommandRouter())
        self.assertIn("/help", router.dispatch(context("what is going on?")))


if __name__ == "__main__":
    unittest.main()
