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
    assert payload["groups"][0]["title"] == "private-group-canary"
    assert "private-user-canary" not in str(payload)
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


def test_cli_keeps_fixed_external_error_codes_without_printing_urls(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", generate_key())

    def fail(*args):
        raise RuntimeError("telegram_unavailable")

    monkeypatch.setattr(cli, "_setup", fail)
    assert cli.main(["setup", "--directory", str(tmp_path)]) == 1
    assert capsys.readouterr().err == "teleping_error: telegram_unavailable\n"


def test_cli_prints_safe_numeric_api_context_without_raw_description(tmp_path, monkeypatch, capsys):
    from teleping.telegram import TelegramFailure
    monkeypatch.setenv("STATE_ENCRYPTION_KEY", generate_key())

    def fail(*args):
        raise TelegramFailure("telegram_api_failed", method="getWebhookInfo", http_status=401, api_status=401)

    monkeypatch.setattr(cli, "_setup", fail)
    assert cli.main(["setup", "--directory", str(tmp_path)]) == 1
    assert capsys.readouterr().err == "teleping_error: telegram_api_failed; method=getWebhookInfo; http=401; api=401\n"


def test_remote_control_and_finish_use_no_legacy_services(tmp_path, monkeypatch, capsys):
    key = generate_key()
    monkeypatch.setenv('STATE_ENCRYPTION_KEY', key)
    monkeypatch.setenv('REALTIME_API_URL', 'https://service.example')
    monkeypatch.setenv('RUNNER_API_TOKEN', 'token-canary')
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    plan = {'run_id':'123-1', 'lease_token':'secret-lease', 'started':100, 'expected_targets':['private-canary'], 'batches':[['private-canary']]}
    class Service:
        def __init__(self, url, token, encryption_key):
            assert (url, token, encryption_key) == ('https://service.example','token-canary',key)
        def claim(self, run_id, force=False):
            assert run_id == '123-1' and force is True
            return plan
    def fail():
        raise AssertionError('legacy service called')
    def finish(service, actual_plan, results, cache, *, now):
        assert actual_plan == plan and results == [] and cache == {}
        return 1, 0
    monkeypatch.setattr(cli, 'RemoteService', Service)
    monkeypatch.setattr(cli, 'finish_remote', finish)
    monkeypatch.setattr(cli, '_services', fail)
    assert cli.main(['control','--directory',str(tmp_path),'--run-id','123-1','--force']) == 0
    assert decrypt((tmp_path/'plan.enc').read_bytes(),key,purpose='plan:123-1') == plan
    assert cli.main(['finish','--directory',str(tmp_path),'--run-id','123-1']) == 0
    output = capsys.readouterr()
    assert 'private-canary' not in output.out + output.err
    assert 'secret-lease' not in output.out + output.err
    assert 'token-canary' not in output.out + output.err


def test_realtime_setup_never_uses_legacy_telegram(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('STATE_ENCRYPTION_KEY', generate_key())
    monkeypatch.setenv('REALTIME_API_URL', 'https://service.example')
    monkeypatch.setenv('RUNNER_API_TOKEN', 'token-canary')
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    class Service:
        def __init__(self, url, token, key):
            assert url == 'https://service.example' and token == 'token-canary'
        def setup(self):
            pass
    def fail(*args):
        raise AssertionError('legacy called')
    monkeypatch.setattr(cli, 'RemoteService', Service)
    monkeypatch.setattr(cli, '_setup', fail)
    assert cli.main(['setup', '--directory', str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert 'canary' not in output.out + output.err
