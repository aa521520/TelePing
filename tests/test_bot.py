"""Offline behavior checks for private Telegram commands."""

import importlib
import importlib.util
from copy import deepcopy

import pytest


def test_bot_handler_is_available():
    assert importlib.util.find_spec("teleping.bot") is not None, "bot command handler is not implemented"


@pytest.fixture
def bot():
    assert importlib.util.find_spec("teleping.bot") is not None, "bot command handler is not implemented"
    return importlib.import_module("teleping.bot")


@pytest.fixture
def state():
    return {
        "version": 1,
        "interval_minutes": 30,
        "last_update_id": -1,
        "targets": [],
        "forced_targets": [],
        "force_all": False,
        "pending_targets": [],
        "last_check_started": 0.0,
        "site_states": {},
        "expiry_cache": {},
        "outbox": [],
        "last_summary": {},
        "menu_registered": False,
    }


def update(text, *, chat=-100, user=42, **message_fields):
    return {
        "update_id": 12,
        "message": {
            "message_id": 1,
            "chat": {"id": chat, "type": "supergroup"},
            "from": {"id": user, "is_bot": False},
            "text": text,
            **message_fields,
        },
    }


def invoke(bot, state, text):
    return bot.handle_update(state, update(text), chat_id=-100, admin_ids={42}, bot_username="MonitorBot")


@pytest.mark.parametrize(
    "incoming",
    [
        update("/add example.test", chat=-200),
        update("/add example.test", user=7),
        update("/add example.test", sender_chat={"id": -100}),
        update("/add example.test", **{"from": {"id": 42, "is_bot": True}}),
        {"update_id": 12, "edited_message": update("/add example.test")["message"]},
        {"update_id": 12, "channel_post": update("/add example.test")["message"]},
        update("/add@OtherBot example.test"),
        update("/add@MonitorBot@OtherBot example.test"),
        update("普通消息 /add example.test"),
        {"update_id": 12, "message": {"text": "/add example.test"}},
    ],
)
def test_unauthorized_or_unaddressed_messages_have_no_reply_or_mutation(bot, state, incoming):
    original = deepcopy(state)
    assert bot.handle_update(state, incoming, chat_id=-100, admin_ids={42}, bot_username="MonitorBot") == []
    assert state == original


def test_own_mention_is_case_insensitive_and_update_offset_is_not_changed(bot, state):
    assert invoke(bot, state, "/add@monitorbot Example.TEST")
    assert state["targets"] == ["http://example.test/"]
    assert state["last_update_id"] == -1


def test_mentions_are_ignored_if_bot_identity_is_not_known(bot, state):
    assert bot.handle_update(state, update("/check@MonitorBot"), chat_id=-100, admin_ids={42}) == []
    assert not state["force_all"]


def test_add_normalizes_and_deduplicates_urls(bot, state):
    invoke(bot, state, "/add Example.TEST")
    invoke(bot, state, "/add http://example.test/")
    invoke(bot, state, "/add https://other.test:8443/path?q=1")
    assert state["targets"] == ["http://example.test/", "https://other.test:8443/path?q=1"]


def test_addmany_accepts_300_entries_and_list_returns_every_entry(bot, state):
    entries = [f"site{i}.example.test" for i in range(300)]
    invoke(bot, state, "/addmany\n" + "\n".join(entries))
    assert len(state["targets"]) == 300
    result = invoke(bot, state, "/list")
    text = "\n".join(result)
    assert all(f"http://site{i}.example.test/" in text for i in range(300))


def test_addmany_deduplicates_preserving_first_seen_order(bot, state):
    invoke(bot, state, "/add alpha.example.test")
    invoke(
        bot,
        state,
        "/addmany\n# entry list\nALPHA.example.test\n\nbeta.example.test\nhttp://beta.example.test/",
    )
    assert state["targets"] == ["http://alpha.example.test/", "http://beta.example.test/"]


@pytest.mark.parametrize("command", ["/add", "/addmany", "/delete", "/checkone", "/interval"])
def test_missing_argument_explains_usage_without_mutation(bot, state, command):
    original = deepcopy(state)
    reply = "\n".join(invoke(bot, state, command))
    assert command in reply
    assert state == original
    if command == "/addmany":
        assert "同一条" in reply and "换行" in reply


@pytest.mark.parametrize(
    "invalid",
    [
        "https://user:private@example.test/",
        "file:///tmp/data",
        "localhost",
        "http://127.0.0.1",
        "https://example.test/#fragment",
    ],
)
def test_invalid_batch_is_atomic_and_does_not_echo_private_input(bot, state, invalid):
    state["targets"] = ["http://kept.example.test/"]
    original = deepcopy(state)
    reply = "\n".join(invoke(bot, state, "/addmany\nnew.example.test\n" + invalid))
    assert state == original
    assert "无效" in reply or "格式" in reply
    assert invalid not in reply and "private" not in reply


