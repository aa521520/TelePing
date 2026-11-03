import base64
import json

import httpx
import pytest

from teleping.crypto import decrypt, encrypt, generate_key
from teleping.state import GitHubStateStore, new_state
from teleping.targets import normalize_target, parse_targets


def test_targets_preserve_intended_entry_and_deduplicate():
    assert normalize_target(" EXAMPLE.TEST ") == "http://example.test/"
    assert normalize_target("https://Example.test:8443/Path?q=1") == "https://example.test:8443/Path?q=1"
    assert parse_targets("example.test\n# comment\nhttp://example.test/\nother.test") == [
        "http://example.test/", "http://other.test/"
    ]


@pytest.mark.parametrize("value", ["file:///x", "javascript:alert(1)", "http://user:pass@example.test", "localhost", "http://127.0.0.1", "http://169.254.169.254/", "example.test#fragment", "bad domain.test", "http://[::1]/"])
def test_unsafe_targets_are_rejected_without_echo(value):
    with pytest.raises(ValueError) as error:
        normalize_target(value)
    assert str(error.value) == "invalid_target"


def test_secret_ciphertext_authenticates_purpose_and_never_contains_canary():
    key = generate_key()
    secret = {"targets": ["http://private-canary.test/"], "outbox": [{"text": "secret-canary"}]}
    encrypted = encrypt(secret, key, purpose="state")
    assert b"private-canary" not in encrypted
    assert decrypt(encrypted, key, purpose="state") == secret
    assert encrypt(secret, key, purpose="state") != encrypted
    with pytest.raises(ValueError, match="encrypted_data_invalid"):
        decrypt(encrypted, key, purpose="batch:1:0")
    payload = json.loads(encrypted)
    payload["ciphertext"] = base64.urlsafe_b64encode(b"tampered").decode()
    with pytest.raises(ValueError, match="encrypted_data_invalid"):
        decrypt(json.dumps(payload).encode(), key, purpose="state")


def test_seed_is_only_used_to_initialize_state():
    state = new_state("example.test\nother.test")
    assert state["targets"] == ["http://example.test/", "http://other.test/"]
    assert state["interval_minutes"] == 30
    assert state["last_update_id"] == -1


def test_store_roundtrip_saves_only_ciphertext_and_uses_compare_and_swap():
    key = generate_key()
    server = {"content": None, "sha": None, "writes": []}

    def serve(request):
        if request.url.path.endswith("/git/ref/heads/teleping-state"):
            return httpx.Response(200, json={"object": {"sha": "head"}})
        if request.method == "GET":
            if server["content"] is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"sha": server["sha"], "content": server["content"]})
        body = json.loads(request.content)
        server["writes"].append(body)
        if server["sha"] and body.get("sha") != server["sha"]:
            return httpx.Response(409, json={})
        server["content"] = body["content"]
        server["sha"] = "sha" + str(len(server["writes"]))
        return httpx.Response(200, json={"content": {"sha": server["sha"]}})

    client = httpx.Client(transport=httpx.MockTransport(serve))
    store = GitHubStateStore("owner/project", "test-token", key, client=client)
    assert store.load() is None
    state = new_state("private-canary.test")
    store.save(state)
    assert store.load()["targets"] == ["http://private-canary.test/"]
    state["targets"] = []
    store.save(state)
    assert server["writes"][1]["sha"] == "sha1"
    assert b"private-canary" not in base64.b64decode(server["content"])
    assert store.load()["targets"] == []


def test_corrupt_stored_state_does_not_become_missing_state():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"sha": "bad", "content": base64.b64encode(b"corrupted").decode()})))
    store = GitHubStateStore("owner/project", "test-token", generate_key(), client=client)
    with pytest.raises(ValueError, match="encrypted_data_invalid"):
        store.load()


def test_large_state_uses_blob_when_contents_api_omits_inline_data():
    key = generate_key()
    ciphertext = encrypt(new_state("large-state-canary.test"), key, purpose="state")

    def serve(request):
        if request.url.path.endswith("/git/blobs/large-sha"):
            return httpx.Response(200, json={"content": base64.b64encode(ciphertext).decode(), "encoding": "base64"})
        return httpx.Response(200, json={"sha": "large-sha", "content": "", "encoding": "none"})

    store = GitHubStateStore("owner/project", "test-token", key, client=httpx.Client(transport=httpx.MockTransport(serve)))
    assert store.load()["targets"] == ["http://large-state-canary.test/"]
