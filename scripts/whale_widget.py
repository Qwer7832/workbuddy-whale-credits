#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小鲸鱼积分挂件 —— 无边框、半透明（Windows / macOS / Linux）。

显示策略: 默认 `window.cssSlogan=true` —— **不新建任何窗口**。
          首页底部「内容由 AI 生成，请核实重要信息」是主题 CSS 里
          `.wb-home-route::after { content: ... }` 画出来的，把当前积分文案写进
          `~/.workbuddy/appearance-resources/*/skin.v2.css` 的覆盖段即可由界面原生渲染；
          本进程只保留一个隐藏的 Tk root 跑定时记账，每 2 秒检测账本并回写 CSS。
          `window.cssSlogan=false` 才恢复旧的悬浮窗形态（owned window，
          `globalTopmost` / `onlyOnWorkBuddy` 等开关也只对它生效）。
形态: 默认 `window.titleBarDock=true`，渲染成低调纯文字条
      （无卡片/图标/边框，一行居中字「积分：2276.71 今日已用：0」）。
      `window.dockPosition="bottom"`（默认）时是 260×28，贴在输入框下方
      「内容由 AI 生成，请核实重要信息」那条提示带的位置：浅灰渐变底 + 灰字，
      完全盖住原提示文字，视觉上像自带的系统提示；锚在窗口底边上方、聊天区水平中心。
      `dockPosition="title"` 则回到 210×30 标题栏形态（与标题栏同底 #E3ECE4）。
      `titleBarDock=false` 恢复 188×74 卡片，位置用 window.x/y（null = 屏幕右下角）。
      右键菜单可随时切换；两种纯文字条都跟随宿主窗口移动 / 最小化。
数据: ../config/credits.json（可用 WB_WHALE_CONFIG 覆盖）
自动记账: 每 10 秒调用 credits.sync() 增量扫描 ~/.workbuddy/logs 的 credit= 流水，
          按 requestId/messageId/callId 去重，只扣校准时间之后的新增消耗。
