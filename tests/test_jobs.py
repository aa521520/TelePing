import asyncio
import time

from teleping.jobs import process_batch


def test_300_targets_are_all_accounted_for_across_six_batches():
    targets = [f"http://item-{number}.test/" for number in range(300)]
    plan = {"run_id": "r1", "deadline": time.time() + 60, "expiry_cache": {}, "batches": [targets[index:index + 50] for index in range(0, 300, 50)]}
    visited = []

    async def checker(items, **kwargs):
        visited.extend(items)
        return [{"target": target, "status": "ok", "reason": "OK", "final_url": target, "elapsed_ms": 50, "https_hosts": []} for target in items]

    async def expiry(target, hosts, **kwargs):
        return {"domain": {"status": "unknown", "days": None, "expires_at": None}, "certificates": []}

    async def run():
        return await asyncio.gather(*(process_batch(plan, index, check_fn=checker, expiry_fn=expiry, retry_delay=0) for index in range(6)))

    bundles = asyncio.run(run())
    assert len(visited) == 300
    assert len(set(visited)) == 300
    assert sum(len(bundle["results"]) for bundle in bundles) == 300
    assert len({result["target"] for bundle in bundles for result in bundle["results"]}) == 300


def test_transient_failure_retried_once_without_duplicating_results():
    plan = {"run_id": "r1", "deadline": time.time() + 60, "expiry_cache": {}, "batches": [["http://one.test/", "http://two.test/"]]}
    calls = []

    async def checker(items, **kwargs):
        calls.append(list(items))
        return [{"target": target, "status": "unconfirmed" if len(calls) == 1 and "one" in target else "ok", "reason": "TIMEOUT", "https_hosts": []} for target in items]

    async def expiry(*args, **kwargs):
        return {"domain": {"status": "unknown", "days": None}, "certificates": []}

    bundle = asyncio.run(process_batch(plan, 0, check_fn=checker, expiry_fn=expiry, retry_delay=0))
    assert calls == [["http://one.test/", "http://two.test/"], ["http://one.test/"]]
    assert len(bundle["results"]) == 2
    assert all(result["status"] == "ok" for result in bundle["results"])


def test_daily_cache_used_only_for_same_https_chain():
    cached = {"domain": {"status": "ok", "days": 12}, "certificates": []}
    plan = {"run_id": "r1", "deadline": time.time() + 60, "expiry_cache": {"http://one.test/": {"checked_at": time.time(), "hosts": ["one.test"], "value": cached}}, "batches": [["http://one.test/"]]}

    async def checker(items, **kwargs):
        return [{"target": items[0], "status": "ok", "reason": "OK", "https_hosts": ["one.test"]}]

    async def expiry(*args, **kwargs):
        raise AssertionError("fresh cache must be reused")

    bundle = asyncio.run(process_batch(plan, 0, check_fn=checker, expiry_fn=expiry, retry_delay=0))
    assert bundle["results"][0]["expiry"] == cached


def test_expired_round_does_not_launch_browser_or_claim_success():
    plan = {"run_id": "r1", "deadline": time.time() - 1, "expiry_cache": {}, "batches": [["http://one.test/"]]}

    async def checker(*args, **kwargs):
        raise AssertionError("must not start expired work")

    bundle = asyncio.run(process_batch(plan, 0, check_fn=checker, retry_delay=0))
    assert bundle["results"][0]["status"] == "unmeasured"
