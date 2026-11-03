import asyncio
import time
from contextlib import asynccontextmanager

from teleping.checks import check_batch


@asynccontextmanager
async def fixture_site():
    async def handle(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            path = request.split(b" ")[1].decode()
            status = "200 OK"
            headers = ""
            if path == "/http":
                status, headers, body = "302 Found", "Location: /js\r\n", "redirect"
            elif path == "/js":
                body = '<body>Loading<script>setTimeout(()=>location.href="/meta",350)</script>'
            elif path == "/meta":
                body = '<meta http-equiv="refresh" content="0.35;url=/final"><body>Loading'
            elif path == "/blank":
                body = "<html><body><script>setInterval(()=>{},100)</script></body></html>"
            elif path == "/loading":
                body = "<html><body>正在加载...</body></html>"
            elif path == "/404":
                status, body = "404 Not Found", "Missing page"
            elif path == "/slow":
                await asyncio.sleep(3)
                body = "late"
            elif path == "/delayed":
                await asyncio.sleep(0.6)
                body = "<body>Delayed real document</body>"
            elif path == "/latebody":
                body = '<body><script>setTimeout(()=>document.body.innerText="Visible final content",600)</script></body>'
            elif path == "/404redirect":
                status = "404 Not Found"
                body = '<body>Redirecting<script>setTimeout(()=>location.href="/final",350)</script></body>'
            elif path == "/private":
                status, headers, body = (
                    "302 Found",
                    "Location: http://127.0.0.1:12345/private\r\n",
                    "redirect",
                )
            else:
                body = "<html><body>Final visible content</body></html>"
            payload = body.encode()
            writer.write(
                f"HTTP/1.1 {status}\r\n{headers}Content-Type: text/html; charset=utf-8\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"


async def test_real_browser_follows_delayed_js_meta_and_http():
    async with fixture_site() as site:
        started = time.monotonic()
        [result] = await check_batch([site + "/http"], settle=0.8, timeout=8, allow_private=True)
    assert result["status"] == "ok"
    assert result["final_url"] == site + "/final"
    assert result["redirects"] == [site + "/http", site + "/js", site + "/meta", site + "/final"]
    assert time.monotonic() - started >= 1.2
    assert result["http_status"] == 200


async def test_blank_and_404_cannot_be_success_and_one_bad_site_is_isolated():
    async with fixture_site() as site:
        results = await check_batch(
            [site + "/blank", site + "/404", site + "/final"], settle=0.2, timeout=2.5, allow_private=True
        )
    assert [r["status"] for r in results] == ["unconfirmed", "failed", "ok"]
    assert results[1]["reason"] == "HTTP_STATUS"


async def test_deadline_leaves_queued_targets_explicitly_unmeasured():
    async with fixture_site() as site:
        results = await check_batch(
            [site + "/slow", site + "/final"],
            concurrency=1,
            timeout=8,
            settle=0.2,
            deadline=time.time() + 1.5,
            allow_private=True,
        )
    assert results[0]["status"] == "unconfirmed"
    assert results[1]["status"] == "unmeasured"


async def test_private_destination_blocked_by_default():
    [result] = await check_batch(["http://127.0.0.1/private"], settle=0.1, timeout=2)
    assert result["status"] == "failed"
    assert result["reason"] == "DESTINATION_BLOCKED"


async def test_dns_private_answer_blocked_without_network(monkeypatch):
    from teleping import checks

    async def resolve(host, port):
        return ["10.0.0.1"]

    monkeypatch.setattr(checks, "_resolve", resolve)
    [result] = await check_batch(["http://example.test"], timeout=2)
    assert result["reason"] == "DESTINATION_BLOCKED"


def test_browser_environment_excludes_secrets(monkeypatch):
    from teleping.checks import _browser_environment

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-test-only")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "secret-test-only")
    env = _browser_environment()
    assert "TELEGRAM_BOT_TOKEN" not in env
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in env


async def test_stability_begins_when_document_commits_not_when_request_started():
    async with fixture_site() as site:
        [result] = await check_batch([site + "/delayed"], settle=0.6, timeout=4, allow_private=True)
    assert result["status"] == "ok"
    assert result["elapsed_ms"] >= 1100


async def test_redirect_to_private_destination_is_blocked(monkeypatch):
    from teleping import checks

    original = checks._addresses
    async with fixture_site() as site:
        fixture_port = int(site.rsplit(":", 1)[1])

        async def allow_fixture_only(host, port, allow_private):
            return await original(host, port, port == fixture_port)

        monkeypatch.setattr(checks, "_addresses", allow_fixture_only)
        [result] = await check_batch([site + "/private"], settle=0.2, timeout=4)
    assert result["status"] == "failed"
    assert result["reason"] == "DESTINATION_BLOCKED"


async def test_deadline_applies_while_browser_is_starting(monkeypatch):
    from playwright.async_api import BrowserType

    async def slow_launch(*args, **kwargs):
        await asyncio.sleep(2)

    monkeypatch.setattr(BrowserType, "launch", slow_launch)
    started = time.monotonic()
    [result] = await check_batch(["http://example.test"], deadline=time.time() + 0.2)
    assert time.monotonic() - started < 1
    assert result["status"] == "unmeasured"
    assert result["reason"] == "BATCH_DEADLINE"


async def test_explicit_https_with_untrusted_certificate_never_recovers_via_http(tmp_path):
    from fixtures.tls_site import tls_site

    async with tls_site(tmp_path) as site:
        target = f"https://127.0.0.1:{site['port']}/"
        [result] = await check_batch([target], settle=0.1, timeout=4, allow_private=True)
    assert result["status"] == "failed"
    assert result["reason"] == "TLS_ERROR"
    assert result["entry_https"] == "failed"
    assert result["https_hosts"] == [f"127.0.0.1:{site['port']}"]
    assert all(url.startswith("https://") for url in result["redirects"])


async def test_visible_body_receives_full_observation_time():
    async with fixture_site() as site:
        [result] = await check_batch([site + "/latebody"], settle=0.6, timeout=4, allow_private=True)
    assert result["status"] == "ok"
    assert result["elapsed_ms"] >= 1100


async def test_error_document_can_redirect_during_observation():
    async with fixture_site() as site:
        [result] = await check_batch([site + "/404redirect"], settle=0.6, timeout=4, allow_private=True)
    assert result["status"] == "ok"
    assert result["final_url"] == site + "/final"


async def test_route_errors_do_not_escape_as_target_bearing_async_errors(monkeypatch):
    from teleping import checks

    original = checks._addresses
    calls = 0
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    try:
        async with fixture_site() as site:

            async def malformed_after_initial(host, port, allow_private):
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise ValueError("example.test-private-path")
                return await original(host, port, True)

            monkeypatch.setattr(checks, "_addresses", malformed_after_initial)
            [result] = await check_batch([site], timeout=1, settle=0.1, allow_private=True)
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)
    assert result["status"] != "ok"
    assert errors == []


async def test_http_success_survives_failed_standard_https_probe(monkeypatch):
    from teleping import checks

    probes = []

    async def failed_probe(browser, target, timeout, deadline, allow_private, proxy):
        probes.append((target, timeout))
        return {"entry_https": "failed", "https_hosts": ["127.0.0.1"]}

    monkeypatch.setattr(checks, "_probe_https", failed_probe, raising=False)
    async with fixture_site() as site:
        [result] = await check_batch([site + "/final"], timeout=5, settle=0.1, allow_private=True)
    assert result["status"] == "ok"
    assert result["entry_https"] == "failed"
    assert probes[0][0] == "https://127.0.0.1/final"
    assert 0 < probes[0][1] <= 3


async def test_nxdomain_is_failed_but_dns_timeout_is_unconfirmed(monkeypatch):
    import socket

    from teleping import checks

    async def missing(host, port):
        raise socket.gaierror(socket.EAI_NONAME, "example.test")

    monkeypatch.setattr(checks, "_resolve", missing)
    [result] = await check_batch(["http://example.test"], timeout=1, settle=0.1)
    assert result["status"] == "failed"
    assert result["reason"] == "DNS_NOT_FOUND"

    async def delayed(host, port):
        raise socket.gaierror(socket.EAI_AGAIN, "example.test")

    monkeypatch.setattr(checks, "_resolve", delayed)
    [result] = await check_batch(["http://example.test"], timeout=1, settle=0.1)
    assert result["status"] == "unconfirmed"


async def test_connection_refused_is_confirmed_failure():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    [result] = await check_batch([f"http://127.0.0.1:{port}"], timeout=5, settle=0.1, allow_private=True)
    assert (result["status"], result["reason"]) == ("failed", "CONNECTION_REFUSED")


async def test_loading_placeholder_is_not_final_visible_content():
    async with fixture_site() as site:
        [result] = await check_batch([site + "/loading"], timeout=1, settle=0.1, allow_private=True)
    assert result["status"] == "unconfirmed"


async def test_transient_dns_during_browser_request_stays_unconfirmed(monkeypatch):
    import socket

    from teleping import checks

    original = checks._addresses
    attempts = 0
    async with fixture_site() as site:
        port = int(site.rsplit(":", 1)[1])

        async def transient(host, target_port, allow_private):
            nonlocal attempts
            if host == "127.0.0.1" and target_port == port:
                attempts += 1
                if attempts > 1:
                    raise socket.gaierror(socket.EAI_AGAIN, "example.test")
            return await original(host, target_port, True)

        monkeypatch.setattr(checks, "_addresses", transient)
        [result] = await check_batch([site], timeout=2, settle=0.1, allow_private=True)
    assert result["status"] == "unconfirmed"


async def test_proxy_uses_second_public_dns_address_after_first_connection_fails(monkeypatch):
    from urllib.parse import urlsplit

    from teleping import checks

    real_connect = asyncio.open_connection
    tried = []
    async with fixture_site() as site:
        fixture_port = int(site.rsplit(":", 1)[1])

        async def resolve(host, port):
            if host != "example.test":
                raise ValueError("UNEXPECTED_FIXTURE_HOST")
            return ["8.8.8.8", "1.1.1.1"]

        async def connect(host, port, **kwargs):
            if host in ("8.8.8.8", "1.1.1.1"):
                tried.append(host)
                if host == "8.8.8.8":
                    raise ConnectionRefusedError("FIXTURE_FIRST_ADDRESS_REFUSED")
                return await real_connect("127.0.0.1", fixture_port, **kwargs)
            return await real_connect(host, port, **kwargs)

        monkeypatch.setattr(checks, "_resolve", resolve)
        monkeypatch.setattr(asyncio, "open_connection", connect)
        async with checks._PinnedProxy(False) as proxy:
            parsed = urlsplit(proxy.url)
            reader, writer = await real_connect(parsed.hostname, parsed.port)
            writer.write(
                b"GET http://example.test/final HTTP/1.1\r\nHost: example.test\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 2)
            writer.close()
            await writer.wait_closed()
    assert response.startswith(b"HTTP/1.1 200 OK")
    assert b"Final visible content" in response
    assert tried == ["8.8.8.8", "1.1.1.1"]
