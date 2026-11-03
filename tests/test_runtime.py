import copy

import pytest

from teleping.runtime import control_round, finish_round, queue_texts
from teleping.state import new_state


class MemoryStore:
    def __init__(self, state=None):
        self.state = copy.deepcopy(state)
        self.saved = []

    def load(self):
        return copy.deepcopy(self.state)

    def save(self, state):
        self.state = copy.deepcopy(state)
        self.saved.append(copy.deepcopy(state))


class Telegram:
    def __init__(self, pages=None, store=None):
        self.pages = list(pages or [[]])
        self.sent = []
        self.store = store
        self.menu = []
        self.offsets = []
        self.roles = []

    def administrator_ids(self):
        return self.roles.pop(0) if self.roles else {7}

    def webhook_url(self):
        return ""

    def register_menu(self, commands):
        self.menu = commands

    def bot_username(self):
        return "ExampleMonitorBot"

    def get_updates(self, offset):
        if self.offsets and self.store:
            assert self.store.state["last_update_id"] >= offset - 1
        self.offsets.append(offset)
        return self.pages.pop(0) if self.pages else []

    def send(self, text):
        self.sent.append(text)


def update(number, text, user=7):
    return {"update_id": number, "message": {"chat": {"id": -123}, "from": {"id": user}, "text": text}}


def test_control_seeds_once_and_never_resurrects_bot_deleted_targets():
    store = MemoryStore()
    tg = Telegram([ [update(1, "/delete example.test")]], store)
    plan = control_round(store, tg, seed_text="example.test", chat_id=-123, now=10000, run_id="r1")
    assert plan is None
    assert store.state["targets"] == []
    control_round(store, Telegram(), seed_text="example.test", chat_id=-123, now=12000, run_id="r2")
    assert store.state["targets"] == []


def test_control_batches_300_private_targets_without_public_matrix_leak():
    targets = "\n".join(f"private-{i}.test" for i in range(300))
    store = MemoryStore()
    plan = control_round(store, Telegram(), seed_text=targets, chat_id=-123, now=10000, run_id="r1")
    assert len(plan["batches"]) == 6
    assert [len(batch) for batch in plan["batches"]] == [50] * 6
    assert len(plan["expected_targets"]) == 300
    assert store.state["active_round"]["run_id"] == "r1"


def test_force_one_bypasses_long_interval_but_does_not_add_monitor():
    state = new_state("example.test")
    state.update(interval_minutes=1440, last_check_started=10000)
    store = MemoryStore(state)
    tg = Telegram([[update(2, "/checkone other.test")]], store)
    plan = control_round(store, tg, seed_text="", chat_id=-123, now=11000, run_id="r1")
    assert plan["expected_targets"] == ["http://other.test/"]
    assert store.state["targets"] == ["http://example.test/"]
    assert store.state["forced_targets"] == []
    assert store.state["last_check_started"] == 10000


def test_offset_saved_before_next_poll_and_unauthorized_mutations_ignored():
    store = MemoryStore(new_state())
    pages = [[update(i, "/add ignored.test", user=999) for i in range(100)], [update(100, "/add allowed.test")]]
    tg = Telegram(pages, store)
    control_round(store, tg, seed_text="", chat_id=-123, now=10000, run_id="r1")
    assert tg.offsets == [0, 100]
    assert store.state["last_update_id"] == 100
    assert store.state["targets"] == ["http://allowed.test/"]


def test_interrupted_round_goes_to_front_of_next_work_queue():
    state = new_state("a.test\nb.test\nc.test")
    state["active_round"] = {"run_id": "old", "expected_targets": ["http://c.test/"], "started": 100}
    state["last_check_started"] = 10000
    state["interval_minutes"] = 1440
    store = MemoryStore(state)
    plan = control_round(store, Telegram(), seed_text="", chat_id=-123, now=11000, run_id="r1")
    assert plan["expected_targets"][0] == "http://c.test/"


