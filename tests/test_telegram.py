import json
from itertools import pairwise

import httpx
import pytest


def make_client(handler, **kwargs):
    from teleping.telegram import TelegramClient

    return TelegramClient(
        "test-secret", -100123, client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs
    )


def test_administrator_ids_refreshes_and_only_includes_human_administrators():
    replies = iter([
        [
            {"status": "creator", "user": {"id": 1, "is_bot": False}},
            {"status": "administrator", "user": {"id": 2, "is_bot": False}},
            {"status": "administrator", "user": {"id": 3, "is_bot": True}},
            {"status": "member", "user": {"id": 4, "is_bot": False}},
        ],
        [{"status": "creator", "user": {"id": 1, "is_bot": False}}],
        [],
    ])

    def handler(request):
        assert request.url.path.endswith("/getChatAdministrators")
        assert json.loads(request.content) == {"chat_id": -100123}
        return httpx.Response(200, json={"ok": True, "result": next(replies)})

    bot = make_client(handler)
    assert bot.administrator_ids() == {1, 2}
    assert bot.administrator_ids() == {1}
    assert bot.administrator_ids() == set()


@pytest.mark.parametrize("value", [None, {}, [None], [{}], [{"status": "creator"}],
    [{"status": "creator", "user": None}],
    *[{"status": "creator", "user": {"id": bad, "is_bot": False}} for bad in (True, "1", 0, -1)],
    {"status": "administrator", "user": {"id": 1, "is_bot": False}},
    [{"status": "creator", "user": {"id": 1}}],
    [{"status": "creator", "user": {"id": 1, "is_bot": "false"}}],
])
def test_administrator_ids_rejects_malformed_responses(value):
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": value})

    with pytest.raises(RuntimeError, match="^telegram_api_failed$"):
        make_client(handler).administrator_ids()


def test_administrator_ids_propagates_safe_api_failure(capsys):
    def handler(request):
        raise httpx.ConnectError("test-secret " + str(request.url), request=request)

    with pytest.raises(RuntimeError, match="^telegram_unavailable$") as error:
        make_client(handler).administrator_ids()
    assert error.value.__suppress_context__
    assert capsys.readouterr() == ("", "")


def test_bot_api_payloads_and_menu_scopes():
    requests = []

    def handler(request):
        assert request.method == "POST"
        payload = json.loads(request.content)
        requests.append((request.url.path.rsplit("/", 1)[-1], payload))
        results = {
            "getUpdates": [{"update_id": 4}],
            "getWebhookInfo": {"url": ""},
            "getMe": {"username": "example_bot"},
        }
        return httpx.Response(200, json={"ok": True, "result": results.get(requests[-1][0], True)})

    bot = make_client(handler)
    assert bot.get_updates(5) == [{"update_id": 4}]
    assert bot.webhook_url() == ""
    assert bot.bot_username() == "example_bot"
    commands = [{"command": "help", "description": "查看中文帮助，下轮任务回复"}]
    bot.register_menu(commands)
    assert requests == [
        ("getUpdates", {"offset": 5, "timeout": 0, "limit": 100, "allowed_updates": ["message"]}),
        ("getWebhookInfo", {}),
        ("getMe", {}),
        ("setMyCommands", {"commands": commands, "scope": {"type": "default"}}),
        ("setMyCommands", {"commands": commands, "scope": {"type": "chat", "chat_id": -100123}}),
        ("setChatMenuButton", {"menu_button": {"type": "commands"}}),
    ]


def test_send_splits_utf16_and_paces_across_calls(monkeypatch):
    from teleping import telegram

    clock = [10.0]
    monkeypatch.setattr(telegram.time, "monotonic", lambda: clock[0])
    sent = []
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def handler(request):
        sent.append((clock[0], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    bot = make_client(handler, sleep=sleep)
    text = "汉" * 3499 + "😀" * 1800 + "结束"
    bot.send(text)
    bot.send("下一条")
    assert "".join(item[1]["text"] for item in sent[:-1]) == text
    assert all(len(item[1]["text"].encode("utf-16-le")) // 2 <= 3500 for item in sent)
    assert all(item[1]["chat_id"] == -100123 and "parse_mode" not in item[1] for item in sent)
    assert all(b[0] - a[0] >= 3.2 - 1e-9 for a, b in pairwise(sent))
    assert len(sleeps) == len(sent) - 1


def test_rate_limit_retry_and_safe_failures(capsys):
    attempts = []
    sleeps = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "test-secret",
                    "parameters": {"retry_after": 2},
                },
            )
        return httpx.Response(200, json={"ok": True, "result": 7})

    assert make_client(handler, sleep=sleeps.append).call("getMe") == 7
    assert len(attempts) == 2 and sleeps == [2]

    for retry in (61, 1):
        attempts.clear()
        sleeps.clear()

        def limited(request, retry=retry):
            attempts.append(request)
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "test-secret",
                    "parameters": {"retry_after": retry},
                },
            )

        with pytest.raises(RuntimeError, match="^telegram_rate_limited$") as error:
            make_client(limited, sleep=sleeps.append).call("getMe")
        assert "test-secret" not in str(error.value)
        assert len(attempts) <= 3 and sum(sleeps) <= 60
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("kind", ["network", "invalid_url", "api", "invalid_json", "http_error"])
def test_errors_never_include_token_or_raw_description(kind, capsys):
    def handler(request):
        if kind == "network":
            raise httpx.ConnectError("test-secret " + str(request.url), request=request)
        if kind == "invalid_url":
            raise httpx.InvalidURL("test-secret")
        if kind == "invalid_json":
            return httpx.Response(200, text="test-secret")
        return httpx.Response(
            500 if kind == "http_error" else 200, json={"ok": False, "description": "test-secret"}
        )

    with pytest.raises(RuntimeError) as error:
        make_client(handler).call("getMe")
    assert str(error.value) in {"telegram_unavailable", "telegram_api_failed"}
    assert error.value.__suppress_context__
    assert capsys.readouterr() == ("", "")


def test_bootstrap_menu_does_not_register_chat_zero():
    from teleping.telegram import TelegramClient

    requests = []

    def handler(request):
        requests.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": True})

    bot = TelegramClient("test-secret", 0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    commands = [{"command": "help", "description": "帮助"}]
    bot.register_menu(commands)
    assert requests == [
        ("setMyCommands", {"commands": commands, "scope": {"type": "default"}}),
        ("setChatMenuButton", {"menu_button": {"type": "commands"}}),
    ]
