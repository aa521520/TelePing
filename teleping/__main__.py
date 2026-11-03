"""Safe public CLI: only aggregate counts and fixed errors may be printed."""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from .bot import COMMANDS
from .crypto import decrypt, encrypt, generate_key
from .jobs import process_batch
from .runtime import control_round, finish_round
from .state import GitHubStateStore
from .telegram import TelegramClient


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError("configuration_missing")
    return value


def _output(name: str, value: str) -> None:
    if filename := os.environ.get("GITHUB_OUTPUT"):
        with open(filename, "a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")


def _services():
    key = _required("STATE_ENCRYPTION_KEY")
    store = GitHubStateStore(_required("GITHUB_REPOSITORY"), _required("GITHUB_TOKEN"), key, default_branch=os.environ.get("DEFAULT_BRANCH", "main"))
    chat_id = int(_required("TELEGRAM_CHAT_ID"))
    if chat_id >= 0:
        raise ValueError("group_configuration_invalid")
    return store, TelegramClient(_required("TELEGRAM_BOT_TOKEN"), chat_id), chat_id


def _setup(directory: Path, key: str) -> None:
    telegram = TelegramClient(_required("TELEGRAM_BOT_TOKEN"), 0)
    if telegram.webhook_url():
        raise RuntimeError("telegram_webhook_conflict")
    telegram.register_menu(COMMANDS)
    groups = {}
    for update in telegram.get_updates(0):
        message = update.get("message", {})
        chat, sender = message.get("chat", {}), message.get("from", {})
        if chat.get("type") not in {"group", "supergroup"} or "sender_chat" in message or sender.get("is_bot"):
            continue
        groups.setdefault(str(chat["id"]), {"chat_id": chat["id"], "title": chat.get("title", "")})
    (directory / "setup.enc").write_bytes(encrypt({"groups": list(groups.values())}, key, purpose="setup"))
    _output("setup_complete", "true")
    print("setup_complete: menu_registered; discovery_encrypted")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TelePing private-target monitor")
    parser.add_argument("mode", choices=["control", "worker", "finish", "setup", "key", "decrypt-setup"])
    parser.add_argument("--directory", default="work")
    parser.add_argument("--run-id", default=os.environ.get("ROUND_ID", "manual"))
    parser.add_argument("--batch", type=int, default=0)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        if args.mode == "key":
            if os.environ.get("GITHUB_ACTIONS"):
                raise ValueError("local_only")
            (directory / "encryption-key.txt").write_text(generate_key(), encoding="utf-8")
            print("key_written_to_private_work_directory")
            return 0
        key = _required("STATE_ENCRYPTION_KEY")
        if args.mode == "decrypt-setup":
            if os.environ.get("GITHUB_ACTIONS"):
                raise ValueError("local_only")
            payload = decrypt((directory / "setup.enc").read_bytes(), key, purpose="setup")
            (directory / "setup-private.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print("setup_details_written_to_private_work_directory")
        elif args.mode == "setup":
            _setup(directory, key)
        elif args.mode == "worker":
            plan = decrypt((directory / "plan.enc").read_bytes(), key, purpose=f"plan:{args.run_id}")
            if plan.get("run_id") != args.run_id or not 0 <= args.batch < len(plan["batches"]):
                raise ValueError("batch_invalid")
            bundle = asyncio.run(process_batch(plan, args.batch))
            (directory / f"worker-{args.batch}.enc").write_bytes(encrypt(bundle, key, purpose=f"batch:{args.run_id}:{args.batch}"))
            print(f"batch_complete: count={len(bundle['results'])}")
        elif args.mode == "control":
            store, telegram, chat_id = _services()
            plan = control_round(store, telegram, seed_text=os.environ.get("BASIC_DOMAINS", ""), chat_id=chat_id, now=time.time(), run_id=args.run_id)
            _output("should_check", "true" if plan else "false")
            _output("matrix", json.dumps({"batch": list(range(len(plan["batches"]))) if plan else [0]}, separators=(",", ":")))
            if plan:
                (directory / "plan.enc").write_bytes(encrypt(plan, key, purpose=f"plan:{args.run_id}"))
                print(f"round_prepared: count={len(plan['expected_targets'])}; batches={len(plan['batches'])}")
            else:
                print("control_complete: no_check_due")
        elif args.mode == "finish":
            store, telegram, _ = _services()
            state = store.load()
            active = (state or {}).get("active_round", {})
            if active.get("run_id") != args.run_id:
                print("finish_skipped: no_active_round")
                return 0
            plan = {**active}
            results, cache = [], {}
            batches = (len(plan["expected_targets"]) + 49) // 50
            for index in range(batches):
                filename = directory / f"worker-{index}.enc"
                if not filename.exists():
                    continue
                try:
                    bundle = decrypt(filename.read_bytes(), key, purpose=f"batch:{args.run_id}:{index}")
                    if bundle.get("run_id") != args.run_id or bundle.get("batch") != index:
                        continue
                    results.extend(bundle["results"])
                    cache.update(bundle.get("expiry_cache_updates", {}))
                except (ValueError, KeyError, TypeError):
                    continue
            delivered = finish_round(store, telegram, plan, results, cache_updates=cache, now=time.time())
            print(f"round_complete: count={len(plan['expected_targets'])}; received={len(results)}; delivered={'yes' if delivered else 'pending'}")
        return 0
    except Exception as error:
        # Never interpolate exception text: HTTP libraries include access-bearing URLs.
        code = str(error) if str(error) in {"telegram_webhook_conflict", "encrypted_data_invalid", "encryption_key_invalid", "github_state_conflict", "configuration_missing", "group_configuration_invalid"} else "operation_failed"
        print(f"teleping_error: {code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