def test_finish_missing_batch_marks_unmeasured_and_preserves_prior_failure():
    state = new_state("a.test\nb.test")
    state["site_states"]["http://b.test/"] = {"confirmed_status": "failed"}
    store = MemoryStore(state)
    plan = control_round(store, Telegram(), seed_text="", chat_id=-123, now=10000, run_id="r1")
    result = {"target": "http://a.test/", "status": "ok", "reason": "ok", "final_url": "http://a.test/", "elapsed_ms": 100, "entry_https": "unknown"}
    tg = Telegram()
    finish_round(store, tg, plan, [result], cache_updates={}, now=10010)
    assert store.state["last_summary"]["unmeasured"] == 1
    assert store.state["pending_targets"] == ["http://b.test/"]
    assert store.state["site_states"]["http://b.test/"]["confirmed_status"] == "failed"
    assert len(tg.sent) >= 1
    assert store.state["outbox"] == []
    assert "active_round" not in store.state


def test_failed_delivery_leaves_durable_outbox():
    store = MemoryStore(new_state())
    state = store.load()
    queue_texts(state, ["private report"], prefix="r1")
    store.save(state)

    class Offline(Telegram):
        def send(self, text):
            raise RuntimeError("telegram_unavailable")

    control_round(store, Offline(), seed_text="", chat_id=-123, now=10000, run_id="r1")
    assert any(item["text"] == "private report" for item in store.state["outbox"])


def test_adjacent_cron_slot_not_skipped_when_start_is_one_second_early():
    state = new_state("example.test")
    state["last_check_started"] = 10000
    store = MemoryStore(state)
    plan = control_round(store, Telegram(), seed_text="", chat_id=-123, now=11799, run_id="r2")
    assert plan is not None
    assert plan["expected_targets"] == ["http://example.test/"]


def test_longer_interval_obeys_cron_slots_despite_jitter():
    state = new_state("example.test")
    state.update(interval_minutes=60, last_check_started=10000)
    store = MemoryStore(state)
    assert control_round(store, Telegram(), seed_text="", chat_id=-123, now=11799, run_id="r2") is None
    plan = control_round(store, Telegram(), seed_text="", chat_id=-123, now=13599, run_id="r3")
    assert plan is not None


def test_unmeasured_temporary_check_is_retried_without_becoming_monitored():
    state = new_state()
    state.update(last_check_started=10000, interval_minutes=1440, forced_targets=["http://temporary.test/"])
    store = MemoryStore(state)
    plan = control_round(store, Telegram(), seed_text="", chat_id=-123, now=11000, run_id="r1")
    finish_round(store, Telegram(), plan, [], cache_updates={}, now=11010)
    next_plan = control_round(store, Telegram(), seed_text="", chat_id=-123, now=12800, run_id="r2")
    assert next_plan is not None
    assert next_plan["expected_targets"] == ["http://temporary.test/"]
    assert store.state["targets"] == []


def test_delete_cancels_target_from_interrupted_round():
    state = new_state()
    state.update(last_check_started=10000, interval_minutes=1440)
    state["active_round"] = {"run_id": "old", "started": 10000, "expected_targets": ["http://temporary.test/"]}
    store = MemoryStore(state)
    tg = Telegram([[update(1, "/delete temporary.test")]], store)
    assert control_round(store, tg, seed_text="", chat_id=-123, now=11000, run_id="r2") is None
    assert store.state["pending_targets"] == []


def test_current_group_roles_are_checked_for_each_command():
    store = MemoryStore(new_state())
    tg = Telegram([[update(1, "/add first.test", user=7), update(2, "/add denied.test", user=7), update(3, "/add new-admin.test", user=8)]], store)
    tg.roles = [{7}, {8}, {8}]
    control_round(store, tg, seed_text="", chat_id=-123, now=10000, run_id="roles")
    assert store.state["targets"] == ["http://first.test/", "http://new-admin.test/"]
    assert tg.roles == []


def test_role_lookup_failure_does_not_apply_or_ack_command():
    store = MemoryStore(new_state())

    class Unverified(Telegram):
        def administrator_ids(self):
            raise RuntimeError("telegram_api_failed")

    tg = Unverified([[update(1, "/add unverified.test")]], store)
    with pytest.raises(RuntimeError, match="telegram_api_failed"):
        control_round(store, tg, seed_text="", chat_id=-123, now=10000, run_id="roles")
    assert store.state["targets"] == []
    assert store.state["last_update_id"] == -1
