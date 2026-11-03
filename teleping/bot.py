"""Private Telegram command handling, without network or persistence side effects."""

from __future__ import annotations

import re

from .targets import normalize_target, parse_targets

COMMANDS: list[dict[str, str]] = [
    {"command": "add", "description": "添加一个监控网址，下轮任务处理"},
    {"command": "addmany", "description": "批量添加网址，命令和列表放在同一条消息，下轮任务处理"},
    {"command": "list", "description": "查看监控网址列表，下轮任务回复"},
    {"command": "delete", "description": "删除一个监控网址，下轮任务处理"},
    {"command": "check", "description": "全部网址安排下轮任务检测，并非立即检测"},
    {"command": "checkone", "description": "单个网址安排下轮任务检测，并非立即检测"},
    {"command": "settings", "description": "查看检测设置，下轮任务回复"},
    {"command": "interval", "description": "修改检测间隔，下轮任务处理"},
    {"command": "status", "description": "查看运行状态，下轮任务回复"},
    {"command": "help", "description": "查看中文使用说明，下轮任务回复"},
    {"command": "start", "description": "开始使用并查看帮助，下轮任务回复"},
]

_INTERVALS = (30, 60, 120, 360, 720, 1440)
_USAGE = {
    "add": "用法：/add 网址\n支持裸域名或完整 http/https 网址。",
    "addmany": "用法：在同一条消息中发送 /addmany，换行后每行一个网址。\n/addmany\nexample.test\nhttps://other.test/path",
    "delete": "用法：/delete 网址",
    "checkone": "用法：/checkone 网址\n安排下轮任务临时检测，不自动加入长期监控。",
    "interval": "用法：/interval 分钟数\n可选：30、60、120、360、720、1440 分钟。",
}
_HELP = """监控机器人使用说明
仅指定群当前的群主和管理员可使用命令，普通成员无操作权限。
所有命令都在下轮计划任务运行时处理，并非立即响应。检测请求可跳过正常检测间隔，但不会提前启动计划任务。
/add 网址 — 添加一个监控网址
/addmany — 命令和网址列表放在同一条消息，换行后每行一个网址
/list — 查看全部监控网址
/delete 网址 — 删除监控网址及其排队请求
/check — 全部监控网址安排下轮任务检测，并非立即检测
/checkone 网址 — 单个网址安排下轮任务临时检测，并非立即检测；不会自动添加长期监控
/settings — 查看检测设置
/interval 分钟数 — 设置间隔，可选 30、60、120、360、720、1440
/status — 查看运行状态
/help — 查看本说明
/start — 查看本说明"""
_COMMAND_PATTERN = re.compile(r"/([a-zA-Z0-9_]+)(?:@([a-zA-Z0-9_]+))?(?:\s+([\s\S]*))?\Z")


def handle_update(
    state: dict,
    update: dict,
    *,
    chat_id: int,
    admin_ids: set[int],
    bot_username: str = "",
) -> list[str]:
    """Apply one authorized command and return texts for private delivery."""
    message = update.get("message")
    if not isinstance(message, dict) or "sender_chat" in message:
        return []
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict):
        return []
    if chat.get("id") != chat_id or sender.get("id") not in admin_ids or sender.get("is_bot"):
        return []
    text = message.get("text")
    if not isinstance(text, str):
        return []
    matched = _COMMAND_PATTERN.fullmatch(text.strip())
    if not matched:
        return []
    command, mention, arguments = matched.groups()
    if mention and (not bot_username or mention.casefold() != bot_username.lstrip("@").casefold()):
        return []
    command = command.lower()
    arguments = (arguments or "").strip()
    if command not in {item["command"] for item in COMMANDS}:
        return ["未识别的命令，请使用 /help 查看中文说明。"]
    if command not in _USAGE and arguments:
        return [f"用法：/{command}，无需参数。"]

    if command in ("start", "help"):
        return [_HELP]
    if command == "settings":
        return [
            (
                f"当前检测间隔：{state.get('interval_minutes', 30)} 分钟。\n"
                "可选间隔：30、60、120、360、720、1440 分钟。\n"
                "命令在下轮计划任务处理，并非立即响应。\n"
                "使用 /interval 分钟数 修改间隔；/check 可安排下轮任务检测。"
            )
        ]
    if command == "status":
        last_check = "已有检测记录" if state.get("last_check_started") else "尚未开始首轮检测"
        return [
            (
                f"监控网址：{len(state.get('targets', []))} 个\n"
                f"检测间隔：{state.get('interval_minutes', 30)} 分钟\n"
                f"待处理网址：{len(state.get('pending_targets', []))} 个\n"
                f"单独检测排队：{len(state.get('forced_targets', []))} 个\n"
                f"全部检测排队：{'是' if state.get('force_all') else '否'}\n"
                f"检测记录：{last_check}\n命令和检测请求由下轮计划任务处理。"
            )
        ]
    if command == "list":
        targets = state.get("targets", [])
        if not targets:
            return ["监控列表为空。使用 /add 网址 或 /addmany 添加。"]
        return [
            f"监控网址共 {len(targets)} 个：",
            *[f"{index}. {target}" for index, target in enumerate(targets, 1)],
        ]
    if command == "check":
        state["force_all"] = True
        return ["已安排全部监控网址在下轮任务检测，并非立即检测。"]

    if not arguments:
        return [_USAGE[command]]
    if command == "interval":
        if not arguments.isascii() or not arguments.isdecimal() or int(arguments) not in _INTERVALS:
            return [_USAGE["interval"]]
        state["interval_minutes"] = int(arguments)
        return [f"检测间隔已设置为 {state['interval_minutes']} 分钟；由下轮任务处理，仍需等待计划任务运行。"]

    if command == "addmany":
        try:
            targets = parse_targets(arguments)
        except ValueError:
            return ["网址格式无效，本批次未添加任何网址。请检查每行只放一个合法的 http/https 网址。"]
        if not targets:
            return [_USAGE["addmany"]]
        existing = state.get("targets", [])
        additions = [target for target in targets if target not in existing]
        state["targets"] = [*existing, *additions]
        return [
            f"已添加 {len(additions)} 个网址，重复 {len(targets) - len(additions)} 个；当前共 {len(state['targets'])} 个。"
        ]

    try:
        target = normalize_target(arguments)
    except ValueError:
        return ["网址格式无效，未执行操作。\n" + _USAGE[command]]
    if command == "add":
        if target in state.get("targets", []):
            return ["该网址已在监控列表中，无需重复添加。"]
        state.setdefault("targets", []).append(target)
        return [f"已添加 1 个监控网址；当前共 {len(state['targets'])} 个。"]
    if command == "delete":
        keys = ("targets", "pending_targets", "forced_targets")
        if not any(target in state.get(key, []) for key in keys):
            return ["该网址未在监控列表中。"]
        for key in keys:
            state[key] = [item for item in state.get(key, []) if item != target]
        return ["已删除监控网址及其排队请求。"]
    if command == "checkone":
        forced = state.setdefault("forced_targets", [])
        if target not in forced:
            forced.append(target)
        return ["已安排该网址在下轮任务临时检测，并非立即检测；不会自动加入长期监控。"]
    return []
