from copy import deepcopy

import pytest

from teleping.reports import complete_round

NOW = 1_800_000_000


def measured(target, *, certs=(), domain=None, status="ok", reason="OK", **extra):
    return {
        "target": target, "status": status, "reason": reason,
        "expiry": {"domain": domain or {"status": "unknown"}, "certificates": list(certs)},
        **extra,
    }


def cert(days=None, *, host="redirect.example.test:40001", status="ok"):
    return {"host": host, "status": status, "days": days}


def finish(state, results, targets=None):
    return complete_round(
        state, results, expected_targets=targets or [r["target"] for r in results],
        started=NOW - 10, now=NOW, round_id="compact-test",
    )


def test_expiry_is_grouped_by_kind_and_sorted_with_one_original_target_per_group():
    results = [
        measured("http://b.test/", certs=[cert(5), cert(2)], domain={"status": "ok", "days": 10}),
        measured("http://a.test/", certs=[cert(2)], domain={"status": "ok", "days": 2}),
        measured("http://c.test/", certs=[cert(-3, status="expired")]),
        measured("http://outside.test/", certs=[cert(16)]),
    ]
    state = {}
    messages = finish(state, results)
    assert messages[1] == "到期提醒（HTTPS 证书）：\nc.test 已过期3天\na.test 剩2天\nb.test 剩2天"
    assert messages[2] == "到期提醒（域名）：\na.test 剩2天\nb.test 剩10天"
    assert len(messages) == 3
    assert "40001" not in "\n".join(messages) and "提醒阈值" not in "\n".join(messages)
    assert state["last_summary"]["expiry_warnings"] == 3
    assert finish(state, results)[1:] == messages[1:]


def test_expired_unknown_date_remains_explicit_without_invented_days():
    messages = finish({}, [
        measured("https://b.test/", certs=[cert(1)]),
        measured("https://a.test/", certs=[cert(status="expired"), cert(2)]),
        measured("https://unknown.test/", certs=[cert(status="unknown")]),
    ])
    assert messages[1] == "到期提醒（HTTPS 证书）：\na.test 已过期（日期不明）\nb.test 剩1天"


def test_failures_group_chinese_reasons_without_leaking_raw_error_text():
    state = {}
    results = [
        measured("http://b.test/", status="failed", reason="CONNECTION_REFUSED"),
        measured("http://a.test/", status="failed", reason="CONNECTION_REFUSED"),
        measured("https://tls.test/", status="failed", reason="TLS_ERROR"),
        measured("http://missing.test/", status="failed", reason="HTTP_STATUS", http_status=404),
        measured("http://other.test/", status="failed", reason="PRIVATE_EXCEPTION_CANARY"),
    ]
    messages = finish(state, results)
    assert "异常域名（服务器拒绝连接）：\na.test\nb.test" in messages
    assert any("异常域名（HTTPS 证书或加密连接异常）" in m for m in messages)
    assert any("页面不存在（404）" in m for m in messages)
    assert not any("PRIVATE_EXCEPTION_CANARY" in m or "TLS_ERROR" in m for m in messages)
    assert finish(state, results)[1:] == messages[1:]
    assert state["last_summary"]["new_failures"] == 0


def test_unconfirmed_stays_separate_and_does_not_recover_a_previous_failure():
    state = {}
    finish(state, [measured("http://a.test/", status="failed", reason="CONNECTION_REFUSED")])
    messages = finish(state, [measured("http://a.test/", status="unconfirmed", reason="TIMEOUT")])
    assert any(m.startswith("待确认（访问超时）：") for m in messages)
    assert any("a.test（上次：服务器拒绝连接；尚未确认恢复）" in m for m in messages)
    assert state["last_summary"]["failed"] == 0
    assert state["last_summary"]["recovered"] == 0
    recovered = finish(state, [measured("http://a.test/")])
    assert recovered[1] == "恢复正常：\na.test"
    assert len(finish(state, [measured("http://a.test/")])) == 1


def test_labels_preserve_entry_ports_paths_and_disambiguate_http_from_https():
    targets = ["http://same.test/", "https://same.test/", "https://same.test:8443/path?q=1"]
    messages = finish({}, [measured(t, certs=[cert(3)]) for t in targets])
    assert messages[1].splitlines()[1:] == [
        "http://same.test/ 剩3天", "https://same.test/ 剩3天", "same.test:8443/path?q=1 剩3天",
    ]


def test_many_targets_paginate_on_lines_repeat_heading_and_keep_numeric_order():
    results = [measured(f"http://long-domain-{i:03d}.example.test/", certs=[cert(i % 15)]) for i in range(300)]
    messages = finish({}, list(reversed(results)))
    pages = messages[1:]
    assert len(pages) > 1
    rows = []
    for page in pages:
        assert page.startswith("到期提醒（HTTPS 证书）：\n")
        assert len(page.encode("utf-16-le")) // 2 <= 3500
        rows.extend(page.splitlines()[1:])
    assert len(rows) == 300 and len(set(rows)) == 300
    assert all(" 剩" in row for row in rows)
    assert [int(row.rsplit("剩", 1)[1][:-1]) for row in rows] == sorted(i % 15 for i in range(300))


def test_formatting_keeps_results_unchanged_and_emits_no_console_output(capsys):
    results = [measured("http://a.test/", certs=[cert(1)], status="failed", reason="DNS_NOT_FOUND")]
    before = deepcopy(results)
    finish({}, results)
    assert results == before
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(("code", "text"), [
    ("DNS_ERROR", "域名解析异常"), ("CONNECTION_ERROR", "网络连接异常"),
    ("NAVIGATION_ERROR", "页面打开失败"), ("CHECK_ERROR", "检测过程出错"),
    ("DESTINATION_BLOCKED", "目标地址被检测规则拦截"),
    ("BATCH_DEADLINE", "本批检测时间已用完"), ("ROUND_DEADLINE", "本轮检测时间已用完"),
    ("BROWSER_UNAVAILABLE", "浏览器检测未能完成"), ("BATCH_INCOMPLETE", "未收到检测结果"),
    ("result_missing", "未收到检测结果"), ("NOT_STARTED", "尚未开始检测"),
])
def test_inconclusive_reasons_are_chinese_and_remain_inconclusive(code, text):
    state = {}
    messages = finish(state, [measured("http://a.test/", status="unconfirmed", reason=code)])
    assert messages[1] == f"待确认（{text}）：\na.test"
    assert state["last_summary"]["failed"] == 0


def test_single_long_unicode_entry_remains_within_utf16_limit():
    target = "http://a.test/" + "😀" * 2100
    messages = finish({}, [measured(target, certs=[cert(1)])])
    assert len(messages) > 2
    assert all(len(m.encode("utf-16-le")) // 2 <= 3500 for m in messages)
    assert "".join(m.split("\n", 1)[1] for m in messages[1:]) == "a.test/" + "😀" * 2100 + " 剩1天"
