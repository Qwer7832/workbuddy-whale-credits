#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小鲸鱼积分挂件 —— 跨机器一键安装。

用法：
    python install.py                  # 本机安装（Windows 上双击 install.bat 也行）
    python install.py --check          # 只体检，不写任何文件
    python install.py --cdp-port 9223  # 指定 WorkBuddy 调试端口（默认 9223）
    python install.py --no-autostart   # 不设开机自启
    python install.py --no-shortcut    # 不去改 WorkBuddy 快捷方式
    python install.py --no-start       # 装完不自动拉起守护进程

它做六件事：
  1. 探测 Python（守护进程只在后台跑，不需要 tkinter）与 Node（页面注入器）；
  2. 把技能复制到 <WorkBuddy 数据目录>/skills/wb-whale-credits（已经在那儿就跳过）；
  3. 生成干净的 config/credits.json（已有账本不覆盖）和 runtime.json（含 cdpPort）；
  4. Windows：给所有 WorkBuddy 快捷方式补上调试端口参数——换机器最容易漏的一步，
     漏了就是「装好了但界面上什么都没有」；
  5. 写开机自启（Win: HKCU Run / macOS: LaunchAgent / Linux: autostart）；
  6. 拉起后台守护进程，并打印余额校准指引。

不需要管理员权限、不联网、不修改 WorkBuddy 自身文件、不创建任何窗口。
"""
import argparse
import glob
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE, "config")
TEMPLATE = os.path.join(CONFIG_DIR, "credits.template.json")
SCRIPTS = os.path.join(BASE, "scripts")
SKILL_NAME = "wb-whale-credits"

# 复制进技能目录时不该带过去的东西：账本、日志、本机路径、字节码
COPY_IGNORE = {"credits.json", "runtime.json", "python_path.txt", "inject.log",
               "__pycache__", ".codebuddy-plugin", "dist"}
COPY_IGNORE_SUFFIX = (".pyc", ".zip", ".bak-credits")

CLEAN_CONFIG = {
    "balance": None,
    "todayUsage": 0,
    "date": None,
    "unit": "积分",
    "updatedAt": "",
    "note": "在 WorkBuddy 左下角头像 → 套餐与积分 查看官方余额，然后校准",
    "window": {"x": None, "y": None, "alpha": 0.95, "onlyOnWorkBuddy": False,
               "globalTopmost": False, "titleBarDock": True, "dockPosition": "bottom",
               "twoPages": False, "cssSlogan": True},
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
    },
}


def ordered_unique(items):
    seen, out = set(), []
    for item in items:
        if not item:
            continue
        key = os.path.normcase(os.path.abspath(item))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def write_text(path, content, executable=False, encoding="utf-8", newline="\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding, newline=newline) as fh:
        fh.write(content)
    if executable:
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass


# ---------------------------------------------------------------- Python / Node

def iter_python_candidates():
    """按平台列出可能的 Python 解释器，逐一探测标准库可用性。"""
    yield sys.executable
    for name in ("pythonw", "python3", "python", "py"):
        found = shutil.which(name)
        if found:
            yield found
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        home = os.path.expanduser("~")
        patterns = [
            os.path.join(local, "Python", "bin", "python*.exe"),
            os.path.join(local, "Programs", "Python", "Python3*", "python*.exe"),
            os.path.join("C:\\", "Python3*", "python*.exe"),
            os.path.join(home, ".workbuddy", "binaries", "python", "versions", "*", "python.exe"),
        ]
        for pattern in patterns:
            for path in sorted(glob.glob(pattern)):
                yield path
        try:
            result = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=6)
            for line in result.stdout.splitlines():
                match = re.search(r"[A-Za-z]:\\[^\r\n]*?\.exe", line)
                if match and os.path.isfile(match.group(0)):
                    yield match.group(0)
        except Exception:
            pass
    else:
        for pattern in ("/usr/bin/python3", "/usr/local/bin/python3",
                        "/opt/homebrew/bin/python3",
                        "/Library/Frameworks/Python.framework/Versions/*/bin/python3"):
            for path in sorted(glob.glob(pattern)):
                yield path


def probe_python(python):
    try:
        result = subprocess.run(
            [python, "-c", "import json, socket, sys; print(sys.version.split()[0])"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception:
        return False, ""


def detect_python():
    found = []
    for python in ordered_unique(iter_python_candidates()):
        ok, version = probe_python(python)
        if ok:
            found.append((python, version))
    if not found:
        return None
    # CLI 解释器要有 stdout；Windows 上后台跑用同目录 pythonw.exe 才不弹黑框
    cli = version = None
    for python, ver in found:
        if os.path.basename(python).lower().startswith("pythonw"):
            continue
        cli, version = python, ver
        break
    if cli is None:
        cli, version = found[0]
    gui = cli
    if os.name == "nt":
        candidate = os.path.join(os.path.dirname(cli), "pythonw.exe")
        if os.path.isfile(candidate):
            gui = candidate
    return {"python": cli, "pythonGui": gui, "pythonVersion": version or "unknown"}


def find_node():
    """换机器时 node 常常只在 WorkBuddy 自带运行时里，PATH 中并没有。"""
    candidates = []
    for name in ("node", "node.exe"):
        path = shutil.which(name)
        if path:
            candidates.append(path)
    home = os.path.expanduser("~")
    if os.name == "nt":
        patterns = [
            os.path.join(home, ".workbuddy", "binaries", "node", "versions", "*", "node.exe"),
            os.path.join(home, ".workbuddy", "binaries", "node", "versions", "*", "node.EXE"),
            os.path.join(home, ".ai-manager", "runtimes", "node", "*", "node.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs", "node.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "node.exe"),
        ]
        for pattern in patterns:
            candidates.extend(sorted(glob.glob(pattern), reverse=True))
    else:
        candidates.extend(["/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"])
    for path in ordered_unique(candidates):
        try:
            result = subprocess.run([path, "-v"], capture_output=True, text=True, timeout=8,
                                    creationflags=0x08000000 if os.name == "nt" else 0)
            if result.returncode == 0 and result.stdout.strip().startswith("v"):
                return path, result.stdout.strip()
        except Exception:
            continue
    return None, ""


# ---------------------------------------------------------------- 部署

def deploy_skill(wb_home):
    """把技能复制到 <wb_home>/skills/<name>；已在那儿就原地使用。"""
    target = os.path.join(wb_home, "skills", SKILL_NAME)
    if os.path.normcase(os.path.abspath(BASE)) == os.path.normcase(os.path.abspath(target)):
        return BASE, False

    def ignore(_dir, names):
        return [n for n in names
                if n in COPY_IGNORE or n.endswith(COPY_IGNORE_SUFFIX)]

    os.makedirs(target, exist_ok=True)
    shutil.copytree(BASE, target, dirs_exist_ok=True, ignore=ignore)
    return target, True


def ensure_config(base):
    config_dir = os.path.join(base, "config")
    config = os.path.join(config_dir, "credits.json")
    template = os.path.join(config_dir, "credits.template.json")
    os.makedirs(config_dir, exist_ok=True)
    if os.path.exists(config):
        return config, False
    if os.path.isfile(template):
        shutil.copyfile(template, config)
    else:
        write_text(config, json.dumps(CLEAN_CONFIG, ensure_ascii=False, indent=2))
    return config, True


def make_launchers(base, info, wb_home, logs, port, cdp_port):
    cli, gui = info["python"], info["pythonGui"]
    daemon = os.path.join(base, "scripts", "css_daemon.py")
    if os.name == "nt":
        content = (
            "@echo off\n"
            "rem Generated by install.py - pure background daemon, no window\n"
            "setlocal\n"
            f'set "WORKBUDDY_HOME={wb_home}"\n'
            f'set "WB_WHALE_LOGS={logs}"\n'
            f'set "WB_WHALE_PORT={port}"\n'
            f'set "WB_CDP_PORT={cdp_port}"\n'
            f'start "" "{gui}" "{daemon}"\n'
            "endlocal\n"
        )
        name = "start_whale.bat"
        # cmd.exe 按系统 ANSI 代码页（中文 Windows 为 936）解析 .bat，
        # 这里必须用 mbcs + CRLF，否则含中文的绝对路径会变成乱码。
        write_text(os.path.join(base, name), content, encoding="mbcs", newline="\r\n")
    else:
        content = (
            "#!/bin/bash\n"
            "# 由 install.py 生成\n"
            'cd "$(dirname "$0")" || exit 1\n'
            f'export WORKBUDDY_HOME="{wb_home}"\n'
            f'export WB_WHALE_LOGS="{logs}"\n'
            f'export WB_WHALE_PORT="{port}"\n'
            f'export WB_CDP_PORT="{cdp_port}"\n'
            f'"{cli}" "{daemon}" &\n'
        )
        name = "start_whale.command" if sys.platform == "darwin" else "start_whale.sh"
        write_text(os.path.join(base, name), content, executable=True)
    return name


def patch_shortcuts(base, cdp_port):
    """给所有 WorkBuddy 快捷方式补调试端口参数（Windows 专属）。"""
    if os.name != "nt":
        return None
    sys.path.insert(0, os.path.join(base, "scripts"))
    try:
        import fix_shortcut
    except Exception as exc:  # noqa: BLE001
        print(f"  跳过：无法载入 fix_shortcut.py（{exc}）")
        return []

    args = f"--remote-debugging-port={cdp_port} --remote-allow-origins=*"
    results = fix_shortcut.patch_all(args, verbose=False)
    if not results:
        print("  没找到 WorkBuddy 快捷方式（可从开始菜单启动一次再重跑本脚本）")
        return results
    for path, outcome in results:
        label = {"ok": "已写入", "same": "已是目标参数", "other": "已有其它参数，未动",
                 "dry": "演练", "fail": "失败", None: "跳过"}.get(outcome, str(outcome))
        print(f"  {label:<16} {os.path.basename(path)}")
    return results


def enable_autostart(info, daemon):
    """写开机自启，返回描述文字；失败返回 None。"""
    try:
        if os.name == "nt":
            import winreg
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE)
            try:
                winreg.SetValueEx(key, "WhaleCredits", 0, winreg.REG_SZ,
                                  f'"{info["pythonGui"]}" "{daemon}"')
            finally:
                winreg.CloseKey(key)
            return "HKCU\\...\\Run\\WhaleCredits"

        if sys.platform == "darwin":
            path = os.path.expanduser("~/Library/LaunchAgents/com.wbwhale.credits.plist")
            write_text(path, (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                '  <key>Label</key><string>com.wbwhale.credits</string>\n'
                '  <key>ProgramArguments</key><array>\n'
                f'    <string>{info["python"]}</string>\n'
                f'    <string>{daemon}</string>\n'
                '  </array>\n'
                '  <key>RunAtLoad</key><true/>\n'
                '</dict></plist>\n'
            ))
            subprocess.run(["launchctl", "unload", path], capture_output=True)
            subprocess.run(["launchctl", "load", path], capture_output=True)
            return "~/Library/LaunchAgents/com.wbwhale.credits.plist"

        path = os.path.expanduser("~/.config/autostart/wb-whale-credits.desktop")
        write_text(path, (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=小鲸鱼积分\n"
            f'Exec="{info["python"]}" "{daemon}"\n'
            "X-GNOME-Autostart-enabled=true\n"
            "Terminal=false\n"
        ))
        return "~/.config/autostart/wb-whale-credits.desktop"
    except Exception as exc:  # noqa: BLE001
        print(f"  自启写入失败：{exc}")
        return None


def start_daemon(info, daemon):
    try:
        subprocess.Popen(
            [info["pythonGui"], daemon],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0, close_fds=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  守护进程启动失败：{exc}")
        return False


def port_open(port, timeout=1.0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="小鲸鱼积分挂件 · 跨机器一键安装")
    parser.add_argument("--check", action="store_true", help="只体检，不写任何文件")
    parser.add_argument("--workbuddy-home", default=None, help="WorkBuddy 数据目录")
    parser.add_argument("--logs", default=None, help="会话日志目录（默认 <home>/logs）")
    parser.add_argument("--port", type=int, default=48765, help="守护进程单实例锁端口")
    parser.add_argument("--cdp-port", type=int, default=9223, help="WorkBuddy 调试端口")
    parser.add_argument("--no-autostart", action="store_true", help="不设开机自启")
    parser.add_argument("--no-shortcut", action="store_true", help="不改 WorkBuddy 快捷方式")
    parser.add_argument("--no-start", action="store_true", help="装完不自动启动守护进程")
    args = parser.parse_args()

    wb_home = (args.workbuddy_home or os.environ.get("WORKBUDDY_HOME")
               or os.path.join(os.path.expanduser("~"), ".workbuddy"))
    logs = args.logs or os.environ.get("WB_WHALE_LOGS") or os.path.join(wb_home, "logs")

    print("=" * 60)
    print("小鲸鱼积分挂件 · 安装")
    print("=" * 60)
    print(f"系统      : {platform.system()} {platform.release()}")
    print(f"安装源    : {BASE}")

    info = detect_python()
    if not info:
        print("\n[失败] 没找到可用的 Python 3，后台守护无法启动。")
        print("  请装一个 Python 3（https://www.python.org/downloads/ 勾选 Add to PATH），再重跑。")
        sys.exit(2)
    print(f"Python    : {info['python']}（后台用 {os.path.basename(info['pythonGui'])}）")

    node, node_version = find_node()
    if node:
        print(f"Node      : {node}（{node_version}）")
    else:
        print("Node      : 未找到 —— 界面注入会失效")
        print("            装 Node.js，或先启动一次 WorkBuddy 让自带运行时落地，再重跑本脚本。")

    print(f"WorkBuddy : {wb_home}（{'存在' if os.path.isdir(wb_home) else '未找到'}）")
    print(f"日志目录  : {logs}（{'存在' if os.path.isdir(logs) else '尚未生成，装好 WorkBuddy 后自动出现'}）")
    if not os.path.isdir(wb_home):
        print("            [提示] 数据目录在别处就加 --workbuddy-home \"路径\"，或设 WORKBUDDY_HOME。")

    for name in ("credits.py", "css_daemon.py", "cdp_inject.js", "fix_shortcut.py"):
        path = os.path.join(SCRIPTS, name)
        if not os.path.isfile(path):
            print(f"[失败] 缺少 {name}，安装包不完整。")
            sys.exit(3)
        if name.endswith(".py"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    compile(fh.read(), path, "exec")
            except (OSError, SyntaxError) as exc:
                print(f"[失败] {name} 无法编译：{exc}")
                sys.exit(3)
    print("脚本自检  : OK")

    cdp_ready = port_open(args.cdp_port)
    print(f"调试端口  : {args.cdp_port} —— {'已在监听' if cdp_ready else '未监听（WorkBuddy 需要带参数重启）'}")

    if args.check:
        print("\n[check] 环境可用，未写入任何文件。去掉 --check 就是正式安装。")
        return

    print("-" * 60)
    base, deployed = deploy_skill(wb_home)
    print(f"技能目录  : {base}" + ("（已从安装源复制过去）" if deployed else "（原地安装）"))

    config, created = ensure_config(base)
    print(f"配置      : {'已生成 ' if created else '保留现有账本 '}{config}")

    runtime = {
        "python": info["python"],
        "pythonGui": info["pythonGui"],
        "pythonVersion": info["pythonVersion"],
        "node": node,
        "nodeVersion": node_version,
        "os": platform.system(),
        "workbuddyHome": wb_home,
        "logs": logs,
        "port": args.port,
        "cdpPort": args.cdp_port,
        "installedAt": datetime.now().isoformat(timespec="seconds"),
        "version": "1.1.0",
    }
    write_text(os.path.join(base, "config", "runtime.json"),
               json.dumps(runtime, ensure_ascii=False, indent=2))
    write_text(os.path.join(base, "config", "python_path.txt"), info["pythonGui"])
    launcher = make_launchers(base, info, wb_home, logs, args.port, args.cdp_port)
    print(f"运行时    : config/runtime.json（cdpPort={args.cdp_port}）")
    print(f"启动器    : {launcher}")

    if os.name == "nt" and not args.no_shortcut:
        print("快捷方式  : 补调试端口参数")
        patch_shortcuts(base, args.cdp_port)
    elif os.name == "nt":
        print("快捷方式  : 已跳过（--no-shortcut）")

    daemon = os.path.join(base, "scripts", "css_daemon.py")
    if not args.no_autostart:
        where = enable_autostart(info, daemon)
        print(f"开机自启  : {where or '未设置'}")
    else:
        print("开机自启  : 已跳过（--no-autostart）")

    if not args.no_start:
        ok = start_daemon(info, daemon)
        print(f"后台守护  : {'已启动' if ok else '启动失败，可稍后双击 ' + launcher}")

    print("-" * 60)
    print("完成。还需要三步让它真的出现：")
    print("  1) 完全退出 WorkBuddy（托盘也要退出），再用桌面图标启动"
          + ("——图标已经带上调试端口了" if os.name == "nt" else ""))
    print("     判断标准：任务管理器里 WorkBuddy 的命令行含 "
          f"--remote-debugging-port={args.cdp_port}")
    print("  2) WorkBuddy 左下角头像 →「套餐与积分」看一眼官方余额")
    print(f"  3) 校准：{os.path.basename(info['python'])} \"{os.path.join(base, 'scripts', 'credits.py')}\" "
          "set --balance <那个数字>")
    print("     之后每笔消耗自动从本地日志里扣，会话底部那行会实时变。")
    print(f"  4) 想手动重启挂件：双击 {launcher}")
    if node is None:
        print("\n[注意] 没找到 Node，注入器不会工作；装好 Node 后重启挂件即可，无需重装。")


if __name__ == "__main__":
    main()
