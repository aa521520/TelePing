"""List current private warnings every round and retain transition history without I/O."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

_THRESHOLDS = (0, 1, 3, 7, 14, 15)
_BEIJING = timezone(timedelta(hours=8))


def _expiry_resources(expiry: dict):
    domain = expiry.get("domain", {})
    if isinstance(domain, dict):
        if "status" in domain:
            yield "domain", "域名", domain
        else:
            for name, value in domain.items():
                if isinstance(value, dict):
                    yield f"domain:{name}", f"域名 {name}", value
    for item in expiry.get("certificates", []):
        if isinstance(item, dict) and isinstance(item.get("host"), str):
            yield "certificate:" + item["host"], "HTTPS 证书 " + item["host"], item


def _remaining_days(value: dict, now: float) -> int | float | None:
    date = value.get("expires_at")
    if date is not None:
        try:
            expires = datetime.fromisoformat(date)
            if expires.tzinfo is None:
                return None
            return math.floor((expires.timestamp() - now) / 86400)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    days = value.get("days")
    if isinstance(days, bool) or not isinstance(days, (int, float)) or not math.isfinite(days):
        return None
    return days


def _expiry_details(site: dict, expiry: dict, now: float) -> list[dict]:
    thresholds = site.setdefault("expiry_thresholds", {})
    known = site.setdefault("expiry_known", {})
    # An updated redirect route may no longer use a previously seen certificate.
    for key in known:
        known[key] = False
    details = []
    for key, _label, value in _expiry_resources(expiry):
        kind = "certificate" if key.startswith("certificate:") else "domain"
        if value.get("status") not in {"ok", "expired"}:
            continue
        days = _remaining_days(value, now)
        if key.startswith("certificate:") and value.get("status") == "expired" and days is None:
            known[key] = True
            details.append({"kind": kind, "days": None})
            thresholds[key] = 0
            continue
        if days is None:
            continue
        threshold = next((limit for limit in _THRESHOLDS if days <= limit), None)
        known[key] = threshold is not None
        if threshold is not None:
            details.append({"kind": kind, "days": days})
        thresholds[key] = threshold
    return details


def _target_labels(targets: list[str]) -> dict[str, str]:
    labels = {}
    for target in targets:
        parsed = urlsplit(target)
        labels[target] = (
            parsed.netloc + (parsed.path if parsed.path != "/" else "")
            + ("?" + parsed.query if parsed.query else "")
        ) if parsed.netloc else target
    counts = Counter(labels.values())
    return {target: target if counts[label] > 1 else label for target, label in labels.items()}


def _reason_text(reason: object, http_status: object = None) -> str:
    code = reason.upper() if isinstance(reason, str) else ""
    # Only known codes reach the report; raw exception text may contain private data.
    match = re.fullmatch(r"HTTP_([1-5][0-9]{2})", code)
    if match:
        code, http_status = "HTTP_STATUS", int(match[1])
    if code == "HTTP_STATUS":
        if type(http_status) is int and 100 <= http_status <= 599:
            name = {401: "需要登录", 403: "访问被拒绝", 404: "页面不存在", 429: "请求过多"}.get(http_status)
            name = name or ("网站服务器错误" if http_status >= 500 else "网页返回异常状态")
            return f"{name}（{http_status}）"
        return "网页返回异常状态"
    return {
        "TLS_ERROR": "HTTPS 证书或加密连接异常",
        "DESTINATION_BLOCKED": "目标地址被检测规则拦截",
        "DNS_NOT_FOUND": "找不到域名解析地址",
        "DNS_ERROR": "域名解析异常",
        "CONNECTION_REFUSED": "服务器拒绝连接",
        "CONNECTION_ERROR": "网络连接异常",
        "NAVIGATION_ERROR": "页面打开失败",
        "NAVIGATION_FAILED": "页面打开失败",
        "TIMEOUT": "访问超时",
        "CHECK_ERROR": "检测过程出错",
        "BATCH_DEADLINE": "本批检测时间已用完",
        "ROUND_DEADLINE": "本轮检测时间已用完",
        "BROWSER_UNAVAILABLE": "浏览器检测未能完成",
        "BATCH_INCOMPLETE": "未收到检测结果",
        "RESULT_MISSING": "未收到检测结果",
        "NOT_STARTED": "尚未开始检测",
    }.get(code, "其他原因，待确认")


def _urgency(days: int | float | None) -> tuple:
    return (0, 0) if days is None else (1, days)


def _expiry_line(label: str, days: int | float | None) -> str:
    remaining = "已过期（日期不明）" if days is None else f"已过期{abs(days):g}天" if days < 0 else f"剩{days:g}天"
    return f"{label} {remaining}"


def _section_pages(title: str, lines: list[str]) -> list[str]:
    """Keep rows intact and repeat their category on every Telegram-sized page."""
    pages, rows = [], []
    used = len(title.encode("utf-16-le")) // 2
    capacity = 3500 - used - 1
    for line in lines:
        # A single extremely long entry can exceed a page; split without breaking Unicode.
        parts, part, units = [], [], 0
        for char in line:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > capacity:
                parts.append("".join(part))
                part, units = [], 0
            part.append(char)
            units += size
        parts.append("".join(part))
        for part in parts:
            size = len(part.encode("utf-16-le")) // 2 + 1
            if rows and used + size > 3500:
                pages.append(title + "\n" + "\n".join(rows))
                rows, used = [], len(title.encode("utf-16-le")) // 2
            rows.append(part)
            used += size
    if rows:
        pages.append(title + "\n" + "\n".join(rows))
    return pages


def complete_round(
    state: dict,
    results: list[dict],
    *,
    expected_targets: list[str],
    started: float,
    now: float,
    round_id: str,
) -> list[str]:
    """Count each expected target once; only explicit success recovers failures."""
    targets = list(dict.fromkeys(expected_targets))
    by_target = {
        item["target"]: item
        for item in results
        if isinstance(item, dict) and isinstance(item.get("target"), str)
    }
    site_states = state.setdefault("site_states", {})
    counts = {"ok": 0, "failed": 0, "unconfirmed": 0, "unmeasured": 0}
    pending, timings = [], []
    labels = _target_labels(targets)
    expiry_groups = {"certificate": [], "domain": []}
    problem_groups, recoveries = defaultdict(list), []
    new_failures = recovered = expiry_warnings = 0
    for target in targets:
        result = by_target.get(target, {})
        status = result.get("status", "unmeasured")
        if status not in counts:
            status = "unconfirmed"
        counts[status] += 1
        site = site_states.setdefault(target, {})
        confirmed = site.get("confirmed_status")
        reason = result.get("reason", "result_missing")
        if status == "unmeasured":
            pending.append(target)
        else:
            elapsed = result.get("elapsed_ms")
            if isinstance(elapsed, (int, float)) and math.isfinite(elapsed) and elapsed >= 0:
                timings.append(elapsed)
        if status == "failed":
            changed = confirmed != "failed" or site.get("confirmed_reason") != reason
            problem_groups[("failed", _reason_text(reason, result.get("http_status")))].append(labels[target])
            if changed:
                new_failures += 1
            site["confirmed_status"] = "failed"
            site["confirmed_reason"] = reason
        elif status == "ok":
            if confirmed == "failed":
                recoveries.append(labels[target])
                recovered += 1
            site["confirmed_status"] = "ok"
            site.pop("confirmed_reason", None)
        else:
            label = labels[target]
            if confirmed == "failed":
                label += f"（上次：{_reason_text(site.get('confirmed_reason'))}；尚未确认恢复）"
            problem_groups[(status, _reason_text(reason, result.get("http_status")))].append(label)
        entry_https = result.get("entry_https")
        if entry_https == "failed":
            site["entry_https_warning"] = True
        elif entry_https == "ok":
            site["entry_https_warning"] = False
        expiry = result.get("expiry")
        warnings = _expiry_details(site, expiry if isinstance(expiry, dict) else {}, now)
        for kind in expiry_groups:
            resources = [item for item in warnings if item["kind"] == kind]
            if resources:
                days = min(resources, key=lambda item: _urgency(item["days"]))["days"]
                expiry_groups[kind].append((days, labels[target]))
        if warnings:
            expiry_warnings += 1
        site.update({"last_status": status, "last_reason": reason, "last_checked_at": now})
    # A temporary /checkone round must not discard other queued missing work.
    outside_pending = [item for item in state.get("pending_targets", []) if item not in targets]
    state["pending_targets"] = list(dict.fromkeys([*pending, *outside_pending]))
    summary = {
        "round_id": round_id,
        "completed_at": now,
        "total": len(targets),
        "measured": len(targets) - counts["unmeasured"],
        **counts,
        "average_elapsed_ms": round(sum(timings) / len(timings)) if timings else None,
        "duration_seconds": max(0, now - started),
        "new_failures": new_failures,
        "recovered": recovered,
        "expiry_warnings": expiry_warnings,
    }
    state["last_summary"] = summary
    average = f"{summary['average_elapsed_ms'] / 1000:.2f} 秒" if timings else "暂无"
    text = (
        f"普通检测汇总｜轮次：{round_id}\n"
        f"北京时间：{datetime.fromtimestamp(now, _BEIJING):%Y-%m-%d %H:%M:%S}\n"
        f"总数：{summary['total']}｜已测：{summary['measured']}\n"
        f"正常：{counts['ok']}｜异常：{counts['failed']}｜待确认：{counts['unconfirmed']}｜未测：{counts['unmeasured']}\n"
        f"平均打开耗时：{average}｜整轮耗时：{summary['duration_seconds']:.1f} 秒\n"
        f"新异常：{new_failures}｜恢复：{recovered}｜到期提醒：{expiry_warnings}"
    )
    messages = [text]
    for kind, title in (("certificate", "HTTPS 证书"), ("domain", "域名")):
        entries = sorted(expiry_groups[kind], key=lambda item: (_urgency(item[0]), item[1]))
        if entries:
            messages.extend(_section_pages(f"到期提醒（{title}）：", [_expiry_line(label, days) for days, label in entries]))
    states = {"failed": "异常域名", "unconfirmed": "待确认", "unmeasured": "未完成检测"}
    order = {"failed": 0, "unconfirmed": 1, "unmeasured": 2}
    for (status, explanation), rows in sorted(problem_groups.items(), key=lambda item: (order[item[0][0]], item[0][1])):
        messages.extend(_section_pages(f"{states[status]}（{explanation}）：", sorted(rows)))
    if recoveries:
        messages.extend(_section_pages("恢复正常：", sorted(recoveries)))
    return messages