纯文字条形态默认只显示一行「积分：X 今日已用：Y」，左键点击不切页；
window.twoPages=true（或右键「左键切换两页」开关）可恢复两页。
卡片形态仍可按左键切页、拖拽移动。右键有菜单（校准余额 / 立即同步 / 退出…）。
需要带 tkinter 的 Python；install.py 会自动探测并生成本机启动器。
"""
import os
import glob
import io
import shutil
import socket
import sys
import tkinter as tk
from datetime import datetime
from tkinter import simpledialog

import credits as ledger

IS_WINDOWS = sys.platform.startswith("win")
HOST_PROCESSES = tuple(
    name.strip().lower()
    for name in os.environ.get("WB_WHALE_HOST_PROCESSES", "workbuddy.exe").split(",")
    if name.strip()
)
WATCH_MS = 150        # 窗口归属 / 位置跟随 / 前台检查间隔（够跟手，开销很小）
HIDE_GRACE_TICKS = 5  # 5×150ms≈0.75s 宽限，躲开通知 / 输入法抢焦点

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _u = ctypes.windll.user32
    _k = ctypes.windll.kernel32
    _ENUM_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u.GetForegroundWindow.restype = wintypes.HWND
    _u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u.IsWindowVisible.argtypes = [wintypes.HWND]
    _u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u.EnumWindows.argtypes = [_ENUM_CB, wintypes.LPARAM]
    _u.IsIconic.argtypes = [wintypes.HWND]
    _u.IsWindow.argtypes = [wintypes.HWND]
    _u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u.GetParent.argtypes = [wintypes.HWND]
    _u.GetParent.restype = wintypes.HWND
    _u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
    # 64 位系统有 Ptr 版，32 位只有普通版；都用于读写 GWLP_HWNDPARENT
    _set_owner = getattr(_u, "SetWindowLongPtrW", None) or _u.SetWindowLongW
    _set_owner.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _set_owner.restype = ctypes.c_void_p
    GWLP_HWNDPARENT = -8
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
    SW_SHOWNA = 8  # 显示窗口但不激活、不抢焦点
    _k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k.OpenProcess.restype = wintypes.HANDLE
    _k.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _k.CloseHandle.argtypes = [wintypes.HANDLE]


def _exe_of_hwnd(hwnd):
    """窗口所属进程名（小写）；挂件自己的窗口返回 '__self__'。"""
    if not IS_WINDOWS or not hwnd:
        return ""
    try:
        pid = wintypes.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        if pid.value == os.getpid():
            return "__self__"
        proc = _k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not proc:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if not _k.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
                return ""
            return buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            _k.CloseHandle(proc)
    except Exception:
        return ""


def foreground_exe():
    """前台窗口进程名；非 Windows / 查询失败返回 ''，调用方按「保持显示」处理。"""
    if not IS_WINDOWS:
        return ""
    try:
        return _exe_of_hwnd(_u.GetForegroundWindow())
    except Exception:
        return ""


def host_window():
    """WorkBuddy 主窗口 (hwnd, rect)；最小化 / 矩形异常时返回 (0, None)。

    窗口最小化后 GetWindowRect 会给出 -32000 一带的坐标，拿它定位会把挂件
    顶到屏幕左上角，所以这类窗口直接忽略，挂件保持原位不动。
    """
    if not IS_WINDOWS:
        return 0, None
    try:
        found = []

        def cb(hwnd, lp):
            if (_u.IsWindowVisible(hwnd) and not _u.IsIconic(hwnd)
                    and _exe_of_hwnd(hwnd) in HOST_PROCESSES):
                r = wintypes.RECT()
                _u.GetWindowRect(hwnd, ctypes.byref(r))
                rect = (r.left, r.top, r.right, r.bottom)
                if (rect[0] > -10000 and rect[1] > -10000
                        and rect[2] > rect[0] and rect[3] > rect[1]):
                    area = (rect[2] - rect[0]) * (rect[3] - rect[1])
                    # 面积阈值：主窗口几百万像素，更新提示等小弹窗不能当宿主，
                    # 否则挂件会被定位到弹窗所在的奇怪位置。
                    if area >= 100000:
                        found.append((area, hwnd, rect))
            return True

        _u.EnumWindows(_ENUM_CB(cb), 0)
        if not found:
            return 0, None
        _area, hwnd, rect = max(found)
        return hwnd, rect
    except Exception:
        return 0, None


def host_window_rect():
    """只要矩形：WorkBuddy 主窗口 (left, top, right, bottom)；找不到返回 None。"""
    return host_window()[1]


def self_window_hwnd():
    """挂件自己的顶层 HWND（按进程 PID + 标题找，避开右键菜单等临时窗）。

    不能要求窗口可见：宿主最小化时 Windows 会把它隐藏，那时反而必须能找到它
    才能重新绑定 owner / 显示回来。
    """
    if not IS_WINDOWS:
        return 0
    try:
        found = []

        def cb(hwnd, lp):
            if _exe_of_hwnd(hwnd) == "__self__":
                title = ctypes.create_unicode_buffer(128)
                _u.GetWindowTextW(hwnd, title, 128)
                found.append((title.value, hwnd))
            return True

        _u.EnumWindows(_ENUM_CB(cb), 0)
        for title, hwnd in found:
            if title == "小鲸鱼积分挂件":
                return hwnd
        return found[0][1] if found else 0
    except Exception:
        return 0


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = ledger.CONFIG

BASE_W, BASE_H = 236, 92    # 卡片设计稿尺寸，render() 里的坐标都按它写
SCALE = 0.8                  # 卡片缩放；想再小 / 再大改这个数，重启挂件即生效
CARD_W, CARD_H = round(BASE_W * SCALE), round(BASE_H * SCALE)
TITLE_W, TITLE_H = 210, 30  # 贴 WorkBuddy 标题栏的纯文字条尺寸
TITLE_BG = "#E3ECE4"         # 与 WorkBuddy 标题栏同底色 → 无卡片感、融进背景
TITLE_FG = "#8C918F"         # 低调灰字，对齐界面里「内容由 AI 生成…」那条提示
TITLE_BAR_H = 42            # WorkBuddy 自定义标题栏的逻辑像素高度
TITLE_ANCHOR = 0.5          # 纯文字条中心锚在 WorkBuddy 窗口的水平正中
TITLE_DY = 5                # 额外下移，使文字重心与标题栏左侧菜单文字上下对齐（实测差 ~4.6px）

# 底部提示条形态：对 WorkBuddy 输入框下方「内容由 AI 生成，请核实重要信息」
# 那一行做过像素测量（物理 1920×1080 / DPI 1.25）：
#   文字 bbox (1015,996)-(1281,1013)，中心 (1148,1005) → 逻辑 (918,804)
#   宿主窗口逻辑矩形 (-7,-7)-(1543,831) → 锚点 0.597、距底边 27px
#   用户提供的原生提示带截图宽 313 物理 px（约 250 逻辑 px），而 230 逻辑 px 的覆盖层
#   只有 287 物理 px，窗口尺寸一变旧字就会从两侧漏出；故取 260 逻辑 px（325 物理 px）。
BOTTOM_W, BOTTOM_H = 260, 28
BOTTOM_ANCHOR = 0.597       # 聊天区水平中心相对窗口宽度（不是整个窗口正中）
BOTTOM_CENTER_DY = 27       # 文字中心距窗口底边
BOTTOM_FG = "#929391"       # 与提示文字同色灰
# 高度取 28 而非 30：上沿刚好落在输入框下边缘细线之下，不把那条线压断
BOTTOM_GRADIENT_TOP = (239, 241, 238)     # 细线下方的提示带起始色
BOTTOM_GRADIENT_BOTTOM = (245, 247, 244)  # 提示带底部背景

# 文案：用户指定「积分：2276.71 今日已用：0」——全角冒号、小数分隔符用点号。
# 数值仍由账本动态生成；想改回逗号只需把这里改成 ","。
TEXT_DECIMAL_SEP = "."
LOCK_PORT = int(os.environ.get("WB_WHALE_PORT", "48765"))
SYNC_EVERY_TICKS = 5  # 每 2 秒一次 tick → 每 10 秒同步一次
PAGE_COUNT = 2  # 剩余积分 / 今日已用；标题栏形态默认只用第 0 页（见 window.twoPages）

FONT_CANDIDATES = (
    "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
    "PingFang SC", "Hiragino Sans GB",
    "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
    "WenQuanYi Micro Hei", "SimHei", "DengXian",
)


def pick_font(root):
    """按当前系统实际安装的字体挑一个能显示中文的，避免非 Windows 机器字体缺失。"""
    try:
        from tkinter import font as tkfont
        families = set(tkfont.families(root))
        for name in FONT_CANDIDATES:
            if name in families:
                return name
    except Exception:
        pass
    return "TkDefaultFont"


def load_config():
    try:
        return ledger.load()
    except Exception:
        return {}


def save_config(data):
    ledger.save(data)


def fmt(value):
    if value is None:
        return "尚未设置"
    try:
        number = float(value)
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_plain(value):
    """不带千分位分隔符的数字，专供纯文字条使用（小数分隔符由 TEXT_DECIMAL_SEP 决定）。"""
    if value is None:
        return "尚未设置"
    try:
        number = float(value)
        return f"{number:.0f}" if number.is_integer() else f"{number:.2f}"
    except (TypeError, ValueError):
        return str(value)


# ---------- 原生 CSS 文案（不新建窗口） ----------
# WorkBuddy 首页底部那行提示是主题 CSS 里 .wb-home-route::after 的 content，
# 不是控件也不是窗口。直接改写主题样式即可让界面本身显示积分，零悬浮窗。
SLOGAN_BEGIN = "/* WB-CREDITS-SLOGAN:BEGIN (auto-generated, do not edit by hand) */"
SLOGAN_END = "/* WB-CREDITS-SLOGAN:END */"
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


def slogan_text(cfg):
    """与桌面窗口同一套文案：全角冒号、小数分隔符用逗号。"""
    def num(v):
        return fmt_plain(v).replace(".", TEXT_DECIMAL_SEP)
    return f"积分：{num(cfg.get('balance'))} 今日已用：{num(cfg.get('todayUsage', 0))}"


def slogan_css_block(text):
    # 双保险：① 定义官方预留变量 --wb-home-slogan，两条默认规则会自动取它；
    # ② 再用同特异性选择器直接覆盖 content。颜色沿用原生 --wb-color-text-disabled。
    safe = text.replace("\\", "\\\\").replace("'", "\\'")
    return (SLOGAN_BEGIN + "\n"
            + ":root {\n  --wb-home-slogan: '" + safe + "';\n}\n"
            + ",\n".join(SLOGAN_SELECTORS) + " {\n"
            + f"  content: '{safe}';\n"
            + "}\n" + SLOGAN_END)


def css_slogan_files():
    """当前已下载的主题样式；WorkBuddy 会重新下发 zip，故要持续回写。"""
    base = os.path.join(os.path.expanduser("~"), ".workbuddy",
                        "appearance-resources")
    return sorted(set(glob.glob(os.path.join(base, "*", "skin.v2.css"))
                      + glob.glob(os.path.join(base, "*", "*", "skin.css"))))


def update_css_slogan(cfg):
    """把最新积分文案写进主题 CSS 的标记段；仅内容变化时才落盘。"""
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


def stamp():
    return datetime.now().isoformat(timespec="seconds")


def rounded(canvas, x1, y1, x2, y2, r, **kwargs):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class WhaleWidget:
    def __init__(self):
        # 单实例：占用本地端口，第二个实例直接退出
        self._lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._lock.bind(("127.0.0.1", LOCK_PORT))
            self._lock.listen(1)
        except OSError:
            sys.exit(0)

        self.cfg = load_config()
        self.state = 0
        self.ticks = 0
        self.visible = None   # None=尚未判定；由 check_host / set_visible 统一接管
        self.miss = 0
        win = self.cfg.get("window") or {}
        self.title_mode = bool(win.get("titleBarDock", True)) and IS_WINDOWS
        self.two_pages = bool(win.get("twoPages", False))  # 标题栏默认单页，左键不切
        self.global_topmost = bool(win.get("globalTopmost", False))
        # 默认不建窗口：直接改 WorkBuddy 主题 CSS 里的首页文案
        self.css_slogan = bool(win.get("cssSlogan", True)) and IS_WINDOWS
        # bottom = 输入框下方提示条；title = 旧的标题栏空白带
        self.dock_position = "bottom" if win.get("dockPosition", "bottom") != "title" else "title"
        self.W, self.H = self.shape_size()
        self.host_rect = None   # 上次定位用的 WorkBuddy 窗口矩形，用于跟随窗口移动
        self.self_hwnd = 0      # 挂件自身顶层 HWND，懒加载
        self.bound_hwnd = 0     # 当前 owner；0 表示未挂到任何窗口
        self.root = tk.Tk()
        self.font = pick_font(self.root)
        self.root.title("小鲸鱼积分挂件")
        self.root.overrideredirect(True)
        if self.css_slogan:
            self.root.withdraw()   # CSS 模式：不新建任何可见窗口
        self.apply_alpha()
        self.root.resizable(False, False)

        self.apply_geometry()

        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H, bg="#FFFFFF",
                                highlightthickness=0)
        self.canvas.pack()

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_menu)
        self.canvas.bind("<Button-2>", self.on_menu)            # macOS 右键
        self.canvas.bind("<Control-Button-1>", self.on_menu)   # macOS Ctrl+左键

        try:
            self.last_mtime = os.path.getmtime(CONFIG)
        except OSError:
            self.last_mtime = 0
        self.moved = False
        self.render()
        self.root.update_idletasks()
        self.check_host()   # 启动瞬间就按当前前台决定，避免先闪一下再消失
        if not self.css_slogan:
            _hwnd, _rect = host_window()
            self.bind_owner(_hwnd)  # 尽快挂到 WorkBuddy 窗口名下，不浮在别的程序上

    # ---------- 绘制 ----------
    def render(self):
        if self.title_mode:
            return self.render_title()
        c = self.canvas
        c.delete("all")
        self.set_bg("#FFFFFF")

        def fs(size):
            # canvas.scale 只缩放坐标、不缩放字号，字体得单独按比例缩小
            return max(8, round(size * SCALE))

        rounded(c, 1, 1, BASE_W - 2, BASE_H - 2, 16, fill="#FFFFFF",
                outline="#D5E9E8", width=1.5)

        # 小鲸鱼（尾巴在身体后面画）
        c.create_polygon([64, 50, 86, 32, 82, 68], fill="#2E8E8B", outline="")
        c.create_oval(12, 28, 72, 70, fill="#3AA8A0", outline="#2E8E8B")
        c.create_oval(24, 50, 66, 70, fill="#8FD9D2", outline="")
        c.create_oval(28, 40, 33, 45, fill="#16333A", outline="")
        c.create_arc(32, 10, 54, 30, start=20, extent=140,
                     style="arc", outline="#7FCFD0", width=2)

        balance = self.cfg.get("balance")
        today = self.cfg.get("todayUsage", 0)
        led = self.cfg.get("ledger") or {}
        auto = led.get("autoSync", True)

        if self.state == 0:
            title = "剩余积分"
            main = fmt(balance)
            sub = f"今日 {fmt(today)} · {'自动记账' if auto else '手动'}"
        else:
            title = "今日已用"
            main = fmt(today)
            count = len(led.get("events") or [])
            sub = f"{count} 笔 · 最近 -{fmt(led.get('lastCost') or 0)}"

        c.create_text(100, 16, text=title, anchor="nw",
                      font=(self.font, fs(11)), fill="#6A8A90")
        main_item = c.create_text(100, 35, text=main, anchor="nw",
                                  font=(self.font, fs(25), "bold"), fill="#16333A")
        c.create_text(100, 72, text=sub, anchor="nw",
                      font=(self.font, fs(9)), fill="#7C959A")
        c.scale("all", 0, 0, SCALE, SCALE)

        # 位数变多时自动缩字号，避免数字顶出右边缘（按缩放后的实际边界判断）
        box = c.bbox(main_item)
        limit = self.W - 8
        if box and box[2] > limit:
            ratio = (limit - box[0]) / max(1, box[2] - box[0])
            smaller = max(11, int(fs(25) * ratio))
            c.itemconfigure(main_item, font=(self.font, smaller, "bold"))

    def shape_size(self):
        """纯文字条的两种停靠位尺寸不同：底部条略宽，才能盖满原提示文字。"""
        if not self.title_mode:
            return CARD_W, CARD_H
        return (BOTTOM_W, BOTTOM_H) if self.dock_position == "bottom" else (TITLE_W, TITLE_H)

    def apply_alpha(self):
        """底部模式必须完全不透明，否则被盖住的原提示文字会透出鬼影。"""
        raw = (self.cfg.get("window") or {}).get("alpha", 0.95)
        alpha = 1.0 if (self.title_mode and self.dock_position == "bottom") else float(raw or 0.95)
        try:
            self.root.attributes("-alpha", max(0.1, min(1.0, alpha)))
        except (TypeError, ValueError, tk.TclError):
            pass

    def set_bg(self, color):
        """窗口与画布同色，标题栏形态下才能让边界完全消失。"""
        try:
            self.root.configure(bg=color)
            self.canvas.configure(bg=color)
        except tk.TclError:
            pass

    def render_title(self):
        """低调纯文字条：无卡片 / 图标 / 边框，只有一行居中灰字。
        底部停靠时铺一层与提示带一致的浅灰渐变，完整盖住原提示文字。"""
        c = self.canvas
        c.delete("all")
        bottom = self.dock_position == "bottom"
        if bottom:
            lo, hi = BOTTOM_GRADIENT_TOP, BOTTOM_GRADIENT_BOTTOM
            self.set_bg("#%02X%02X%02X" % hi)
            for i in range(self.H):
                t = i / max(1, self.H - 1)
                col = "#%02X%02X%02X" % tuple(
                    round(a + (b - a) * t) for a, b in zip(lo, hi))
                c.create_rectangle(0, i, self.W, i + 1, fill=col, outline="")
            fg = BOTTOM_FG
        else:
            self.set_bg(TITLE_BG)
            fg = TITLE_FG

        balance = self.cfg.get("balance")
        today = self.cfg.get("todayUsage", 0)
        led = self.cfg.get("ledger") or {}

        def num(v):
            # 无千分位；小数分隔符按用户指定的写法
            return fmt_plain(v).replace(".", TEXT_DECIMAL_SEP)

        # 默认单页：无论 state 被谁改过，都只显示这一行
        if not self.two_pages:
            self.state = 0
        if self.state == 0:
            text = f"积分：{num(balance)} 今日已用：{num(today)}"
        else:
            count = len(led.get("events") or [])
            text = (f"今日已用：{num(today)} · {count} 笔 · "
                    f"剩余：{num(balance)}")

        item = c.create_text(self.W // 2, self.H // 2, text=text, anchor="center",
                             font=(self.font, 11), fill=fg)
        # 数字位数变多时自动缩字号，避免顶出两侧边缘
        box = c.bbox(item)
        if box and (box[2] - box[0]) > self.W - 12:
            ratio = (self.W - 12) / max(1, box[2] - box[0])
            c.itemconfigure(item, font=(self.font, max(8, int(11 * ratio))))

    # ---------- 交互 ----------
    def on_press(self, event):
        # 必须用屏幕绝对坐标：event.x/y 是相对窗口的，窗口跟着鼠标移动后
        # 相对坐标几乎不变，位移会归零，导致窗口弹回原地（拖不动）。
        self.px, self.py = event.x_root, event.y_root
        self.wx, self.wy = self.root.winfo_x(), self.root.winfo_y()
        self.moved = False

    def on_drag(self, event):
        if self.title_mode:
            return  # 迷你条固定贴标题栏并跟随宿主窗口，不自由拖拽
        dx, dy = event.x_root - self.px, event.y_root - self.py
        if abs(dx) + abs(dy) > 4:
            self.moved = True
        if self.moved:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            nx = max(0, min(self.wx + dx, sw - self.W))
            ny = max(0, min(self.wy + dy, sh - self.H))
            self.root.geometry(f"+{nx}+{ny}")

    def on_release(self, event):
        if self.moved:
            # 只在拖拽结束这一刻落盘；退出时不再读窗口坐标，避免隐藏/漂移污染配置
            self.save_position(self.root.winfo_x(), self.root.winfo_y())
        elif self.title_mode and not self.two_pages:
            return  # 标题栏默认常驻单行，左键点击不做任何切换
        else:
            self.state = (self.state + 1) % PAGE_COUNT
            self.render()

    def on_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0, font=(self.font, 10))
        menu.add_command(label="校准余额…", command=self.set_balance)
        menu.add_command(label="立即同步消耗", command=self.sync_now)
        menu.add_command(label="今日已用清零", command=self.reset_today)
        menu.add_command(label="恢复卡片 → 右下角", command=self.dock)
        menu.add_command(label="立即刷新", command=self.refresh)
        menu.add_separator()
        win = self.cfg.get("window") or {}
        self.var_only_host = tk.BooleanVar(value=bool(win.get("onlyOnWorkBuddy", False)))
        menu.add_checkbutton(label="仅 WorkBuddy 前台显示",
                             variable=self.var_only_host,
                             command=self.toggle_only_host)
        self.var_global_topmost = tk.BooleanVar(value=self.global_topmost)
        menu.add_checkbutton(label="浮在所有窗口之上（全局置顶）",
                             variable=self.var_global_topmost,
                             command=self.toggle_global_topmost)
        self.var_title = tk.BooleanVar(value=self.title_mode)
        menu.add_checkbutton(label="贴在标题栏（迷你条）",
                             variable=self.var_title,
                             command=self.toggle_title_mode)
        self.var_bottom = tk.BooleanVar(value=self.dock_position == "bottom")
        menu.add_checkbutton(label="贴在底部提示条（关=回标题栏）",
                             variable=self.var_bottom,
                             command=self.toggle_dock_position)
        self.var_two_pages = tk.BooleanVar(value=self.two_pages)
        menu.add_checkbutton(label="左键切换两页（笔数 / 剩余）",
                             variable=self.var_two_pages,
                             command=self.toggle_two_pages)
        menu.add_separator()
        menu.add_command(label="退出挂件", command=self.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def write(self, mutate):
        cfg = load_config()
        mutate(cfg)
        save_config(cfg)
        self.cfg = cfg
        try:
            self.last_mtime = os.path.getmtime(CONFIG)
        except OSError:
            pass
        self.render()

    def set_balance(self):
        """用官方数字校准：之后的新消耗才会被扣减。"""
        value = simpledialog.askfloat(
            "小鲸鱼 · 校准余额", "官方剩余积分：",
            initialvalue=self.cfg.get("balance"), parent=self.root)
        if value is None:
            return
        self.state = 0
        now = stamp()

        def mutate(c):
            c["balance"] = value
            c.setdefault("ledger", {})["baselineAt"] = now
            c["updatedAt"] = now

        self.write(mutate)

    def sync_now(self):
        self.auto_sync()
        self.render()

    # ---------- 仅在宿主窗口前台时显示 ----------
    def apply_geometry(self, host=None):
        """按当前形态重设尺寸与位置。

        Windows 下 withdraw/deiconify 会让无边框窗口漂移，所以每次重新显示都要重设；
        标题栏模式按 WorkBuddy 窗口矩形定位，窗口移动 / 缩放时跟着走。
        """
        win = self.cfg.get("window") or {}
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if self.title_mode:
            r = host if host is not None else host_window_rect()
            if r:
                self.host_rect = r
                left, top, right, bottom = r
                if self.dock_position == "bottom":
                    # 输入框下方提示带：聊天区水平中心 + 窗口底边上方固定距离
                    x = left + round((right - left) * BOTTOM_ANCHOR) - self.W // 2
                    y = bottom - BOTTOM_CENTER_DY - self.H // 2
                else:
                    x = left + round((right - left) * TITLE_ANCHOR) - self.W // 2
                    y = top + max(0, (TITLE_BAR_H - self.H) // 2) + TITLE_DY
            elif self.host_rect is not None:
                # 宿主最小化 / 暂时找不到：保持上次位置，别跳到右下角或左上角
                return
            else:
                x, y = sw - self.W - 12, sh - self.H - 56
        else:
            self.host_rect = None
            # x / y 为 null 表示贴屏幕右下角，按当前分辨率自适应
            x = win.get("x") if isinstance(win.get("x"), int) else sw - self.W - 12
            y = win.get("y") if isinstance(win.get("y"), int) else sh - self.H - 56
        x = max(0, min(x, sw - self.W))
        y = max(0, min(y, sh - self.H))
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def apply_topmost(self):
        """默认不全局置顶：挂件靠 owner 关系待在 WorkBuddy 之上。
        勾了「浮在所有窗口之上」才恢复 HWND_TOPMOST。"""
        if self.css_slogan:
            return
        try:
            self.root.attributes("-topmost", bool(self.global_topmost))
            if self.global_topmost:
                self.root.lift()
            elif IS_WINDOWS and self.bound_hwnd and self.self_hwnd:
                # Tk 取消 -topmost 会把窗口放到非置顶窗口的最上面，
                # 必须再插回宿主之后，否则又会压住别的程序。
                _u.SetWindowPos(self.self_hwnd, self.bound_hwnd, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    def bind_owner(self, host_hwnd):
        """把挂件设成 WorkBuddy 的 owned window：始终显示在它上面、随它最小化，
        切到其它程序时随宿主一起退到后面，不再压住别人的窗口。
        globalTopmost=true 时解除归属，恢复浮在所有窗口之上。"""
        if not IS_WINDOWS:
            return
        if self.global_topmost:
            target = 0                 # 全局置顶时主动解除归属
        elif host_hwnd:
            target = host_hwnd
        else:
            # 宿主暂时找不到（最小化 / 正在重启）：保留现有 owner 不解除，
            # 否则 Windows 隐藏 owned window 后不会再自动把它显示回来。
            return
        if target == self.bound_hwnd:
            return
        try:
            if not self.self_hwnd:
                self.self_hwnd = self_window_hwnd()
            if not self.self_hwnd:
                return
            _set_owner(self.self_hwnd, GWLP_HWNDPARENT,
                       ctypes.c_void_p(target) if target else None)
            self.bound_hwnd = target
            if target:
                # 立刻把挂件插到宿主窗口之后（即宿主之上），避免短暂浮在最上层
                _u.SetWindowPos(self.self_hwnd, target, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    def ensure_window_shown(self, rect):
        """宿主最小化时 Windows 会隐藏 owned window，但 Tk 仍以为自己可见，
        set_visible(True) 会被短路。宿主回来后显式显示，并复位坐标与 Z 序。"""
        if not IS_WINDOWS or not self.visible:
            return
        if not self.self_hwnd:
            self.self_hwnd = self_window_hwnd()
        if not self.self_hwnd or _u.IsWindowVisible(self.self_hwnd):
            return
        try:
            self.root.deiconify()
            _u.ShowWindow(self.self_hwnd, SW_SHOWNA)  # 显示但不抢焦点
            r = rect or self.host_rect
            if r:
                self.apply_geometry(r)
            self.apply_topmost()  # 内部会把窗口插回宿主之后
        except Exception:
            pass

    def hide_with_host(self):
        """宿主最小化时把挂件一起藏起来。用系统级 SW_HIDE 而不是 Tk withdraw()：
        withdraw 会让无边框窗口漂移，且 Tk 状态与系统状态容易脱节。"""
        if not IS_WINDOWS:
            return
        if not self.self_hwnd:
            self.self_hwnd = self_window_hwnd()
        try:
            if self.self_hwnd and _u.IsWindowVisible(self.self_hwnd):
                _u.ShowWindow(self.self_hwnd, 0)  # SW_HIDE，不改 self.visible
        except Exception:
            pass

    def set_visible(self, show):
        if self.css_slogan:
            return
        if show == self.visible:
            return
        self.visible = show
        try:
            if show:
                self.root.deiconify()
                self.apply_geometry()
                self.apply_topmost()
            else:
                self.root.withdraw()
        except tk.TclError:
            pass

    def check_host(self):
        if self.css_slogan:
            self.miss = 0
            self.visible = False
            return
        win = self.cfg.get("window") or {}
        if not IS_WINDOWS or not win.get("onlyOnWorkBuddy", False):
            self.miss = 0
            self.set_visible(True)
            return
        exe = foreground_exe()
        # 前台是 WorkBuddy / 挂件自己 / 查询不到（权限或非 Windows）时都保持显示
        if exe in HOST_PROCESSES or exe in ("__self__", ""):
            self.miss = 0
            self.set_visible(True)
        else:
            self.miss += 1
            if self.miss >= HIDE_GRACE_TICKS:
                self.set_visible(False)

    def watch_host(self):
        if self.css_slogan:
            self.root.after(WATCH_MS, self.watch_host)
            return
        self.check_host()
        if self.visible:
            hwnd, rect = host_window()
            self.bind_owner(hwnd)          # 宿主重启 / 换窗口后重新挂上
            if hwnd:
                self.ensure_window_shown(rect)  # 被系统隐藏过就显示回来并复位
                if self.title_mode and rect and rect != self.host_rect:
                    self.apply_geometry(rect)  # 窗口拖动 / 缩放时跟着走
            elif (IS_WINDOWS and not self.global_topmost
                  and not (self.cfg.get("window") or {}).get("onlyOnWorkBuddy", False)):
                # 宿主最小化 / 关闭：保留 owner 不解除，挂件随宿主一起隐藏
                self.hide_with_host()
        self.root.after(WATCH_MS, self.watch_host)

    def toggle_only_host(self):
        value = bool(self.var_only_host.get())

        def mutate(c):
            c["window"] = {**(c.get("window") or {}), "onlyOnWorkBuddy": value}
            c["updatedAt"] = stamp()

        self.write(mutate)
        self.miss = 0
        self.check_host()

    def toggle_global_topmost(self):
        """切回旧的「浮在所有窗口上」行为；再关掉则重新挂回 WorkBuddy 窗口。"""
        value = bool(self.var_global_topmost.get())
        self.global_topmost = value

        def mutate(c):
            c["window"] = {**(c.get("window") or {}), "globalTopmost": value}
            c["updatedAt"] = stamp()

        self.write(mutate)
        self.apply_topmost()
        _hwnd, _rect = host_window()
        self.bind_owner(_hwnd)

    def set_title_mode(self, value):
        """在「标题栏迷你条」与「普通卡片」之间切换。"""
        value = bool(value) and IS_WINDOWS
        if value == self.title_mode:
            return
        self.title_mode = value
        self.W, self.H = self.shape_size()
        self.canvas.config(width=self.W, height=self.H)
        self.apply_alpha()

        def mutate(c):
            c["window"] = {**(c.get("window") or {}), "titleBarDock": value}
            c["updatedAt"] = stamp()

        self.write(mutate)      # 内部 render() 会用新的布局
        self.apply_geometry()
        self.set_visible(True)
        self.apply_topmost()
        _hwnd, _rect = host_window()
        self.bind_owner(_hwnd)

    def toggle_title_mode(self):
        self.set_title_mode(bool(self.var_title.get()))

    def set_dock_position(self, bottom):
        """在「输入框下方提示条」与「标题栏空白带」之间切换停靠位。"""
        value = "bottom" if bottom else "title"
        if value == self.dock_position:
            return
        self.dock_position = value
        self.W, self.H = self.shape_size()
        self.canvas.config(width=self.W, height=self.H)
        self.apply_alpha()

        def mutate(c):
            c["window"] = {**(c.get("window") or {}), "dockPosition": value}
            c["updatedAt"] = stamp()

        self.write(mutate)      # 内部 render() 会铺新底 / 换字色
        self.apply_geometry()
        self.set_visible(True)
        self.apply_topmost()
        _hwnd, _rect = host_window()
        self.bind_owner(_hwnd)

    def toggle_dock_position(self):
        self.set_dock_position(bool(self.var_bottom.get()))

    def toggle_two_pages(self):
        value = bool(self.var_two_pages.get())
        self.two_pages = value
        if not value:
            self.state = 0

        def mutate(c):
            c["window"] = {**(c.get("window") or {}), "twoPages": value}
            c["updatedAt"] = stamp()

        self.write(mutate)

    # ---------- 自动记账 ----------
    def auto_sync(self):
        if not (self.cfg.get("ledger") or {}).get("autoSync", True):
            return 0
        try:
            result = ledger.sync(ledger.load())
        except Exception:
            return 0
        self.cfg = ledger.load()
        try:
            self.last_mtime = os.path.getmtime(CONFIG)
        except OSError:
            pass
        if result["count"]:
            self.render()
        return result["count"]

    # ---------- 其它动作 ----------
    def reset_today(self):
        self.write(lambda c: c.update(todayUsage=0, updatedAt=stamp()))

    def refresh(self):
        self.cfg = load_config()
        self.render()

    def dock(self):
        if self.title_mode:
            self.set_title_mode(False)   # 先回到普通卡片，才有「右下角」可言
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{sw - self.W - 12}+{sh - self.H - 56}")
        cfg = load_config()
        cfg["window"] = {**(cfg.get("window") or {}), "x": None, "y": None,
                         "titleBarDock": False}
        save_config(cfg)
        self.cfg = cfg

    def save_position(self, x, y):
        # 坐标由调用方（拖拽结束时）显式给出，不从可能已漂移的窗口反读
        cfg = load_config()
        cfg["window"] = {**(cfg.get("window") or {}), "x": x, "y": y}
        save_config(cfg)
        self.cfg = cfg

    def quit(self):
        self.root.destroy()

    # ---------- 轮询 ----------
    def tick(self):
        self.ticks += 1
        if self.ticks % SYNC_EVERY_TICKS == 0:
            self.auto_sync()
        try:
            mtime = os.path.getmtime(CONFIG)
        except OSError:
            mtime = self.last_mtime
        if mtime != self.last_mtime:
            self.last_mtime = mtime
            self.cfg = load_config()
            win = self.cfg.get("window") or {}
            self.two_pages = bool(win.get("twoPages", False))
            self.global_topmost = bool(win.get("globalTopmost", False))
            self.apply_topmost()
            want_css = bool(win.get("cssSlogan", True)) and IS_WINDOWS
            if want_css != self.css_slogan:
                self.css_slogan = want_css
                if want_css:
                    try:
                        self.root.withdraw()
                    except tk.TclError:
                        pass
                    self.visible = False
                else:
                    self.visible = None
                    self.check_host()
                    _hwnd, _ = host_window()
                    self.bind_owner(_hwnd)
            # 配置热更新：外部改了停靠位 / 形态也能即时切换
            changed = False
            want_dock = "bottom" if win.get("dockPosition", "bottom") != "title" else "title"
            if want_dock != self.dock_position:
                self.set_dock_position(want_dock == "bottom")
                changed = True
            want = bool(win.get("titleBarDock", True)) and IS_WINDOWS
            if want != self.title_mode:
                self.set_title_mode(want)
                changed = True
            if not changed:
                self.render()
        if self.css_slogan:
            update_css_slogan(self.cfg)
        self.root.after(2000, self.tick)

    def run(self):
        self.auto_sync()
        if self.css_slogan:
            update_css_slogan(self.cfg)
        self.root.after(2000, self.tick)
        self.root.after(WATCH_MS, self.watch_host)
        self.root.mainloop()


if __name__ == "__main__":
    WhaleWidget().run()
