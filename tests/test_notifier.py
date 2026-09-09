"""Telegram notifier tests, against a recording transport. No network."""

from __future__ import annotations

import pytest

from goldbot.config import TelegramConfig
from goldbot.notifier import Telegram


class FakeTransport:
    def __init__(self, updates=None, fail=False):
        self.calls: list[tuple[str, dict]] = []
        self.updates = updates or []
        self.fail = fail

    def __call__(self, method, params):
        self.calls.append((method, dict(params)))
        if self.fail:
            raise ConnectionError("telegram down")
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            return {"ok": True, "result": batch}
        return {"ok": True, "result": {}}


def update(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


@pytest.fixture
def cfg():
    return TelegramConfig(enabled=True, bot_token="123:abc", chat_id="777")


class TestSend:
    def test_sends_to_the_configured_chat(self, cfg):
        t = FakeTransport()
        tg = Telegram(cfg, transport=t)
        assert tg.send("hello")
        method, params = t.calls[0]
        assert method == "sendMessage" and params["chat_id"] == "777" and params["text"] == "hello"

    def test_a_transport_failure_never_raises(self, cfg):
        tg = Telegram(cfg, transport=FakeTransport(fail=True))
        assert tg.send("hello") is False
        assert tg.failures == 1

    def test_disabled_sends_nothing_but_records(self):
        tg = Telegram(TelegramConfig(enabled=False))
        assert tg.send("hello") is False
        assert tg.sent == ["hello"]

    def test_the_token_never_appears_in_a_message(self, cfg):
        t = FakeTransport()
        tg = Telegram(cfg, transport=t)
        tg.send(tg.help_text())
        assert "123:abc" not in t.calls[0][1]["text"]


class TestCommands:
    def test_parses_known_commands_from_the_owner(self, cfg):
        t = FakeTransport(updates=[update(1, 777, "/status"), update(2, 777, "/flat now")])
        cmds = Telegram(cfg, transport=t).poll_commands()
        assert [(c.name, c.args) for c in cmds] == [("status", ""), ("flat", "now")]

    def test_ignores_other_chats_silently(self, cfg):
        # No reply either: replying tells a stranger the bot exists.
        t = FakeTransport(updates=[update(1, 999, "/flat")])
        cmds = Telegram(cfg, transport=t).poll_commands()
        assert cmds == []
        assert all(m != "sendMessage" for m, _ in t.calls)

    def test_unknown_command_gets_a_hint(self, cfg):
        t = FakeTransport(updates=[update(1, 777, "/sell everything")])
        assert Telegram(cfg, transport=t).poll_commands() == []
        assert any(m == "sendMessage" and "Unknown command" in p["text"] for m, p in t.calls)

    def test_strips_the_bot_mention_suffix(self, cfg):
        t = FakeTransport(updates=[update(1, 777, "/pause@my_bot")])
        assert Telegram(cfg, transport=t).poll_commands()[0].name == "pause"

    def test_advances_the_offset_so_commands_run_once(self, cfg):
        t = FakeTransport(updates=[update(5, 777, "/pnl")])
        tg = Telegram(cfg, transport=t)
        assert len(tg.poll_commands()) == 1
        assert tg.poll_commands() == []
        assert t.calls[-1][1]["offset"] == 6

    def test_commands_can_be_disabled(self):
        cfg = TelegramConfig(enabled=True, bot_token="x", chat_id="777", allow_commands=False)
        t = FakeTransport(updates=[update(1, 777, "/flat")])
        assert Telegram(cfg, transport=t).poll_commands() == []
        assert t.calls == []

    def test_poll_failure_never_raises(self, cfg):
        tg = Telegram(cfg, transport=FakeTransport(fail=True))
        assert tg.poll_commands() == []
