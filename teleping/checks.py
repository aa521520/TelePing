"""Isolated, time-bounded Chromium navigation; never log target data."""

import asyncio
import contextlib
import ipaddress
import os
import socket
import time
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright

CONNECT_ATTEMPT_TIMEOUT = 3


class DestinationBlocked(Exception):
    pass


def _network_reason(error):
    if isinstance(error, socket.gaierror):
        return (
            "DNS_NOT_FOUND"
            if error.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", None))
            else "DNS_ERROR"
        )
    if isinstance(error, ConnectionRefusedError):
        return "CONNECTION_REFUSED"
    return "CONNECTION_ERROR"


def _authority_key(parsed):
    return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def _proxy_reason(proxy, url):
    key = _authority_key(urlsplit(url))
    return "DESTINATION_BLOCKED" if key in proxy.blocked_authorities else proxy.errors.get(key)


def _confirmed(reason):
    return reason in ("TLS_ERROR", "DESTINATION_BLOCKED", "DNS_NOT_FOUND", "CONNECTION_REFUSED")


async def _resolve(host, port):
    addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(item[4][0] for item in addresses))


async def _addresses(host, port, allow_private):
    if not host:
        raise DestinationBlocked()
    addresses = await _resolve(host, port)
    if not addresses or (not allow_private and any(not ipaddress.ip_address(a).is_global for a in addresses)):
        raise DestinationBlocked()
    return addresses


async def _connect_addresses(addresses, port, budget):
    """Try only already-validated numeric addresses within one connection budget."""
    expires = time.monotonic() + budget
    uncertain = False
    for index, address in enumerate(addresses):
        remaining = expires - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("CONNECTION_TIMEOUT")
        # Reserve a fair share for later candidates, including IPv4 after broken IPv6.
        attempt = min(CONNECT_ATTEMPT_TIMEOUT, remaining / (len(addresses) - index))
        try:
            async with asyncio.timeout(attempt):
                return await asyncio.open_connection(address, port)
        except (OSError, TimeoutError) as error:
            if not isinstance(error, ConnectionRefusedError):
                uncertain = True
    if uncertain:
        raise TimeoutError("CONNECTION_TIMEOUT")
    raise ConnectionRefusedError("ALL_ADDRESSES_REFUSED")


def _parts(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or not parsed.hostname:
        raise DestinationBlocked()
    return parsed


def _browser_environment():
    # Explicit allowlist: Actions Secrets must not enter the browser process.
    names = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "XDG_RUNTIME_DIR",
        "LANG",
        "LC_ALL",
    )
    return {name: os.environ[name] for name in names if name in os.environ}


