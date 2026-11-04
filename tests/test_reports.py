from copy import deepcopy
from datetime import UTC, datetime

_NOW = 1_730_000_010


def result(target="http://example.test", status="ok", **extra):
    return {
        "target": target,
        "status": status,
        "reason": "loaded" if status == "ok" else "navigation_failed",
        "final_url": target,
        "elapsed_ms": 1200,
        "http_status": 200 if status == "ok" else None,
        "redirects": [],
        "https_hosts": [],
        "entry_https": "ok",
        **extra,
    }


def finish(state, results, expected=None, round_id="round-1", now=_NOW):
    from teleping.reports import complete_round

    return complete_round(
        state,
        results,
        expected_targets=expected if expected is not None else ["http://example.test"],
        started=1_730_000_000,
        now=now,
        round_id=round_id,
    )


def test_summary_every_round_and_single_check_does_not_add_target(capsys):
    state = {"targets": [], "outbox": [{"id": "keep"}]}
    for round_id in ("one", "two"):
        messages = finish(state, [result()], round_id=round_id)
        assert len(messages) == 1
        assert all(
            word in messages[0]
            for word in (
                round_id,
                "北京时间",
                "总数：1",
                "已测：1",
                "正常：1",
                "异常：0",
                "待确认：0",
                "未测：0",
                "平均打开耗时",
                "整轮耗时",
                "新异常：0",
                "恢复：0",
                "到期提醒：0",
            )
        )
        assert "example.test" not in messages[0]
    assert state["targets"] == [] and state["outbox"] == [{"id": "keep"}]
    assert state["last_summary"]["round_id"] == "two"
    assert capsys.readouterr() == ("", "")


def test_300_results_and_missing_batch_statistics():
    targets = [f"http://site{i}.example.test" for i in range(300)]
    results = [
        result(
            t, status="ok" if i < 200 else "failed" if i < 230 else "unconfirmed" if i < 240 else "unmeasured"
        )
        for i, t in enumerate(targets[:250])
    ]
    state = {}
    finish(state, results, [*targets, targets[0]])
    summary = state["last_summary"]
    assert {k: summary[k] for k in ("total", "measured", "ok", "failed", "unconfirmed", "unmeasured")} == {
        "total": 300,
        "measured": 240,
        "ok": 200,
        "failed": 30,
        "unconfirmed": 10,
        "unmeasured": 60,
    }
    assert state["pending_targets"] == targets[240:]


def test_unknown_does_not_recover_confirmed_failure_and_recovery_once():
    state = {}
    assert len(finish(state, [result(status="failed")])) == 2
    repeated = finish(state, [result(status="failed")])
    assert len(repeated) == 2 and "持续异常：http://example.test" in repeated[1]
    assert state["last_summary"]["new_failures"] == 0
    for status in ("unconfirmed", "unmeasured"):
        pending = finish(state, [result(status=status)])
        assert not any("恢复正常" in m for m in pending)
        assert "异常待确认：http://example.test" in pending[1]
        assert "上次确认异常" in pending[1]
    recovered = finish(state, [result()])
    assert state["last_summary"]["recovered"] == 1
    assert len(recovered) == 2 and "恢复正常" in recovered[1]
    assert len(finish(state, [result()])) == 1


def test_missing_result_preserves_confirmed_failure():
    state = {}
    finish(state, [result(status="failed")])
    previous = deepcopy(state["site_states"]["http://example.test"])
    finish(state, [])
    assert (
        state["site_states"]["http://example.test"]["confirmed_status"]
        == previous["confirmed_status"]
        == "failed"
    )
    assert state["last_summary"]["unmeasured"] == 1


def test_entry_https_failure_does_not_create_a_report_message():
    state = {}
    for entry in ("failed", "failed", "ok", "failed"):
        messages = finish(state, [result(entry_https=entry)])
        assert state["last_summary"]["ok"] == 1 and state["last_summary"]["failed"] == 0
        assert len(messages) == 1
        assert "入口 HTTPS 提醒" not in "\n".join(messages)


def expiry(days, *, status="ok"):
    return {
        "domain": {
            "status": status,
            "days": days,
            "expires_at": datetime.fromtimestamp(_NOW + days * 86400, UTC).isoformat() if days is not None else None,
        },
        "certificates": [],
    }


def test_expiry_warning_is_listed_every_round_until_renewal():
    state = {}
    for days, details in [
        (31, 0),
        (30, 0),
        (29, 0),
        (16, 0),
        (15, 1),
        (14, 1),
        (13, 1),
        (7, 1),
        (3, 1),
        (1, 1),
        (0, 1),
        (-1, 1),
    ]:
        messages = finish(state, [result(expiry=expiry(days))])
        assert len(messages) == 1 + details
        assert state["last_summary"]["expiry_warnings"] == (0 if days > 15 else 1)
    assert len(finish(state, [result(expiry=expiry(None, status="unknown"))])) == 1
    assert state["last_summary"]["expiry_warnings"] == 0
    assert len(finish(state, [result(expiry=expiry(-2))])) == 2
    assert len(finish(state, [result(expiry=expiry(365))])) == 1
    assert len(finish(state, [result(expiry=expiry(15))])) == 2


