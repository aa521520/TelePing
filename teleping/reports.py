"""List current private warnings every round and retain transition history without I/O."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

_THRESHOLDS = (0, 1, 3, 7, 14, 30)
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


def _expiry_details(site: dict, expiry: dict, target: str, now: float) -> list[str]:
    thresholds = site.setdefault("expiry_thresholds", {})
    known = site.setdefault("expiry_known", {})
    # An updated redirect route may no longer use a previously seen certificate.
    for key in known:
        known[key] = False
    details = []
    for key, label, value in _expiry_resources(expiry):
        if value.get("status") not in {"ok", "expired"}:
            continue
        days = _remaining_days(value, now)
        if key.startswith("certificate:") and value.get("status") == "expired" and days is None:
            known[key] = True
            details.append(f"到期提醒：{target}\n{label}：证书已过期，具体日期无法确认")
            thresholds[key] = 0
            continue
        if days is None:
            continue
        threshold = next((limit for limit in _THRESHOLDS if days <= limit), None)
        known[key] = threshold is not None
        if threshold is not None:
            remaining = f"已到期 {abs(days):g} 天" if days < 0 else f"剩余 {days:g} 天"
            details.append(f"到期提醒：{target}\n{label}：{remaining}（{threshold} 天提醒阈值）")
        thresholds[key] = threshold
    return details


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
    pending, details, timings = [], [], []
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
            details.append(f"{'新增异常' if changed else '持续异常'}：{target}\n原因：{reason}")
            if changed:
                new_failures += 1
            site["confirmed_status"] = "failed"
            site["confirmed_reason"] = reason
        elif status == "ok":
            if confirmed == "failed":
                details.append(f"恢复正常：{target}")
                recovered += 1
            site["confirmed_status"] = "ok"
            site.pop("confirmed_reason", None)
        elif confirmed == "failed":
            current = "未检测" if status == "unmeasured" else "待确认"
            details.append(
                f"异常待确认：{target}\n上次确认异常：{site.get('confirmed_reason', '未知')}；"
                f"本轮{current}，尚未确认恢复。"
            )
        entry_https = result.get("entry_https")
        if entry_https == "failed":
            site["entry_https_warning"] = True
        elif entry_https == "ok":
            site["entry_https_warning"] = False
        expiry = result.get("expiry")
        warnings = _expiry_details(site, expiry if isinstance(expiry, dict) else {}, target, now)
        details.extend(warnings)
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
    return [text, "\n\n".join(details)] if details else [text]
