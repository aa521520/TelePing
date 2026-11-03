from teleping import __main__ as cli
from teleping.crypto import decrypt, generate_key


def test_setup_registers_menu_but_only_writes_encrypted_private_identity(tmp_path, monkeypatch, capsys):
    key = generate_key()
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", key)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private-token-canary")

    class Telegram:
        def __init__(self, token, chat_id):
            assert token == "private-token-canary"
            assert chat_id == 0

        def webhook_url(self):
            return ""

        def register_menu(self, commands):
            assert {"add", "addmany", "list", "delete", "check", "checkone"} <= {item["command"] for item in commands}

        def get_updates(self, offset):
            assert offset == 0
            return [{"message": {"chat": {"type": "supergroup", "id": -123, "title": "private-group-canary"}, "from": {"id": 456, "first_name": "private-user-canary"}, "text": "private-message-must-not-be-stored"}}]

    monkeypatch.setattr(cli, "TelegramClient", Telegram)
    assert cli.main(["setup", "--directory", str(tmp_path)]) == 0
    encrypted = (tmp_path / "setup.enc").read_bytes()
    payload = decrypt(encrypted, key, purpose="setup")
    assert payload["groups"][0]["chat_id"] == -123
    assert payload["groups"][0]["users"]["456"]["name"] == "private-user-canary"
    assert "private-message-must-not-be-stored" not in str(payload)
    public_output = capsys.readouterr()
    for value in ("private-group-canary", "private-user-canary", "private-token-canary", key):
        assert value not in public_output.out + public_output.err
        assert value.encode() not in encrypted


def test_cli_never_prints_credential_bearing_exception(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", generate_key())

    def fail(*args):
        raise RuntimeError("https://api.telegram.org/botprivate-token-canary/sendMessage private-domain-canary.test")

    monkeypatch.setattr(cli, "_setup", fail)
    assert cli.main(["setup", "--directory", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert output.err == "teleping_error: operation_failed\n"
    assert "canary" not in output.out + output.err


def test_local_key_creation_does_not_print_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert cli.main(["key", "--directory", str(tmp_path)]) == 0
    key = (tmp_path / "encryption-key.txt").read_text()
    assert len(key) == 44
    assert key not in capsys.readouterr().out
