#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当前技能目录打包成可分发的 WorkBuddy 插件 zip。

用法：
    python3 scripts/package.py [--out /path/to/wb-whale-credits-plugin.zip]

包内结构：
    .codebuddy-plugin/marketplace.json      插件市场清单（单插件）
    wb-whale-credits/.codebuddy-plugin/plugin.json   插件 manifest
    wb-whale-credits/SKILL.md              技能本体（可直接放进 ~/.workbuddy/skills/）
    wb-whale-credits/...                   脚本 / 模板 / 安装器

会排除本机余额账本、运行时信息与 Python 绝对路径，并做隐私扫描。
"""
import argparse
import json
import os
import sys
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = "wb-whale-credits"
VERSION = "1.1.0"

GENERIC_BAT = (
    "@echo off\n"
    "rem 通用启动器；install.py 会写入本机 Python 路径，这里只是占位\n"
    "setlocal enabledelayedexpansion\n"
    'set "PYW=pythonw"\n'
    'if exist "%~dp0config\\python_path.txt" set /p PYW=<"%~dp0config\\python_path.txt"\n'
    'start "" "!PYW!" "%~dp0scripts\\css_daemon.py"\n'
    "endlocal\n"
)
GENERIC_SH = (
    "#!/bin/bash\n"
    "# 通用启动器；install.py 会写入本机 Python 路径，这里只是占位\n"
    'cd "$(dirname "$0")" || exit 1\n'
    'if [ -f config/python_path.txt ]; then\n'
    '  PY=$(cat config/python_path.txt)\n'
    "else\n"
    "  PY=python3\n"
    "fi\n"
    '"$PY" scripts/css_daemon.py &\n'
)

MARKETPLACE = {
    "name": "wb-whale-credits-marketplace",
    "description": "小鲸鱼积分挂件插件市场（单插件）",
    "owner": {"name": "wb-whale-credits contributors"},
    "metadata": {"version": VERSION},
    "plugins": [
        {
            "name": PREFIX,
            "description": "零窗口积分挂件：把 WorkBuddy 每个会话底部那行提示原位换成"
                           "「积分：X 今日已用：Y 本会话已用：Z」，按本地日志逐笔自动记账、"
                           "数字实时更新；一键安装，自动配好调试端口与开机自启，换台电脑也能直接用。",
            "source": "./" + PREFIX,
            "category": "skill",
            "version": VERSION,
            "author": {"name": "wb-whale-credits contributors"},
            "keywords": ["积分", "余额", "挂件", "widget", "credits", "WorkBuddy"],
            "license": "MIT",
        }
    ],
}
PLUGIN = {
    "name": PREFIX,
    "version": VERSION,
    "description": "零窗口积分挂件：会话底部原位显示「积分：X 今日已用：Y 本会话已用：Z」，"
                   "本地日志自动记账 + 一键安装（含调试端口与开机自启）。",
    "author": {"name": "wb-whale-credits contributors"},
    "category": "skill",
    "keywords": ["积分", "余额", "挂件", "widget", "credits"],
    "license": "MIT",
}

SKIP_EXACT = {
    "config/credits.json",
    "config/runtime.json",
    "config/python_path.txt",
}
SKIP_DIRS = {"__pycache__", ".git", ".codebuddy-plugin", "dist"}
SKIP_SUFFIX = (".pyc", ".tmp", ".log", ".zip")
TEXT_SUFFIX = (".py", ".md", ".bat", ".sh", ".command", ".json", ".txt")


def should_skip(rel):
    if rel in SKIP_EXACT or rel.startswith(".codebuddy-plugin/"):
        return True
    if any(part in SKIP_DIRS for part in rel.split("/")[:-1]):
        return True
    return rel.endswith(SKIP_SUFFIX) or rel.startswith("_startup_")


def main():
    parser = argparse.ArgumentParser(description="打包小鲸鱼积分挂件插件")
    parser.add_argument("--out", default=os.path.join(
        os.getcwd(), f"wb-whale-credits-plugin-v{VERSION}.zip"))
    args = parser.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    texts, count = [], 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        def put(arc, content):
            nonlocal count
            zf.writestr(arc, content)
            texts.append((arc, content))
            count += 1

        put(".codebuddy-plugin/marketplace.json",
            json.dumps(MARKETPLACE, ensure_ascii=False, indent=2) + "\n")
        put(f"{PREFIX}/.codebuddy-plugin/plugin.json",
            json.dumps(PLUGIN, ensure_ascii=False, indent=2) + "\n")

        for root, dirs, files in os.walk(BASE):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for filename in sorted(files):
                path = os.path.join(root, filename)
                rel = os.path.relpath(path, BASE).replace(os.sep, "/")
                if should_skip(rel):
                    continue
                arc = f"{PREFIX}/{rel}"
                if filename in ("start_whale.bat", "start_whale.command", "start_whale.sh"):
                    put(arc, GENERIC_BAT if filename.endswith(".bat") else GENERIC_SH)
                    continue
                zf.write(path, arc)
                count += 1
                if filename.endswith(TEXT_SUFFIX):
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                            texts.append((arc, fh.read()))
                    except OSError:
                        pass

        for extra in ("start_whale.command", "start_whale.sh"):
            arc = f"{PREFIX}/{extra}"
            if arc not in zf.namelist():
                put(arc, GENERIC_SH)

        names = zf.namelist()
        required = [
            ".codebuddy-plugin/marketplace.json",
            f"{PREFIX}/.codebuddy-plugin/plugin.json",
            f"{PREFIX}/SKILL.md",
            f"{PREFIX}/install.py",
            f"{PREFIX}/install.bat",
            f"{PREFIX}/scripts/credits.py",
            f"{PREFIX}/scripts/css_daemon.py",
            f"{PREFIX}/scripts/cdp_inject.js",
            f"{PREFIX}/scripts/fix_shortcut.py",
            f"{PREFIX}/config/credits.template.json",
        ]
        missing = [name for name in required if name not in names]

    home = os.path.expanduser("~")
    needles = {home, home.replace("\\", "/")}
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(user) >= 4:
        needles.add(user)
    hits = []
    for name, text in texts:
        for needle in needles:
            if needle and needle in text:
                hits.append((name, needle))
                break

    print(f"打包完成: {out}")
    print(f"条目数: {count}  大小: {os.path.getsize(out)} bytes")
    print("隐私扫描:", "干净（无本机路径 / 用户名 / 账本）" if not hits else f"发现残留 {hits}")
    if missing:
        print("[失败] 缺少必需条目:", missing)
        sys.exit(3)
    for name in (".codebuddy-plugin/marketplace.json",
                 f"{PREFIX}/.codebuddy-plugin/plugin.json"):
        with zipfile.ZipFile(out) as zf:
            json.loads(zf.read(name))
    print("manifest JSON 校验: OK")
    if hits:
        sys.exit(4)


if __name__ == "__main__":
    main()
