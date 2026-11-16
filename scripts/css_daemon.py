#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小鲸鱼积分 · 纯后台守护进程（零窗口）。

不 import tkinter、不创建任何窗口、不往桌面画任何东西。只做这些事：
  1. 每 10 秒调用 credits.sync() 增量扫描 WorkBuddy 本地日志记账；
  2. 管着一个 Node 注入器（scripts/cdp_inject.js，CREATE_NO_WINDOW 启动）：
     它通过本机调试端口把积分文案注入 WorkBuddy 页面，原位替换首页底部
     「内容由 AI 生成，请核实重要信息」那行字——零窗口、原生文字。
     注入器挂了会自动重启；WorkBuddy 没开调试端口时它只是空转等待。
  3. 主题 CSS 顺带回写（对将来支持热更新的版本有用）。

单实例锁沿用 127.0.0.1:48765。
"""
import glob
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))
import credits as ledger  # noqa: E402

TEXT_DECIMAL_SEP = "."
LOCK_PORT = int(os.environ.get("WB_WHALE_PORT", "48765"))
TICK_SEC = 2
SYNC_EVERY = 5  # 5 × 2s = 10s

SLOGAN_BEGIN = "/* WB-CREDITS-SLOGAN:BEGIN (auto-generated, do not edit by hand) */"
SLOGAN_END = "/* WB-CREDITS-SLOGAN:END */"
DISCLAIMER_TEXT = "内容由 AI 生成，请核实重要信息"
# 官方规则是 .wb-home-route:not(.wb-calendar-route):after，特异性 (0,4,1)；
# 选择器必须带上同样的 :not() 才能压过它，否则 content 永远被官方默认值盖住。
SLOGAN_SELECTORS = (
    ":root .wb-home-route:not(.wb-calendar-route)::after",
    ":root:lang(zh) .wb-home-route:not(.wb-calendar-route)::after",
    ":root:lang(zh-TW) .wb-home-route:not(.wb-calendar-route)::after",
    ":root:lang(zh-HK) .wb-home-route:not(.wb-calendar-route)::after",
    ":root:lang(zh-MO) .wb-home-route:not(.wb-calendar-route)::after",
    ":root:lang(zh-Hant) .wb-home-route:not(.wb-calendar-route)::after",
)

PRELOAD_PATH = os.path.join(os.path.expanduser("~"), ".workbuddy",
                            "app", "electron-log-preload.js")
PRELOAD_BEGIN = "/* WB-CREDITS-SLOGAN-PRELOAD:BEGIN */"
PRELOAD_END = "/* WB-CREDITS-SLOGAN-PRELOAD:END */"
# 注意：Electron 20+ 的 preload 默认沙箱化，require('fs'/'os'/'path') 会被禁止。
# 所以这里只用 DOM API，文案由守护进程在 __WB_TEXT__ 处回写，不碰 Node 模块。
PRELOAD_TEMPLATE = """/* WB-CREDITS-SLOGAN-PRELOAD:BEGIN */
/* Injected by wb-whale-credits. Sandbox-safe: DOM only, no require(). */
try {
  (function () {
    var TEXT = '__WB_TEXT__';
    var ID = 'wb-credits-slogan-style';
    function inject() {
      try {
        var d = document;
        if (!d || !d.documentElement) return;
        var css = ":root{--wb-home-slogan:'" + TEXT + "'}";
        var el = d.getElementById(ID);
        if (el) { if (el.textContent !== css) el.textContent = css; return; }
        el = d.createElement('style');
        el.id = ID;
        el.textContent = css;
        (d.head || d.documentElement).appendChild(el);
        try { console.log('[wb-credits] slogan preload injected'); } catch (e) {}
      } catch (e) {}
    }
    inject();
    try {
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inject, { once: true });
      }
    } catch (e) {}
    try { setInterval(inject, 3000); } catch (e) {}
  })();
} catch (e) {}
/* WB-CREDITS-SLOGAN-PRELOAD:END */"""


def fmt_plain(value):
    """不带千分位分隔符的数字；小数分隔符由 TEXT_DECIMAL_SEP 决定。"""
    if value is None:
        return "尚未设置"
    try:
        number = float(value)
        return f"{number:.0f}" if number.is_integer() else f"{number:.2f}"
    except (TypeError, ValueError):
        return str(value)


def slogan_text(cfg):
    def num(v):
        return fmt_plain(v).replace(".", TEXT_DECIMAL_SEP)
    return f"积分：{num(cfg.get('balance'))} 今日已用：{num(cfg.get('todayUsage', 0))}"


def js_quote(text):
    return text.replace("\\", "\\\\").replace("'", "\\'")


def slogan_css_block(text):
    """兜底：主题 CSS 生效时，伪元素里同样只放积分那一行（官方声明已去掉）。"""
    safe = js_quote(text)
    return (SLOGAN_BEGIN + "\n"
            + ":root {\n  --wb-home-slogan: '" + safe + "';\n}\n"
            + ",\n".join(SLOGAN_SELECTORS) + " {\n"
            + f"  content: '{safe}';\n"
            + "}\n" + SLOGAN_END)


def preload_block(text):
    return PRELOAD_TEMPLATE.replace("__WB_TEXT__", js_quote(text))


def css_slogan_files():
    base = os.path.join(os.path.expanduser("~"), ".workbuddy",
                        "appearance-resources")
    return sorted(set(glob.glob(os.path.join(base, "*", "skin.v2.css"))
                      + glob.glob(os.path.join(base, "*", "*", "skin.css"))))


def update_css_slogan(cfg):
    """主题 CSS 标记段；仅内容变化时才落盘（默认外观下它不加载，属双保险）。"""
    block = slogan_css_block(slogan_text(cfg))
    for path in css_slogan_files():
        try:
            old = io.open(path, encoding="utf-8").read()
            if SLOGAN_BEGIN in old and SLOGAN_END in old:
                a = old.index(SLOGAN_BEGIN)
                b = old.index(SLOGAN_END, a) + len(SLOGAN_END)
                new = old[:a] + block + old[b:]
            else:
                new = old.rstrip() + "\n\n" + block + "\n"
            if new == old:
                continue
            bak = path + ".bak-credits"
            if not os.path.exists(bak):
                shutil.copy2(path, bak)
            io.open(path, "w", encoding="utf-8", newline="\n").write(new)
        except Exception:
            pass


def update_preload(text):
    """维护 session preload 注入段：缺失补回、文案变化时更新，幂等。"""
    try:
        old = io.open(PRELOAD_PATH, encoding="utf-8").read()
    except Exception:
        return
    block = preload_block(text)
    if PRELOAD_BEGIN in old and PRELOAD_END in old:
        a = old.index(PRELOAD_BEGIN)
        b = old.index(PRELOAD_END, a) + len(PRELOAD_END)
        new = old[:a] + block + old[b:]
    else:
        new = old.rstrip() + "\n" + block + "\n"
    if new == old:
        return
    try:
        bak = PRELOAD_PATH + ".bak-credits"
        if not os.path.exists(bak):
            shutil.copy2(PRELOAD_PATH, bak)
        io.open(PRELOAD_PATH, "w", encoding="utf-8", newline="\n").write(new)
    except Exception:
        pass


INJECT_JS = os.path.join(BASE, "scripts", "cdp_inject.js")
RUNTIME_JSON = os.path.join(BASE, "config", "runtime.json")
DEFAULT_CDP_PORT = "9223"


def cdp_port():
    """CDP 端口：环境变量 > runtime.json > 9223（安装器把实际端口写进 runtime.json）。"""
    port = os.environ.get("WB_CDP_PORT")
    if port:
        return str(port)
    try:
        with io.open(RUNTIME_JSON, encoding="utf-8") as fh:
            value = json.load(fh).get("cdpPort")
        if value:
            return str(value)
    except Exception:
        pass
    return DEFAULT_CDP_PORT


def node_candidates():
    """换台电脑时 node 常常只存在于 WorkBuddy 自带的运行时里，PATH 中并没有。"""
    out = []

    def add(path):
        if not path:
            return
        try:
            path = os.path.abspath(path)
        except Exception:
            return
        if os.path.isfile(path) and path not in out:
            out.append(path)

    for name in ("node", "node.exe"):
        add(shutil.which(name))
    home = os.path.expanduser("~")
    if os.name == "nt":
        patterns = [
            os.path.join(home, ".workbuddy", "binaries", "node", "versions", "*", "node.exe"),
            os.path.join(home, ".ai-manager", "runtimes", "node", "*", "node.EXE"),
            os.path.join(home, ".ai-manager", "runtimes", "node", "*", "node.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs", "node.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "node.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "nodejs", "node.exe"),
        ]
        for pattern in patterns:
            for path in sorted(glob.glob(pattern), reverse=True):
                add(path)
    else:
        for path in ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"):
            add(path)
    return out


def find_node():
    flags = 0x08000000 if os.name == "nt" else 0
    for path in node_candidates():
        try:
            result = subprocess.run([path, "-v"], capture_output=True, text=True,
                                    timeout=8, creationflags=flags)
            if result.returncode == 0 and result.stdout.strip().startswith("v"):
                return path
        except Exception:
            continue
    return None


def _spawn_injector():
    """启动无窗口的 Node 注入器；找不到 node 就返回 None（不报错）。"""
    node = find_node()
    if not node or not os.path.exists(INJECT_JS):
        return None
    try:
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        env = dict(os.environ)
        env["WB_CDP_PORT"] = cdp_port()
        return subprocess.Popen(
            [node, INJECT_JS],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True, env=env,
        )
    except Exception:
        return None


def ensure_autostart_args():
    """自愈：WorkBuddy 更新/重装会重写开机自启项并丢掉调试端口参数。

    用户从自启项拉起 WorkBuddy 时就是裸命令行，注入必然失效（表现为
    「用着用着又不显示了」）。这里在守护启动时补回一次参数。
    """
    if os.name != "nt":
        return
    try:
        import winreg
        name = "WorkBuddy.WorkBuddy"
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                value, _ = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                return
            if "remote-debugging-port" in value:
                return
            exe = value.strip().strip('"').strip()
            if not exe or not os.path.isfile(exe):
                return
            winreg.SetValueEx(
                key, name, 0, winreg.REG_SZ,
                '"%s" --remote-debugging-port=%s --remote-allow-origins=*'
                % (exe, cdp_port()))
    except Exception:
        pass


def main():
    # 单实例：占用本地端口，第二个实例直接退出（与旧挂件共用，避免双开）
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
    except OSError:
        sys.exit(0)

    ensure_autostart_args()
    injector = _spawn_injector()
    ticks = 0
    while True:
        ticks += 1
        try:
            if injector is None or injector.poll() is not None:
                injector = _spawn_injector()   # 首次启动或崩溃后重启
            cfg = ledger.load()
            if ticks == 1 or ticks % SYNC_EVERY == 0:
                if (cfg.get("ledger") or {}).get("autoSync", True):
                    ledger.sync(ledger.load())
                    cfg = ledger.load()
            update_css_slogan(cfg)
        except Exception:
            pass
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    main()