class _PinnedProxy:
    """Resolve, validate and connect to the same numeric address (no DNS rebind window).

    CONNECT is a byte tunnel: Chromium still performs normal TLS verification.
    Plain HTTP connections are closed after a response to revalidate every host.
    """

    def __init__(self, allow_private, *, connect_budget=45):
        self.allow_private = allow_private
        self.connect_budget = connect_budget
        self.tasks = set()
        self.writers = set()
        self.blocked_authorities = set()
        self.errors = {}

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=65536)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *_):
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        upstream = None
        try:
            async with asyncio.timeout(60):
                raw = await reader.readuntil(b"\r\n\r\n")
                first, *headers = raw.decode("latin1").split("\r\n")
                method, target, version = first.split(" ", 2)
                parsed = _parts("https://" + target if method == "CONNECT" else target)
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                key = _authority_key(parsed)
                try:
                    addresses = await _addresses(parsed.hostname, port, self.allow_private)
                except DestinationBlocked:
                    self.blocked_authorities.add(key)
                    raise
                except OSError as error:
                    self.errors[key] = _network_reason(error)
                    raise
                # Numeric address is crucial: never resolve this name again at connect time.
                try:
                    remote, upstream = await _connect_addresses(addresses, port, self.connect_budget)
                except OSError as error:
                    self.errors[key] = _network_reason(error)
                    raise
                self.errors.pop(key, None)
                self.blocked_authorities.discard(key)
                self.writers.add(upstream)
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    safe_headers = [
                        h
                        for h in headers
                        if h
                        and h.split(":", 1)[0].lower()
                        not in ("connection", "proxy-connection", "proxy-authorization")
                    ]
                    upstream.write(
                        (
                            f"{method} {path} {version}\r\n"
                            + "\r\n".join(safe_headers)
                            + "\r\nConnection: close\r\n\r\n"
                        ).encode("latin1")
                    )
                    await upstream.drain()
                await writer.drain()

                async def pipe(source, destination):
                    try:
                        while data := await source.read(65536):
                            destination.write(data)
                            await destination.drain()
                    except (ConnectionError, OSError):
                        pass

                outbound = asyncio.create_task(pipe(reader, upstream))
                inbound = asyncio.create_task(pipe(remote, writer))
                try:
                    await asyncio.wait((outbound, inbound), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    outbound.cancel()
                    inbound.cancel()
                    await asyncio.gather(outbound, inbound, return_exceptions=True)
        except (
            TimeoutError,
            OSError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            DestinationBlocked,
        ):
            with contextlib.suppress(ConnectionError, OSError):
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
        finally:
            for connection in (writer, upstream):
                if connection:
                    self.writers.discard(connection)
                    connection.close()
            self.tasks.discard(task)


def _result(target, status="unmeasured", reason="NOT_STARTED"):
    return {
        "target": target,
        "status": status,
        "reason": reason,
        "final_url": target,
        "elapsed_ms": 0,
        "http_status": None,
        "redirects": [],
        "https_hosts": [],
        "entry_https": "unknown" if target.startswith("https:") else "not_requested",
    }


def _browser_reason(error):
    # Exception text is inspected locally and is never returned or logged.
    message = str(error)
    if any(code in message for code in ("ERR_CERT_", "ERR_SSL_")):
        return "TLS_ERROR"
    if "ERR_CONNECTION_REFUSED" in message:
        return "CONNECTION_REFUSED"
    if "ERR_NAME_NOT_RESOLVED" in message:
        return "DNS_NOT_FOUND"
    if any(
        code in message
        for code in ("ERR_CONNECTION_", "ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY_CONNECTION_FAILED")
    ):
        return "CONNECTION_ERROR"
    return "NAVIGATION_ERROR"


async def _check_one(browser, target, timeout, settle, deadline, allow_private, proxy):
    result = _result(target)
    # Do not start a queued site when even its minimum observation cannot fit.
    if deadline is not None and deadline - time.time() <= max(settle, 0.05):
        result["reason"] = "BATCH_DEADLINE"
        return result
    started = time.monotonic()
    budget = min(timeout, max(0, deadline - time.time())) if deadline is not None else timeout
    context = None
    blocked = False
    last_navigation = started
    last_error = None
    route_error = None
    document_status = None
    visible_since = None
    page = None
    try:
        async with asyncio.timeout(budget):
            initial = _parts(target)
            await _addresses(
                initial.hostname, initial.port or (443 if initial.scheme == "https" else 80), allow_private
            )
            context = await browser.new_context(accept_downloads=False, service_workers="block")
            # WebSockets cannot bypass request routing or the pinned proxy.
            await context.route_web_socket("**/*", lambda ws: ws.close())
            page = await context.new_page()

            async def guard(route):
                nonlocal blocked, route_error
                request = route.request
                try:
                    parts = _parts(request.url)
                    await _addresses(
                        parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), allow_private
                    )
                except DestinationBlocked:
                    if request.is_navigation_request() and request.frame == page.main_frame:
                        blocked = True
                    await route.abort("blockedbyclient")
                    return
                except OSError as error:
                    if request.is_navigation_request() and request.frame == page.main_frame:
                        route_error = _network_reason(error)
                    await route.abort("namenotresolved")
                    return
                if request.resource_type in ("media", "image", "font"):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", guard)

            def request_started(request):
                nonlocal last_navigation, document_status, last_error, visible_since, route_error
                if request.is_navigation_request() and request.frame == page.main_frame:
                    last_navigation = time.monotonic()
                    document_status = None
                    last_error = None
                    route_error = None
                    visible_since = None
                    if not result["redirects"] or result["redirects"][-1] != request.url:
                        result["redirects"].append(request.url)
                    parts = urlsplit(request.url)
                    if parts.scheme == "https" and parts.netloc not in result["https_hosts"]:
                        result["https_hosts"].append(parts.netloc)

            def response_received(response):
                nonlocal document_status, blocked
                request = response.request
                if request.is_navigation_request() and request.frame == page.main_frame:
                    document_status = response.status
                    result["http_status"] = response.status
                    if _proxy_reason(proxy, response.url) == "DESTINATION_BLOCKED":
                        blocked = True
                    if response.url == target and initial.scheme == "https":
                        result["entry_https"] = "ok"

            def document_committed(frame):
                nonlocal last_navigation, visible_since
                if frame == page.main_frame:
                    last_navigation = time.monotonic()
                    visible_since = None

            def request_failed(request):
                nonlocal last_error
                if request.is_navigation_request() and request.frame == page.main_frame:
                    # Aborted requests are common during JS navigation; allow a later navigation to replace it.
                    last_error = request.failure or "NAVIGATION_ERROR"

            page.on("request", request_started)
            page.on("response", response_received)
            page.on("framenavigated", document_committed)
            page.on("requestfailed", request_failed)
            page.on("download", lambda download: asyncio.create_task(download.cancel()))
            try:
                await page.goto(target, wait_until="commit", timeout=max(1, budget * 1000))
            except BrowserError as error:
                if "ERR_ABORTED" not in str(error):
                    raise
            while True:
                result["final_url"] = page.url if page.url != "about:blank" else target
                if blocked:
                    raise DestinationBlocked()
                stable = time.monotonic() - last_navigation >= settle
                if stable and document_status is not None and document_status >= 400:
                    reason = _proxy_reason(proxy, result["final_url"]) or "HTTP_STATUS"
                    result.update(
                        status="failed" if reason == "HTTP_STATUS" or _confirmed(reason) else "unconfirmed",
                        reason=reason,
                    )
                    break
                if last_error and time.monotonic() - last_navigation >= 0.2:
                    raise BrowserError(last_error)
                if document_status is not None and 200 <= document_status < 300:
                    try:
                        visible = await page.evaluate(r"""() => {
                            if (!document.body) return false;
                            const style = getComputedStyle(document.body);
                            const rect = document.body.getBoundingClientRect();
                            const text = document.body.innerText.trim();
                            const label = text.toLowerCase().replace(/[\s.。…!！]/g, '');
                            const loading = ['loading', 'pleasewait', '正在加载', '加载中',
                                '正在跳转', '跳转中', '请稍候', '请稍等'].includes(label);
                            return style.visibility !== 'hidden' && style.display !== 'none'
                                && Number(style.opacity) > 0 && rect.width > 0 && rect.height > 0
                                && text.length > 0 && !loading;
                        }""")
                    except BrowserError:
                        visible = False
                    if visible and visible_since is None:
                        visible_since = time.monotonic()
                    elif not visible:
                        visible_since = None
                    if stable and visible_since is not None and time.monotonic() - visible_since >= settle:
                        result.update(status="ok", reason="OK")
                        break
                await asyncio.sleep(0.1)
    except DestinationBlocked:
        result.update(status="failed", reason="DESTINATION_BLOCKED")
    except TimeoutError:
        attempted_url = result["redirects"][-1] if result["redirects"] else target
        confirmed_error = route_error or _proxy_reason(proxy, attempted_url)
        result.update(
            status="failed" if _confirmed(confirmed_error) else "unconfirmed",
            reason=confirmed_error
            if _confirmed(confirmed_error)
            else "BATCH_DEADLINE"
            if deadline is not None and time.time() >= deadline
            else "TIMEOUT",
        )
        if target.startswith("https:") and _confirmed(confirmed_error):
            result["entry_https"] = "failed"
    except OSError as error:
        reason = _network_reason(error)
        result.update(status="failed" if _confirmed(reason) else "unconfirmed", reason=reason)
        if target.startswith("https:") and _confirmed(reason):
            result["entry_https"] = "failed"
    except BrowserError as error:
        attempted_url = result["redirects"][-1] if result["redirects"] else target
        reason = (
            "DESTINATION_BLOCKED"
            if blocked
            else route_error or _proxy_reason(proxy, attempted_url) or _browser_reason(error)
        )
        result.update(
            status="failed" if _confirmed(reason) else "unconfirmed",
            reason=reason,
        )
        if initial.scheme == "https" and result["entry_https"] != "ok":
            result["entry_https"] = "failed" if _confirmed(reason) else "unknown"
    except Exception:  # noqa: BLE001 - isolate a broken page without exposing target-bearing errors
        result.update(status="unconfirmed", reason="CHECK_ERROR")
    finally:
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if context:
            with contextlib.suppress(Exception):
                await context.close()
    return result


