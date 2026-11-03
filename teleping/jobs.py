"""Isolated batch work and encrypted worker payloads."""

import asyncio
import time

from .checks import check_batch
from .expiry import check_expiry


async def process_batch(plan: dict, batch_index: int, *, check_fn=None, expiry_fn=None, retry_delay: float = 5) -> dict:
    check_fn = check_fn or check_batch
    expiry_fn = expiry_fn or check_expiry
    targets = plan["batches"][batch_index]
    deadline = min(plan["deadline"], time.time() + 18 * 60)
    output = {"run_id": plan["run_id"], "batch": batch_index, "results": [], "expiry_cache_updates": {}}
    if time.time() >= deadline:
        output["results"] = [{"target": target, "status": "unmeasured", "reason": "ROUND_DEADLINE"} for target in targets]
        return output
    results = await check_fn(targets, concurrency=5, timeout=45, settle=15, deadline=deadline)
    by_target = {result["target"]: result for result in results if result.get("target") in targets}
    retry = [target for target in targets if by_target.get(target, {}).get("status") in {"failed", "unconfirmed"}]
    if retry and time.time() + retry_delay + 5 < deadline:
        await asyncio.sleep(retry_delay)
        for result in await check_fn(retry, concurrency=5, timeout=45, settle=15, deadline=deadline):
            if result.get("target") in retry:
                by_target[result["target"]] = result
    semaphore = asyncio.Semaphore(5)

    async def enrich(target):
        result = by_target.get(target, {"target": target, "status": "unmeasured", "reason": "BATCH_INCOMPLETE"})
        if result["status"] == "unmeasured":
            return result
        hosts = sorted(set(result.get("https_hosts", [])))
        cached = plan.get("expiry_cache", {}).get(target)
        if cached and 0 <= time.time() - cached["checked_at"] < 86400 and cached.get("hosts") == hosts:
            result["expiry"] = cached["value"]
            return result
        async with semaphore:
            remaining = deadline - time.time()
            if remaining < 2:
                return result
            try:
                value = await asyncio.wait_for(expiry_fn(target, hosts), timeout=min(25, remaining))
            except Exception:
                value = {"domain": {"status": "unknown", "days": None, "expires_at": None}, "certificates": []}
            result["expiry"] = value
            output["expiry_cache_updates"][target] = {"checked_at": time.time(), "hosts": hosts, "value": value}
        return result

    output["results"] = await asyncio.gather(*(enrich(target) for target in targets))
    return output