def test_domain_and_certificate_warnings_count_targets_not_resources():
    state = {}
    value = {
        "domain": {"example.test": {"status": "ok", "days": 10, "expires_at": None}},
        "certificates": [{"host": "example.test", "status": "expired", "days": -1, "expires_at": None}],
    }
    messages = finish(state, [result(expiry=value)])
    assert state["last_summary"]["expiry_warnings"] == 1
    assert len(messages) == 2
    assert "域名" in messages[1] and "HTTPS" in messages[1]


def test_replaced_https_path_does_not_keep_removed_certificate_warning():
    state = {}
    value = {
        "domain": {"status": "unknown", "days": None},
        "certificates": [{"host": "old.example.test", "status": "ok", "days": 1}],
    }
    finish(state, [result(expiry=value)])
    assert state["last_summary"]["expiry_warnings"] == 1
    finish(state, [result(expiry={"domain": {"status": "unknown", "days": None}, "certificates": []})])
    assert state["last_summary"]["expiry_warnings"] == 0


def test_temporary_check_preserves_other_pending_work_and_ignores_extra_result():
    state = {"targets": ["http://saved.example.test"], "pending_targets": ["http://pending.example.test"]}
    finish(state, [result(), result("http://extraneous.example.test", status="failed")])
    assert state["pending_targets"] == ["http://pending.example.test"]
    assert "http://extraneous.example.test" not in state["site_states"]
    assert state["last_summary"]["new_failures"] == 0


def test_unknown_expiry_keeps_prior_threshold_for_next_known_result():
    state = {}
    finish(state, [result(expiry=expiry(7))])
    site = state["site_states"]["http://example.test"]
    assert site["expiry_thresholds"]["domain"] == 7
    finish(state, [result(expiry=expiry(None, status="unknown"))])
    assert site["expiry_thresholds"]["domain"] == 7
    assert len(finish(state, [result(expiry=expiry(6))])) == 2
    assert len(finish(state, [result(expiry=expiry(3))])) == 2


def test_300_failures_all_appear_in_details():
    targets = [f"http://site{i}.example.test" for i in range(300)]
    state = {}
    messages = finish(state, [result(target, status="failed") for target in targets], targets)
    assert state["last_summary"]["failed"] == 300
    assert state["last_summary"]["new_failures"] == 300
    assert len(messages) == 2
    assert all(target in messages[1] for target in targets)
    repeated = finish(state, [result(target, status="failed") for target in targets], targets, round_id="next")
    assert state["last_summary"]["new_failures"] == 0
    assert len(repeated) == 2 and all(target in repeated[1] for target in targets)


def test_300_failures_batch_into_two_logical_messages_and_few_telegram_chunks():
    import json

    import httpx

    from teleping.telegram import TelegramClient

    targets = [f"http://site{i}.example.test" for i in range(300)]
    messages = finish({}, [result(target, status="failed") for target in targets], targets)
    assert len(messages) == 2
    assert "普通检测汇总" in messages[0]
    assert all(target in messages[1] for target in targets)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": True})

    bot = TelegramClient(
        "test-secret", -123, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    for message in messages:
        bot.send(message)
    assert len(sent) <= 10
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 3500 for chunk in sent)
    assert "".join(sent) == "".join(messages)


def test_expired_certificate_without_date_is_listed_every_round_until_verified_renewal():
    state = {}

    def cert(status, days):
        return {
            "domain": {"status": "unknown", "days": None},
            "certificates": [{"host": "example.test", "status": status, "days": days, "expires_at": None}],
        }

    messages = finish(state, [result(expiry=cert("expired", None))])
    assert len(messages) == 2
    assert "证书已过期，具体日期无法确认" in messages[1]
    assert state["last_summary"]["expiry_warnings"] == 1
    assert len(finish(state, [result(expiry=cert("expired", None))])) == 2
    finish(state, [result(expiry=cert("unknown", None))])
    assert len(finish(state, [result(expiry=cert("expired", None))])) == 2
    finish(state, [result(expiry=cert("ok", 365))])
    assert state["last_summary"]["expiry_warnings"] == 0
    assert len(finish(state, [result(expiry=cert("expired", None))])) == 2


def test_changed_failure_reason_is_new_but_unchanged_failure_still_has_details():
    state = {}
    for reason, new_count in [("TIMEOUT", 1), ("TIMEOUT", 0), ("HTTP_500", 1), ("HTTP_500", 0)]:
        messages = finish(state, [result(status="failed", reason=reason)])
        assert len(messages) == 2 and reason in messages[1]
        assert state["last_summary"]["new_failures"] == new_count


def test_current_expiry_counts_do_not_keep_missing_or_unknown_previous_warnings():
    for current in [result(), result(expiry={}), result(expiry=expiry(None, status="unknown")), result(status="unmeasured")]:
        state = {}
        finish(state, [result(expiry=expiry(7))])
        messages = finish(state, [current])
        assert state["last_summary"]["expiry_warnings"] == 0
        assert not any("到期提醒：" in message for message in messages[1:])


def test_cached_expiry_date_is_recomputed_at_report_time_without_mutating_result():
    state = {}
    cached = expiry(1)
    messages = finish(state, [result(expiry=cached)], now=_NOW + 2 * 86400)
    assert "已到期 1 天" in messages[1]
    assert cached["domain"]["days"] == 1
    unknown = deepcopy(cached)
    unknown["domain"]["status"] = "unknown"
    assert len(finish(state, [result(expiry=unknown)], now=_NOW + 2 * 86400)) == 1
