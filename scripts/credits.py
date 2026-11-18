#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小鲸鱼积分挂件 —— 配置读写 + 基于本地日志的自动记账。

数据源：~/.workbuddy/logs/<日期>/<会话>.log 里的两条流水
  [AcpUsagePublisher] [CreditUsageSource] ... credit=<积分>
  [GenerationToolCreditUsageHook] publish credit start ... credit=<积分>
按 requestId/messageId/callId/stageId 去重；只统计「基准校准时间之后」的新增消耗。

用法：
    python credits.py show
    python credits.py set --balance 2273 [--today 0] [--note "..."]   # 官方数字校准
    python credits.py sync [--dry-run]                                  # 从日志同步消耗
    python credits.py consume --amount 12.5 [--note "..."]              # 手动补记一笔
    python credits.py ledger [--limit 10]                               # 查看流水
    python credits.py grant [--on|--off] [--amount 100] [--now]          # 每日赠送积分设置
    python credits.py reset-today
"""
import argparse
import copy
import glob
import json
import os
import re
import shutil
from datetime import date, datetime, timedelta

# 官方每日赠送积分（用户 2026-11-18 确认为每天 100）。
# 注意：本地日志里没有赠送流水（全是消耗），所以这笔是「按用户已知政策」本地补记，
# 不是从日志读出来的真值。官方余额变化请以客户端为准，必要时用 set --balance 校准。
DAILY_GRANT_AMOUNT = 100
DAILY_GRANT_MAX_DAYS = 370   # 补给记录保留天数上限（略大于一年）

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
WORKBUDDY_HOME = os.environ.get("WORKBUDDY_HOME") or os.path.join(HOME, ".workbuddy")
CONFIG = os.environ.get("WB_WHALE_CONFIG") or os.path.join(BASE, "config", "credits.json")
LOGS = os.environ.get("WB_WHALE_LOGS") or os.path.join(WORKBUDDY_HOME, "logs")

DEFAULT = {
    "balance": None,
    "todayUsage": 0,
    "date": None,
    "unit": "积分",
    "updatedAt": "",
    "note": "在 WorkBuddy 左下角头像 → 套餐与积分 查看余额，然后点挂件「设置余额」",
    "window": {"x": None, "y": None, "alpha": 0.95, "onlyOnWorkBuddy": False, "globalTopmost": False, "titleBarDock": True, "dockPosition": "bottom", "twoPages": False, "cssSlogan": True},
    "ledger": {
        "autoSync": True,
        "baselineAt": None,
        "scan": {},
        "seen": {},
        "events": [],
        "history": {},
        "lastCost": 0,
        "lastEventAt": "",
        "lastKind": "",
        # 每日赠送积分：按用户确认的「官方每天送 100」在跨天时补记一笔正向流水。
        # grantedDays 用日期去重，同一天只补一次；校准余额时把当天标记为已补，
        # 避免用户手输的官方余额（已含当天赠送）再被叠加。
        "dailyGrant": {
            "enabled": True,
            "amount": DAILY_GRANT_AMOUNT,
            "grantedDays": [],
        },
    },
}

RE_TS = re.compile(r"^\[(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})")

# 账本保留策略：防止 credits.json 随使用时间无限膨胀（守护每 2 秒读一次整份配置）。
EVENTS_MAX = 2000        # 流水最多保留笔数
EVENTS_KEEP_DAYS = 90    # 流水保留时间窗（两者同时生效，取交集）
SEEN_MAX = 4000          # 去重键上限（留 2 倍余量，降低日志轮转时重算的可能）
HISTORY_KEEP_DAYS = 90   # 按日汇总保留天数
BY_SESSION_MAX = 120     # 按会话累计保留个数

RE_MODEL = re.compile(
    r"\[AcpUsagePublisher\].*?\[CreditUsageSource\]\s+source=(\S+)\s+"
    r"sessionId=(\S+)\s+requestId=(\S+)\s+messageId=(\S+)\s+credit=([\d.]+)")
RE_TOOL = re.compile(
    r"\[GenerationToolCreditUsageHook\] publish credit start tool=(\S+)\s+"
    r"sessionId=(\S+)\s+callId=(\S+)\s+stageId=(\S+)\s+credit=([\d.]+)")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load():
    data = _read_json(CONFIG) if os.path.exists(CONFIG) else None
    if not isinstance(data, dict):
        # 账本缺失或损坏：先救上一份可用副本，别把余额静默清成 null
        data = _read_json(CONFIG + ".bak")
        if isinstance(data, dict):
            try:
                shutil.copyfile(CONFIG + ".bak", CONFIG)
            except OSError:
                pass
        else:
            return copy.deepcopy(DEFAULT)
    merged = copy.deepcopy(DEFAULT)
    merged.update(data)
    merged["window"] = {**DEFAULT["window"], **(data.get("window") or {})}
    merged["ledger"] = {**DEFAULT["ledger"], **(data.get("ledger") or {})}
    return merged


def save(data):
    """原子写：唯一临时文件名 + fsync + 保留上一份可用账本。

    守护进程和 CLI 会同时写同一份账本；此前用固定的 .tmp 文件名，
    两个进程撞在一起会写出半截 JSON，load() 读坏就回退成 DEFAULT，
    余额被静默清空。这里改成按 pid 区分临时文件，并在覆盖前留 .bak。
    """
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = "%s.tmp.%d" % (CONFIG, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        if os.path.exists(CONFIG):
            shutil.copyfile(CONFIG, CONFIG + ".bak")
    except OSError:
        pass
    os.replace(tmp, CONFIG)


def roll_day(data):
    """跨天自动把「今日已用」清零。"""
    today = date.today().isoformat()
    if data.get("date") != today:
        data["date"] = today
        data["todayUsage"] = 0
    return data


def grant_daily(data, dry_run=False):
    """按官方每日赠送政策给本地账本补记正向积分（默认每天 100）。

    规则：
      - 只有余额已校准（balance 非 None）才补；未校准时补了也没有基准，没意义。
      - 按自然日去重，同一天最多补一次，记在 ledger.dailyGrant.grantedDays。
      - 校准（set --balance）会把当天标记为已补：官方数字本身已含当天赠送，
        不能重复叠加。
      - 补给作为一条 kind="每日补给" 的 event 写入流水，便于 ledger 里审计。
      - 跨天首次 sync 时由守护进程自动触发；也可以 `grant --force` 手动补。
    返回 (本次补给金额, 说明)；不补时金额为 0。
    """
    ledger = data.setdefault("ledger", {})
    cfg = ledger.setdefault("dailyGrant", {})
    if not cfg.get("enabled", True):
        return 0.0, "每日补给已关闭"
    if data.get("balance") is None:
        return 0.0, "余额未校准，跳过补给（先 set --balance）"

    today = date.today().isoformat()
    granted = cfg.setdefault("grantedDays", [])
    if today in granted:
        return 0.0, "今天已补过"
    amount = round(float(cfg.get("amount", DAILY_GRANT_AMOUNT)), 2)
    if amount <= 0:
        return 0.0, "补给金额非正，跳过"

    if not dry_run:
        data["balance"] = round(float(data["balance"]) + amount, 2)
        granted.append(today)
        # 保留上限，防止 grantedDays 无限增长
        if len(granted) > DAILY_GRANT_MAX_DAYS:
            cfg["grantedDays"] = granted[-DAILY_GRANT_MAX_DAYS:]
        records = ledger.setdefault("events", [])
        records.append({
            "ts": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
            "cost": -amount,          # 负数流水 = 增加积分
            "kind": "每日补给",
            "key": "grant|" + today,
            "session": "",
        })
        ledger["events"] = records[-EVENTS_MAX:]
        data["updatedAt"] = stamp()
    return amount, "已补 %+.2f" % amount


def mark_grant_today(data):
    """校准余额时调用：把今天标记为已补，避免官方数字（含当天赠送）被重复叠加。"""
    cfg = data.setdefault("ledger", {}).setdefault("dailyGrant", {})
    granted = cfg.setdefault("grantedDays", [])
    today = date.today().isoformat()
    if today not in granted:
        granted.append(today)
        if len(granted) > DAILY_GRANT_MAX_DAYS:
            cfg["grantedDays"] = granted[-DAILY_GRANT_MAX_DAYS:]


def fmt(value):
    if value is None:
        return "尚未设置"
    try:
        number = float(value)
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"
    except (TypeError, ValueError):
        return str(value)


def stamp():
    return datetime.now().isoformat(timespec="seconds")


def parse_ts(text):
    try:
        return datetime.strptime(text, "%Y/%m/%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def iter_log_files(days=2):
    """只扫今天和昨天的会话日志目录。"""
    for i in range(days):
        day = (date.today() - timedelta(days=i)).isoformat()
        yield from sorted(glob.glob(os.path.join(LOGS, day, "*.log")))


def parse_credit_line(line):
    match = RE_MODEL.search(line) or RE_TOOL.search(line)
    if not match:
        return None
    # 必须有行首时间戳：真实流水全部是 "[2026/11/16 08:11:43.134] [Info] ..." 这种形态。
    # 少了这条校验，对话里被记录下来的日志片段（例如脚本源码或贴出的日志文本）
    # 会被误当成真实消耗扣钱 —— 曾因此把 8.73 记成了「s1」会话的消耗。
    ts_match = RE_TS.match(line)
    if not ts_match:
        return None
    ts = ts_match.group(1)
    if match.re is RE_MODEL:
        _source, session, request_id, message_id, value = match.groups()
        key = "u|" + request_id + "|" + message_id + "|" + value
        kind = "模型调用"
    else:
        tool, session, call_id, stage_id, value = match.groups()
        key = "t|" + call_id + "|" + stage_id + "|" + value
        kind = "工具:" + tool
    try:
        cost = round(float(value), 2)
    except ValueError:
        return None
    return {"key": key, "ts": ts, "kind": kind, "cost": cost, "session": session}


def scan_events(ledger):
    """按字节偏移增量扫描日志，返回 (事件列表, 新的扫描状态)。"""
    state = ledger.get("scan") or {}
    new_state = {}
    events = []
    for path in iter_log_files():
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        prev = state.get(path) or {}
        offset = prev.get("size", 0)
        tail = prev.get("tail", "")
        if size < offset:  # 日志被轮转 / 重写
            offset, tail = 0, ""
        chunk = ""
        if size > offset:
            try:
                with open(path, "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read().decode("utf-8", "ignore")
            except OSError:
                chunk = ""
        lines = (tail + chunk).split("\n")
        new_tail = lines.pop() if lines else ""
        for line in lines:
            event = parse_credit_line(line)
            if event:
                events.append(event)
        new_state[path] = {"size": size, "tail": new_tail[-4096:]}
    return events, new_state


def _day_key(text):
    """把 '2026/11/16 08:30:33' 归一成 '2026-11-16'。"""
    return (text or "")[:10].replace("/", "-")


def _cutoff(days):
    return (date.today() - timedelta(days=days)).isoformat()


def prune_ledger(ledger):
    """收敛账本体积：流水按「最近 N 笔 + 最近 M 天」双条件保留，去重键与汇总同步裁剪。

    返回被裁剪掉的条数统计，便于排查。只在 apply_events 之后调用，
    因此它不会影响余额（余额是累计值，与流水明细无关）。
    """
    stats = {}

    events = ledger.get("events") or []
    if events:
        cutoff = _cutoff(EVENTS_KEEP_DAYS)
        kept = [e for e in events if _day_key(e.get("ts")) >= cutoff]
        if len(kept) > EVENTS_MAX:
            kept = kept[-EVENTS_MAX:]
        if len(kept) != len(events):
            stats["events"] = len(events) - len(kept)
        ledger["events"] = kept

    seen = ledger.get("seen") or {}
    if len(seen) > SEEN_MAX:
        # dict 保持插入顺序（Python 3.7+），裁掉最早的一批
        ledger["seen"] = {k: seen[k] for k in list(seen)[-SEEN_MAX:]}
        stats["seen"] = len(seen) - SEEN_MAX

    history = ledger.get("history") or {}
    if history:
        cutoff = _cutoff(HISTORY_KEEP_DAYS)
        trimmed = {d: v for d, v in history.items() if d >= cutoff}
        if len(trimmed) != len(history):
            stats["history"] = len(history) - len(trimmed)
        ledger["history"] = trimmed

    by_session = ledger.get("bySession") or {}
    if len(by_session) > BY_SESSION_MAX:
        ledger["bySession"] = {k: by_session[k] for k in list(by_session)[-BY_SESSION_MAX:]}
        stats["bySession"] = len(by_session) - BY_SESSION_MAX

    return stats


def rebuild_by_session(days=2):
    """全量扫描最近几天的日志，按会话重建 bySession。

    只影响「本会话已用」的展示，**不动 balance / todayUsage**，因此可以随时重跑。
    去重键与 sync 一致，所以 modelUsage/rawUsage 的重复上报不会算两遍。
    """
    seen = set()
    by_session = {}
    for path in iter_log_files(days):
        try:
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8", "ignore")
        except OSError:
            continue
        for line in text.split("\n"):
            event = parse_credit_line(line)
            if not event or event["key"] in seen:
                continue
            seen.add(event["key"])
            sid = event.get("session") or ""
            if sid:
                by_session[sid] = round(by_session.get(sid, 0) + event["cost"], 2)
    if len(by_session) > BY_SESSION_MAX:
        by_session = {k: by_session[k] for k in list(by_session)[-BY_SESSION_MAX:]}
    return by_session, len(seen)


def apply_events(data, ledger, events):
    """把一批新增消耗记入账本并从余额扣减。"""
    today = date.today().isoformat()
    history = ledger.setdefault("history", {})
    total = 0.0
    for event in events:
        day = event["ts"][:10].replace("/", "-") if event["ts"] else today
        history[day] = round(history.get(day, 0) + event["cost"], 2)
        if day == today:
            data["todayUsage"] = round((data.get("todayUsage") or 0) + event["cost"], 2)
        total += event["cost"]
    total = round(total, 2)
    if data.get("balance") is not None:
        data["balance"] = round(float(data["balance"]) - total, 2)
    records = ledger.setdefault("events", [])
    # 按会话累计：sessionId 就是会话 id，和页面上 data-conversation-id 一一对应
    by_session = ledger.setdefault("bySession", {})
    for event in events:
        sid = event.get("session") or ""
        if sid:
            by_session[sid] = round(by_session.get(sid, 0) + event["cost"], 2)
    records.extend({
        "ts": e["ts"], "cost": e["cost"], "kind": e["kind"], "key": e["key"]
    } for e in events)
    # 先做一次粗裁避免中间态过大，再按保留策略精裁
    ledger["events"] = records[-EVENTS_MAX * 2:] if len(records) > EVENTS_MAX * 2 else records
    prune_ledger(ledger)
    ledger["lastCost"] = events[-1]["cost"] if events else ledger.get("lastCost", 0)
    ledger["lastEventAt"] = events[-1]["ts"] if events else ledger.get("lastEventAt", "")
    ledger["lastKind"] = events[-1]["kind"] if events else ledger.get("lastKind", "")
    data["updatedAt"] = stamp()
    return total


def sync(data, dry_run=False):
    """从本地日志同步新增消耗。dry_run 不改动传入对象、不写文件。"""
    if dry_run:
        data = copy.deepcopy(data)
    roll_day(data)
    # 每日赠送：跨天首次 sync 补记 100（余额未校准/当天已补会自动跳过）。
    granted, grant_note = grant_daily(data, dry_run=dry_run)
    ledger = data.setdefault("ledger", {})
    if not ledger.get("baselineAt"):
        # 兼容老配置：以最近一次人工设值的时间作为基准，历史消耗不重复扣
        ledger["baselineAt"] = data.get("updatedAt") or stamp()
    try:
        baseline = datetime.fromisoformat(ledger["baselineAt"])
    except (ValueError, TypeError):
        baseline = None

    previous_scan = ledger.get("scan") or {}
    scanned, new_state = scan_events(ledger)
    ledger["scan"] = new_state
    seen = ledger.setdefault("seen", {})
    fresh = []
    for event in scanned:
        if event["key"] in seen:
            continue
        when = parse_ts(event["ts"])
        if baseline and when and when <= baseline:
            seen[event["key"]] = True  # 校准前的历史，标记但不扣
            continue
        fresh.append(event)
        seen[event["key"]] = True

    total = round(sum(e["cost"] for e in fresh), 2)
    if not dry_run:
        if fresh:
            apply_events(data, ledger, fresh)
        # 无变化时完全不写盘，避免挂件轮询反复刷新 mtime
        if fresh or new_state != previous_scan or granted:
            save(data)
    return {"count": len(fresh), "total": total, "events": fresh,
            "balance": data.get("balance"), "todayUsage": data.get("todayUsage"),
            "granted": granted, "grantNote": grant_note}


def main():
    parser = argparse.ArgumentParser(description="小鲸鱼积分挂件 · 配置与自动记账")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="查看当前余额与账本状态")

    setter = sub.add_parser("set", help="用官方余额校准（之后只扣新增消耗）")
    setter.add_argument("--balance", type=float, default=None, help="官方剩余积分")
    setter.add_argument("--today", type=float, default=None, help="今日已用积分")
    setter.add_argument("--note", type=str, default=None, help="备注")

    syncer = sub.add_parser("sync", help="从本地日志同步新增消耗")
    syncer.add_argument("--dry-run", action="store_true", help="只报告不写入")

    consumer = sub.add_parser("consume", help="手动补记一笔消耗")
    consumer.add_argument("--amount", type=float, required=True, help="消耗积分")
    consumer.add_argument("--note", type=str, default=None, help="备注")

    ledger_cmd = sub.add_parser("ledger", help="查看消耗流水")
    ledger_cmd.add_argument("--limit", type=int, default=10, help="显示最近 N 条")

    rebuild = sub.add_parser("rebuild-sessions", help="按日志重建各会话累计消耗（仅展示用，不动余额）")
    rebuild.add_argument("--days", type=int, default=2, help="回扫最近几天（默认 2）")

    granter = sub.add_parser("grant", help="每日赠送积分：查看状态 / 开关 / 改金额 / 手动补")
    granter.add_argument("--on", action="store_true", help="开启每日补给")
    granter.add_argument("--off", action="store_true", help="关闭每日补给")
    granter.add_argument("--amount", type=float, default=None, help="修改每次补给金额（默认 100）")
    granter.add_argument("--now", action="store_true", help="手动补记今天（已补过则跳过，除非 --force）")
    granter.add_argument("--force", action="store_true", help="配合 --now：今天已补也再补一次")
    granter.add_argument("--unmark", action="store_true", help="清除今天的已补标记（用于重补）")

    sub.add_parser("reset-today", help="今日已用清零")
    sub.add_parser("paths", help="显示配置与日志路径（安装排查用）")

    args = parser.parse_args()
    if args.cmd == "paths":
        print(f"技能目录: {BASE}")
        print(f"配置文件: {CONFIG} ({'存在' if os.path.exists(CONFIG) else '尚未生成'})")
        print(f"日志目录: {LOGS} ({'存在' if os.path.isdir(LOGS) else '不存在'})")
        print(f"WorkBuddy 目录: {WORKBUDDY_HOME}")
        return
    data = roll_day(load())

    if args.cmd == "show":
        ledger = data.get("ledger") or {}
        print(f"剩余积分: {fmt(data.get('balance'))}  （本地推算值）")
        print(f"今日已用: {fmt(data.get('todayUsage'))}")
        print(f"更新时间: {data.get('updatedAt') or '（无）'}")
        print(f"自动记账: {'开' if ledger.get('autoSync', True) else '关'}"
              f" · 基准 {ledger.get('baselineAt') or '（无）'}"
              f" · 已记 {len(ledger.get('events') or [])} 笔")
        print(f"最近一笔: {fmt(ledger.get('lastCost') or 0)} @ {ledger.get('lastEventAt') or '—'}")
        g = ledger.get("dailyGrant") or {}
        print(f"每日补给: {'开' if g.get('enabled', True) else '关'} · +{fmt(g.get('amount', DAILY_GRANT_AMOUNT))}"
              f" · 已补 {len(g.get('grantedDays') or [])} 天")
        print(f"备注: {data.get('note') or '（无）'}")
        print("---")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if args.cmd == "set":
        if args.balance is None and args.today is None and args.note is None:
            parser.error("set 至少需要 --balance / --today / --note 之一")
        if args.balance is not None:
            data["balance"] = args.balance
            # 官方数字校准：此后产生的**新消耗**才从余额里扣。
            # 注意：bySession 是各会话的历史累计（纯展示用），校准不该抹掉它，
            # 否则所有旧会话都会变成 0，页面上看起来就像"每个会话都一样"。
            data.setdefault("ledger", {})["baselineAt"] = stamp()
            # 官方数字已含当天赠送，标记今天已补，防止紧接着的 sync 再叠加 100
            mark_grant_today(data)
        if args.today is not None:
            data["todayUsage"] = args.today
        if args.note is not None:
            data["note"] = args.note
        data["updatedAt"] = stamp()
        save(data)
        print(f"已校准：剩余积分 {fmt(data.get('balance'))}，今日已用 {fmt(data.get('todayUsage'))}")
        if args.balance is not None:
            print("基准时间已重置，仅统计此后新增的本地消耗")
        return

    if args.cmd == "sync":
        result = sync(data, dry_run=args.dry_run)
        if args.dry_run:
            print(f"[试运行] 新增 {result['count']} 笔，合计 {fmt(result['total'])} 积分")
            for e in result["events"][:10]:
                print(f"  {e['ts']}  {e['kind']}  -{e['cost']}")
        else:
            print(f"同步完成：新增 {result['count']} 笔，消耗 {fmt(result['total'])}，"
                  f"余额 → {fmt(result['balance'])}，今日 {fmt(result['todayUsage'])}")
        return

    if args.cmd == "consume":
        if args.amount <= 0:
            parser.error("--amount 必须大于 0")
        ledger = data.setdefault("ledger", {})
        event = {
            "key": "m|" + stamp(),
            "ts": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
            "kind": "手动补记", "cost": round(args.amount, 2), "session": "",
        }
        apply_events(data, ledger, [event])
        if args.note:
            data["note"] = args.note
        save(data)
        print(f"已补记 {fmt(event['cost'])} 积分，余额 → {fmt(data.get('balance'))}")
        return

    if args.cmd == "ledger":
        ledger = data.get("ledger") or {}
        print("按日汇总:", json.dumps(ledger.get("history") or {}, ensure_ascii=False))
        print(f"最近 {args.limit} 笔:")
        for e in (ledger.get("events") or [])[-args.limit:]:
            print(f"  {e.get('ts')}  {e.get('kind')}  -{e.get('cost')}")
        return

    if args.cmd == "rebuild-sessions":
        by_session, scanned = rebuild_by_session(max(1, args.days))
        ledger = data.setdefault("ledger", {})
        ledger["bySession"] = by_session
        data["updatedAt"] = stamp()
        save(data)
        print(f"已重建 {len(by_session)} 个会话（扫描去重后 {scanned} 笔事件），余额未变：{fmt(data.get('balance'))}")
        for sid, cost in list(by_session.items())[-8:]:
            print(f"  {sid[:8]}…  {fmt(cost)}")
        return

    if args.cmd == "grant":
        cfg = data.setdefault("ledger", {}).setdefault("dailyGrant", {})
        changed = False
        if args.on:
            cfg["enabled"] = True
            changed = True
        if args.off:
            cfg["enabled"] = False
            changed = True
        if args.amount is not None:
            if args.amount <= 0:
                parser.error("--amount 必须大于 0")
            cfg["amount"] = round(args.amount, 2)
            changed = True
        if args.unmark:
            today = date.today().isoformat()
            marked = cfg.setdefault("grantedDays", [])
            if today in marked:
                marked.remove(today)
            changed = True
            print("已清除今天的补给标记")
        if args.now:
            cfg["enabled"] = True   # 手动补不受开关影响
            if args.force:
                today = date.today().isoformat()
                marked = cfg.setdefault("grantedDays", [])
                if today in marked:
                    marked.remove(today)
            got, note = grant_daily(data)
            changed = True
            print(f"手动补给：{note}，余额 → {fmt(data.get('balance'))}")
        if changed:
            data["updatedAt"] = stamp()
            save(data)
        granted = cfg.get("grantedDays") or []
        print(f"每日补给: {'开' if cfg.get('enabled', True) else '关'} · 每次 +{fmt(cfg.get('amount', DAILY_GRANT_AMOUNT))}"
              f" · 已补 {len(granted)} 天")
        if granted:
            print("最近补给日期: " + ", ".join(granted[-5:]))
        return

    if args.cmd == "reset-today":
        data["todayUsage"] = 0
        data["updatedAt"] = stamp()
        save(data)
        print("今日已用已清零")
        return


if __name__ == "__main__":
    main()
