"""Control/check/finalize protocol; private values never reach stdout."""

import hashlib
import json

from .bot import COMMANDS, handle_update
from .reports import complete_round
from .state import new_state


def queue_texts(state: dict, texts: list[str], *, prefix: str) -> None:
    for text in texts:
        state["outbox_serial"] = state.get("outbox_serial", 0) + 1
        state.setdefault("outbox", []).append({"id": f"{prefix}:{state['outbox_serial']}", "text": text})


def flush_outbox(store, telegram, state: dict, *, limit: int = 20) -> bool:
    for _ in range(limit):
        if not state.get("outbox"):
            return True
        try:
            telegram.send(state["outbox"][0]["text"])
        except RuntimeError:
            return False
        state["outbox"].pop(0)
        store.save(state)
    return not state.get("outbox")


def control_round(store, telegram, *, seed_text: str, chat_id: int, now: float, run_id: str) -> dict | None:
    state = store.load()
    if state is None:
        state = new_state(seed_text)
        store.save(state)
    active = state.pop("active_round", None)
    if active:
        interrupted = active.get("expected_targets", [])
        state["pending_targets"] = list(dict.fromkeys(interrupted + state.get("pending_targets", [])))
        queue_texts(state, [f"⚠️ 上轮任务未完成，未确认的目标已安排优先补测。轮次：{active.get('run_id', 'unknown')}"], prefix=f"interrupted:{run_id}")
    flush_outbox(store, telegram, state)
    if telegram.webhook_url():
        raise RuntimeError("telegram_webhook_conflict")
    menu_fingerprint = hashlib.sha256((json.dumps(COMMANDS, ensure_ascii=False) + str(chat_id)).encode()).hexdigest()
    if state.get("menu_fingerprint") != menu_fingerprint:
        telegram.register_menu(COMMANDS)
        state["menu_fingerprint"] = menu_fingerprint
        state["menu_registered"] = True
        store.save(state)
    username = telegram.bot_username()
    for _ in range(10):
        updates = telegram.get_updates(state.get("last_update_id", -1) + 1)
        replies = []
        for update in sorted(updates, key=lambda item: item.get("update_id", -1)):
            update_id = update.get("update_id")
            if not isinstance(update_id, int) or update_id <= state.get("last_update_id", -1):
                continue
            message = update.get("message", {})
            current_admins = set()
            if isinstance(message, dict) and isinstance(message.get("chat"), dict) and message["chat"].get("id") == chat_id and isinstance(message.get("text"), str) and message["text"].lstrip().startswith("/"):
                # Resolve live roles at processing time; a demoted administrator loses access immediately.
                # On API failure do not persist/ack this page, so an unverified command can retry safely.
                current_admins = telegram.administrator_ids()
            replies.extend(handle_update(state, update, chat_id=chat_id, admin_ids=current_admins, bot_username=username))
            state["last_update_id"] = update_id
        if replies:
            queue_texts(state, ["\n\n".join(replies)], prefix=f"command:{run_id}")
        if updates:
            # Persist settings and offset BEFORE acknowledging this page with a higher offset.
            store.save(state)
            flush_outbox(store, telegram, state)
        if len(updates) < 100:
            break
    monitored = state.get("targets", [])
    # Temporary /checkone requests also retain their right to retry until /delete cancels them.
    pending = state.get("pending_targets", [])
    forced = state.get("forced_targets", [])
    # Cron's :07/:37 slots are stable even when runner startup jitters by seconds.
    slot = (int(now) - 7 * 60) // (30 * 60)
    last_slot = state.get("last_check_slot", (int(state.get("last_check_started", 0)) - 7 * 60) // (30 * 60))
    full_due = bool(state.get("force_all")) or not state.get("last_check_started") or slot - last_slot >= state["interval_minutes"] // 30
    if full_due:
        expected = list(dict.fromkeys(pending + forced + monitored))
    else:
        expected = list(dict.fromkeys(pending + forced))
    if not expected:
        if state.get("force_all"):
            queue_texts(state, ["监控清单为空，未启动检测。请用 /add 或 /addmany 添加。"], prefix=f"empty:{run_id}")
        state["force_all"] = False
        store.save(state)
        flush_outbox(store, telegram, state)
        return None
    if full_due:
        state["last_check_started"] = now
        state["last_check_slot"] = slot
    state["force_all"] = False
    state["forced_targets"] = []
    state["active_round"] = {"run_id": run_id, "started": now, "expected_targets": expected}
    store.save(state)
    flush_outbox(store, telegram, state)
    return {"run_id": run_id, "started": now, "deadline": now + 25 * 60, "expected_targets": expected, "batches": [expected[index:index + 50] for index in range(0, len(expected), 50)], "expiry_cache": state.get("expiry_cache", {})}


def finish_round(store, telegram, plan: dict, results: list[dict], *, cache_updates: dict, now: float) -> bool:
    state = store.load()
    if state is None:
        raise RuntimeError("state_missing")
    if state.get("active_round", {}).get("run_id") != plan["run_id"]:
        return False
    allowed = set(plan["expected_targets"])
    results = [result for result in results if result.get("target") in allowed]
    messages = complete_round(state, results, expected_targets=plan["expected_targets"], started=plan["started"], now=now, round_id=plan["run_id"])
    state["expiry_cache"].update({target: cached for target, cached in cache_updates.items() if target in state["targets"]})
    state["expiry_cache"] = {target: cached for target, cached in state["expiry_cache"].items() if target in state["targets"]}
    state.pop("active_round", None)
    queue_texts(state, messages, prefix=f"report:{plan['run_id']}")
    store.save(state)
    return flush_outbox(store, telegram, state)