@pytest.mark.parametrize(
    "command",
    [
        "/add alpha.example.test beta.example.test",
        "/delete alpha.example.test beta.example.test",
        "/checkone alpha.example.test beta.example.test",
    ],
)
def test_single_target_commands_reject_extra_arguments(bot, state, command):
    original = deepcopy(state)
    assert invoke(bot, state, command)
    assert state == original


def test_delete_clears_all_references_but_keeps_history(bot, state):
    target = "http://example.test/"
    kept = "http://kept.example.test/"
    state.update(
        targets=[target, kept], pending_targets=[target, kept, target], forced_targets=[target, kept]
    )
    state["site_states"][target] = {"status": "failed"}
    invoke(bot, state, "/delete EXAMPLE.TEST")
    assert state["targets"] == [kept]
    assert state["pending_targets"] == [kept]
    assert state["forced_targets"] == [kept]
    assert state["site_states"][target] == {"status": "failed"}


def test_delete_unknown_target_does_not_change_list(bot, state):
    state["targets"] = ["http://example.test/"]
    original = deepcopy(state)
    assert invoke(bot, state, "/delete absent.example.test")
    assert state == original


def test_delete_also_cancels_a_temporary_target_without_long_term_monitoring(bot, state):
    state["forced_targets"] = ["http://example.test/"]
    state["pending_targets"] = ["http://example.test/"]
    invoke(bot, state, "/delete example.test")
    assert state["forced_targets"] == []
    assert state["pending_targets"] == []
    assert state["targets"] == []


def test_force_all_only_schedules_next_run(bot, state):
    state["targets"] = ["http://example.test/"]
    reply = "\n".join(invoke(bot, state, "/check"))
    assert state["force_all"] is True
    assert state["last_check_started"] == 0.0
    assert "下轮" in reply and "立即" in reply


def test_force_one_deduplicates_and_does_not_force_all(bot, state):
    state["targets"] = ["http://example.test/"]
    invoke(bot, state, "/checkone Example.TEST")
    reply = "\n".join(invoke(bot, state, "/checkone http://example.test/"))
    assert state["forced_targets"] == ["http://example.test/"]
    assert state["force_all"] is False
    assert "下轮" in reply and "立即" in reply


def test_force_one_unknown_target_is_temporary_without_adding_to_monitoring(bot, state):
    reply = "\n".join(invoke(bot, state, "/checkone example.test"))
    assert state["forced_targets"] == ["http://example.test/"]
    assert state["targets"] == []
    assert "下轮" in reply


@pytest.mark.parametrize("minutes", [30, 60, 120, 360, 720, 1440])
def test_interval_accepts_supported_minutes(bot, state, minutes):
    reply = "\n".join(invoke(bot, state, f"/interval {minutes}"))
    assert state["interval_minutes"] == minutes
    assert str(minutes) in reply and "下轮" in reply


@pytest.mark.parametrize("argument", ["0", "29", "31", "1441", "-30", "30.0", "30 60", "abc"])
def test_interval_rejects_unsupported_or_malformed_arguments(bot, state, argument):
    original = deepcopy(state)
    assert invoke(bot, state, "/interval " + argument)
    assert state == original


def test_help_start_settings_and_status_are_read_only_and_preserve_unknown_results(bot, state):
    state["site_states"] = {"http://example.test/": {"status": "future-status"}}
    original = deepcopy(state)
    help_text = "\n".join(invoke(bot, state, "/help"))
    assert invoke(bot, state, "/start") == invoke(bot, state, "/help")
    for item in bot.COMMANDS:
        assert "/" + item["command"] in help_text
    assert "下轮" in help_text and "立即" in help_text
    assert "30" in "\n".join(invoke(bot, state, "/settings"))
    assert invoke(bot, state, "/status")
    assert state == original


def test_command_menu_is_chinese_and_explains_scheduled_processing(bot):
    menu = {entry["command"]: entry["description"] for entry in bot.COMMANDS}
    assert {
        "add",
        "addmany",
        "list",
        "delete",
        "check",
        "checkone",
        "settings",
        "interval",
        "status",
        "help",
        "start",
    } <= menu.keys()
    for command, description in menu.items():
        assert 1 <= len(description) <= 256
        assert command.isascii() and not command.startswith("/")
        assert any("\u4e00" <= char <= "\u9fff" for char in description)
    for command in ("check", "checkone"):
        assert "下轮" in menu[command] and "立即" in menu[command]
    assert "下轮" in menu["interval"]


@pytest.mark.parametrize(
    "command",
    [
        "/check unexpected",
        "/list unexpected",
        "/settings unexpected",
        "/help unexpected",
        "/status unexpected",
    ],
)
def test_no_argument_commands_reject_trailing_input(bot, state, command):
    original = deepcopy(state)
    assert invoke(bot, state, command)
    assert state == original
