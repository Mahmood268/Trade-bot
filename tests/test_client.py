import unittest

import requests

from trade_bot.client import MAX_MESSAGE_CHARS, TelegramClient, TelegramError, split_message
from tests.fakes import FakeResponse, FakeSession, ok

TOKEN = "123456789:AAsecret"


def build(responses=None, **kwargs):
    session = FakeSession(responses)
    client = TelegramClient(TOKEN, session=session, sleep=lambda _s: None, **kwargs)
    return client, session


class SplitMessageTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(split_message("hello"), ["hello"])

    def test_empty_text_yields_nothing(self):
        self.assertEqual(split_message(""), [])

    def test_long_text_is_split_under_the_limit(self):
        chunks = split_message("x" * (MAX_MESSAGE_CHARS * 2 + 10))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= MAX_MESSAGE_CHARS for chunk in chunks))

    def test_split_prefers_line_boundaries(self):
        text = "\n".join("line %d" % i for i in range(2000))
        chunks = split_message(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(not chunk.startswith("\n") for chunk in chunks))
        self.assertNotIn("lin\ne", "".join(chunks))

    def test_no_content_is_lost(self):
        text = " ".join(str(i) for i in range(3000))
        rejoined = " ".join(split_message(text))
        self.assertEqual(rejoined.split(), text.split())


class SendMessageTests(unittest.TestCase):
    def test_payload_carries_text_and_parse_mode(self):
        client, session = build()
        client.send_message(42, "hi", reply_to_message_id=5)
        payload = session.payloads("sendMessage")[0]
        self.assertEqual(payload["chat_id"], 42)
        self.assertEqual(payload["text"], "hi")
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertEqual(payload["reply_to_message_id"], 5)

    def test_long_text_becomes_several_calls_and_only_the_first_quotes(self):
        client, session = build()
        client.send_message(42, "y" * (MAX_MESSAGE_CHARS + 100), reply_to_message_id=5)
        payloads = session.payloads("sendMessage")
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["reply_to_message_id"], 5)
        self.assertNotIn("reply_to_message_id", payloads[1])

    def test_none_values_are_stripped_from_the_payload(self):
        client, session = build()
        client.send_message(42, "hi", parse_mode=None)
        self.assertNotIn("parse_mode", session.payloads("sendMessage")[0])


class RetryTests(unittest.TestCase):
    def test_server_errors_are_retried_then_succeed(self):
        client, session = build([FakeResponse(502, {"ok": False}, text="bad gateway"), ok({"message_id": 1})])
        client.send_message(42, "hi")
        self.assertEqual(len(session.calls), 2)

    def test_network_errors_are_retried(self):
        session = FakeSession([requests.ConnectionError("boom"), ok({"message_id": 1})])
        client = TelegramClient(TOKEN, session=session, sleep=lambda _s: None)
        client.send_message(42, "hi")
        self.assertEqual(len(session.calls), 2)

    def test_retries_are_bounded(self):
        client, session = build([FakeResponse(503, {"ok": False}, text="down")], max_attempts=3)
        with self.assertRaises(TelegramError):
            client.send_message(42, "hi")
        self.assertEqual(len(session.calls), 3)

    def test_rate_limit_hint_is_honoured(self):
        delays = []
        session = FakeSession(
            [FakeResponse(429, {"ok": False, "parameters": {"retry_after": 30}}), ok({"message_id": 1})]
        )
        client = TelegramClient(TOKEN, session=session, sleep=delays.append)
        client.send_message(42, "hi")
        self.assertEqual(delays, [30.0])

    def test_client_errors_are_not_retried(self):
        client, session = build([FakeResponse(401, {"ok": False}, text="unauthorized")])
        with self.assertRaises(TelegramError) as caught:
            client.get_me()
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(len(session.calls), 1)

    def test_a_rejected_payload_is_not_retried(self):
        client, session = build([FakeResponse(200, {"ok": False, "description": "chat not found"})])
        with self.assertRaises(TelegramError) as caught:
            client.send_message(42, "hi")
        self.assertIn("chat not found", str(caught.exception))
        self.assertEqual(len(session.calls), 1)

    def test_the_token_never_leaks_into_an_error(self):
        client, _ = build([FakeResponse(401, {"ok": False}, text="unauthorized")])
        with self.assertRaises(TelegramError) as caught:
            client.get_me()
        self.assertNotIn("AAsecret", str(caught.exception))


class GetUpdatesTests(unittest.TestCase):
    def test_offset_and_timeout_are_passed_through(self):
        client, session = build([ok([{"update_id": 1}])])
        updates = client.get_updates(offset=7, timeout=25)
        payload = session.payloads("getUpdates")[0]
        self.assertEqual(payload["offset"], 7)
        self.assertEqual(payload["timeout"], 25)
        self.assertEqual(payload["allowed_updates"], ["message"])
        self.assertEqual(len(updates), 1)

    def test_a_null_result_becomes_an_empty_list(self):
        client, _ = build([ok(None)])
        self.assertEqual(client.get_updates(), [])


class SetMyCommandsTests(unittest.TestCase):
    def test_pairs_become_command_objects(self):
        client, session = build()
        client.set_my_commands([("help", "Show help")])
        payload = session.payloads("setMyCommands")[0]
        self.assertEqual(payload["commands"], [{"command": "help", "description": "Show help"}])


if __name__ == "__main__":
    unittest.main()
