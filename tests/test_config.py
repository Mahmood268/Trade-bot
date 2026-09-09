import unittest
from pathlib import Path

from trade_bot.config import ConfigError, TelegramConfig

TOKEN = "123456789:AAsomethingsecret"


class TelegramConfigTests(unittest.TestCase):
    def test_from_env_reads_all_fields(self):
        config = TelegramConfig.from_env(
            {
                "TELEGRAM_BOT_TOKEN": TOKEN,
                "TELEGRAM_CHAT_ID": "42",
                "TELEGRAM_ALLOWED_CHAT_IDS": "42, 99;100",
                "TELEGRAM_POLL_TIMEOUT": "10",
                "TELEGRAM_OFFSET_FILE": "/tmp/offset",
            }
        )
        self.assertEqual(config.chat_id, 42)
        self.assertEqual(config.allowed_chat_ids, frozenset({42, 99, 100}))
        self.assertEqual(config.poll_timeout, 10)
        self.assertEqual(config.offset_file, Path("/tmp/offset"))

    def test_allowed_defaults_to_the_main_chat(self):
        config = TelegramConfig.from_env({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(config.allowed_chat_ids, frozenset({42}))
        self.assertTrue(config.is_allowed(42))
        self.assertFalse(config.is_allowed(43))

    def test_negative_chat_ids_are_valid_for_groups(self):
        config = TelegramConfig.from_env({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-1001234567890"})
        self.assertTrue(config.is_allowed(-1001234567890))

    def test_missing_token_is_rejected(self):
        with self.assertRaises(ConfigError):
            TelegramConfig.from_env({"TELEGRAM_CHAT_ID": "42"})

    def test_malformed_token_is_rejected(self):
        with self.assertRaises(ConfigError):
            TelegramConfig.from_env({"TELEGRAM_BOT_TOKEN": "nonsense", "TELEGRAM_CHAT_ID": "42"})

    def test_missing_chat_id_is_rejected(self):
        with self.assertRaises(ConfigError):
            TelegramConfig.from_env({"TELEGRAM_BOT_TOKEN": TOKEN})

    def test_non_numeric_chat_id_is_rejected(self):
        with self.assertRaises(ConfigError):
            TelegramConfig.from_env({"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "@me"})

    def test_repr_hides_the_token(self):
        config = TelegramConfig(token=TOKEN, chat_id=42)
        self.assertNotIn("AAsomethingsecret", repr(config))
        self.assertIn("***", repr(config))


if __name__ == "__main__":
    unittest.main()