async def _probe_https(browser, target, timeout, deadline, allow_private, proxy):
    result = await _check_one(browser, target, timeout, 0, deadline, allow_private, proxy)
    result["entry_https"] = {"ok": "ok", "failed": "failed"}.get(result["status"], "unknown")
    return result


async def _check_target(browser, target, timeout, settle, deadline, allow_private, proxy):
    started = time.monotonic()
    result = await _check_one(browser, target, timeout, settle, deadline, allow_private, proxy)
    if urlsplit(target).scheme == "http" and result["status"] == "ok":
        remaining = timeout - (time.monotonic() - started)
        if deadline is not None:
            remaining = min(remaining, deadline - time.time())
        if remaining > 0.1:
            parsed = urlsplit(target)
            host = parsed.hostname
            authority = f"[{host}]" if ":" in host else host
            probe_url = urlunsplit(("https", authority, parsed.path, parsed.query, ""))
            probe = await _probe_https(browser, probe_url, min(3, remaining), deadline, allow_private, proxy)
            result["entry_https"] = probe["entry_https"]
            result["https_hosts"] = list(
                dict.fromkeys(result["https_hosts"] + [authority] + probe["https_hosts"])
            )
        else:
            result["entry_https"] = "unknown"
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result


async def check_batch(targets, *, concurrency=5, timeout=45, settle=15, deadline=None, allow_private=False):
    """Check targets in input order; UTC deadline includes browser launch and queueing."""
    if not 1 <= concurrency <= 5 or timeout <= 0 or settle < 0:
        raise ValueError("INVALID_CHECK_OPTIONS")
    if not targets:
        return []
    if deadline is not None and time.time() >= deadline:
        return [dict(_result(t), reason="BATCH_DEADLINE") for t in targets]
    results = [None] * len(targets)
    cursor = 0

    async def run():
        async with (
            _PinnedProxy(allow_private, connect_budget=timeout) as proxy,
            async_playwright() as playwright,
        ):
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError()
            async with asyncio.timeout(max(0, deadline - time.time()) if deadline is not None else 30):
                browser = await playwright.chromium.launch(
                    headless=True,
                    env=_browser_environment(),
                    proxy={"server": proxy.url, "bypass": "<-loopback>"},
                    args=["--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
                )
            try:

                async def worker():
                    nonlocal cursor
                    while cursor < len(targets):
                        index = cursor
                        cursor += 1
                        results[index] = await _check_target(
                            browser, targets[index], timeout, settle, deadline, allow_private, proxy
                        )

                await asyncio.gather(*(worker() for _ in range(min(concurrency, len(targets)))))
            finally:
                await browser.close()

    try:
        await run()
    except Exception:  # noqa: BLE001 - a browser outage must return explicit unmeasured records
        for index, target in enumerate(targets):
            if results[index] is None:
                reason = (
                    "BATCH_DEADLINE"
                    if deadline is not None and time.time() >= deadline
                    else "BROWSER_UNAVAILABLE"
                )
                results[index] = _result(target, "unmeasured", reason)
    return results
