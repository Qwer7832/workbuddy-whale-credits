#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把调试端口参数写进 WorkBuddy 的 .lnk（桌面 / 开始菜单）。

为什么需要：CDP 注入要求 WorkBuddy 进程启动时带
`--remote-debugging-port=9223 --remote-allow-origins=*`。用户习惯双击旧图标，
而旧图标没有参数，注入会静默失效。这里直接改 .lnk 的 Shell Link 结构
（LinkFlags 置 HasArguments，并在 StringData 里插入 COMMAND_LINE_ARGUMENTS），
不依赖 WScript.Shell COM（本机安全策略会拦）。

幂等：已有相同参数则跳过；改动前自动备份为 *.lnk.bak-credits。
用法：python scripts/fix_shortcut.py [--args "..."] [--dry-run]
"""
import argparse
import glob
import os
import shutil
import struct
import sys

DEFAULT_ARGS = "--remote-debugging-port=9223 --remote-allow-origins=*"

HAS_ID_LIST = 0x1
HAS_LINK_INFO = 0x2
HAS_NAME = 0x4
HAS_REL_PATH = 0x8
HAS_WORK_DIR = 0x10
HAS_ARGUMENTS = 0x20
HAS_ICON = 0x40
IS_UNICODE = 0x80


def shortcut_paths():
    """列出这台机器上所有可能被用来启动 WorkBuddy 的快捷方式。"""
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    # 中文系统桌面可能被重定向，优先读注册表里的实际路径
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        val, _ = winreg.QueryValueEx(key, "Desktop")
        desktop = os.path.expandvars(val)
        winreg.CloseKey(key)
    except Exception:
        pass

    appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    programdata = os.environ.get("ProgramData") or r"C:\ProgramData"
    start_menu = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")
    programdata_menu = os.path.join(programdata, "Microsoft", "Windows", "Start Menu", "Programs")
    taskbar = os.path.join(appdata, "Microsoft", "Internet Explorer", "Quick Launch",
                           "User Pinned", "TaskBar")
    classic_quick = os.path.join(appdata, "Microsoft", "Internet Explorer", "Quick Launch")

    found = [
        os.path.join(desktop, "WorkBuddy.lnk"),
        os.path.join(start_menu, "WorkBuddy.lnk"),
        os.path.join(programdata_menu, "WorkBuddy.lnk"),
        os.path.join(taskbar, "WorkBuddy.lnk"),
        os.path.join(classic_quick, "WorkBuddy.lnk"),
    ]
    # 名字可能带后缀（「WorkBuddy 副本」「WorkBuddy (2)」），一并扫出来
    for base_dir in (desktop, start_menu, programdata_menu, taskbar, classic_quick):
        try:
            found.extend(glob.glob(os.path.join(base_dir, "WorkBuddy*.lnk")))
        except Exception:
            pass

    seen, out = set(), []
    for path in found:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            out.append(path)
    return out


def patch_all(args, dry_run=False, verbose=True):
    """给所有找到的快捷方式打补丁，返回 [(路径, 结果)]。"""
    if verbose:
        print(f"找到 {len(shortcut_paths())} 个 WorkBuddy 快捷方式")
    results = []
    for path in shortcut_paths():
        results.append((path, patch(path, args, dry_run)))
    return results


def parse(buf):
    header = struct.unpack("<I", buf[0:4])[0]
    flags = struct.unpack("<I", buf[20:24])[0]
    pos = header
    if flags & HAS_ID_LIST:
        size = struct.unpack("<H", buf[pos:pos + 2])[0]
        pos += 2 + size
    if flags & HAS_LINK_INFO:
        pos += struct.unpack("<I", buf[pos:pos + 4])[0]
    fields = {}
    unicode = bool(flags & IS_UNICODE)
    insert_at = None

    def read_str(p):
        count = struct.unpack("<H", buf[p:p + 2])[0]
        raw = buf[p + 2:p + 2 + count * (2 if unicode else 1)]
        text = raw.decode("utf-16-le" if unicode else "mbcs", "ignore")
        return text, p + 2 + len(raw)

    for name, bit in (("name", HAS_NAME), ("relative", HAS_REL_PATH),
                      ("workdir", HAS_WORK_DIR), ("args", HAS_ARGUMENTS),
                      ("icon", HAS_ICON)):
        if flags & bit:
            text, pos = read_str(pos)
            fields[name] = text
            if name in ("name", "relative", "workdir"):
                insert_at = pos
    return header, flags, fields, insert_at, unicode


def patch(path, args, dry_run=False):
    if not os.path.exists(path):
        print("  跳过（不存在）:", path)
        return None
    buf = bytearray(open(path, "rb").read())
    _, flags, fields, insert_at, unicode = parse(bytes(buf))
    if flags & HAS_ARGUMENTS and fields.get("args", "").strip() == args.strip():
        print("  已是目标参数，跳过:", path)
        return "same"
    if flags & HAS_ARGUMENTS:
        print("  已有其它参数，未改动:", path, "->", fields.get("args"))
        return "other"
    if insert_at is None:
        print("  结构里找不到 StringData 锚点，未改动:", path)
        return "fail"
    entry = struct.pack("<H", len(args)) + args.encode("utf-16-le")
    new = buf[:insert_at] + entry + buf[insert_at:]
    new[20:24] = struct.pack("<I", flags | HAS_ARGUMENTS)
    if dry_run:
        print("  [dry-run] 将写入", path, "args =", args)
        return "dry"
    backup = path + ".bak-credits"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    open(path, "wb").write(new)
    _, flags2, fields2, _, _ = parse(bytes(open(path, "rb").read()))
    ok = bool(flags2 & HAS_ARGUMENTS) and fields2.get("args") == args
    print("  已写入%s:" % ("并校验通过" if ok else "但校验失败"), path)
    print("    args =", fields2.get("args"))
    return "ok" if ok else "fail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--args", default=DEFAULT_ARGS)
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args()
    results = [outcome for _, outcome in patch_all(ns.args, ns.dry_run)]
    if all(r in (None, "same") for r in results):
        print("无需改动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
