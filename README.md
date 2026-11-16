# 小鲸鱼积分挂件 · WorkBuddy 版

把会话输入框下面那行「内容由 AI 生成，请核实重要信息」**原地换成积分信息**：

```
积分：1955.88  今日已用：297.72  本会话已用：101.04
```

数字随每笔消耗实时变化，后台纯守护进程记账，**不新建任何窗口、不联网、不读聊天内容**。

灵感来自 DSH 生态的 [`MeteorNOX/DeepSeek-Balance-Whale-Widget`](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)。

---

## 为什么不是普通插件

`dsh-whale-widget` 靠 `cordis.patch.yml` 往 DSH 界面注入 DOM，**没法直接装在 WorkBuddy 上**。

WorkBuddy 插件市场只有 `skill / hook / mcp-app / welcomeMode / interaction / template` 六类，
**没有客户端 UI 挂件注入点**。但那行提示在会话里是真实 DOM：

```html
<div class="conversation-input-area__disclaimer">
  <div class="wb-homepage-disclaimer"><span>内容由 AI 生成，请核实重要信息</span></div>
</div>
```

所以本项目走的是「**用户级 Skill + 本地 CDP 注入 + 后台记账**」：

| 环节 | 做法 |
| --- | --- |
| 注入 | WorkBuddy 以 `--remote-debugging-port=9223` 启动，`scripts/cdp_inject.js` 每 2 秒扫 `/json/list`，用 `Runtime.evaluate` 改写那行 |
| 记账 | `scripts/css_daemon.py` 盯 `~/.workbuddy/logs` 里的积分消耗行，按 `requestId/messageId` 去重后逐笔扣减 |
| 守护 | 注入器退出后 2 秒内自动重拉；两者都只绑 `127.0.0.1` |

官方那句提示用 `display:none` **收起而不是删除节点**，避免干扰 React 渲染。

---

## 安装

**Windows**：解压 → 双击 `install.bat` → 完全退出 WorkBuddy（含托盘）→ 重新打开。

**macOS / Linux**：`bash install.sh`（`.command` 可双击）。

安装器会自动：

1. 找到 WorkBuddy 自带的 Python / Node，不用另装环境；
2. 把技能装到 `~/.workbuddy/skills/wb-whale-credits`；
3. 给桌面、开始菜单、任务栏的图标补上调试端口参数；
4. 设置开机自启并立刻启动守护进程。

**验证是否生效**：任务管理器 → 详细信息 → `WorkBuddy.exe` 的「命令行」里应有 `--remote-debugging-port=9223`。

### 校准余额（只需一次）

WorkBuddy 左下角头像 →「套餐与积分」，记住官方余额，然后：

```bash
python scripts/credits.py set --balance 1955.88
```

之后每笔消耗自动扣减。想重新对账：

```bash
python scripts/credits.py sync                    # 立即同步一次
python scripts/credits.py rebuild-sessions --days 7   # 按日志重建会话明细
python scripts/credits.py ledger                  # 看最近流水
```

---

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `python install.py --check` | 体检：端口、进程、技能目录、账本 |
| `python install.py --cdp-port 9224` | 换调试端口（9223 被占用时） |
| `python scripts/credits.py set --balance N` | 校准余额 |
| `python scripts/package.py` | 打包成可分发的 zip |

---

## 可调项

- **文字深浅**：`cdp_inject.js` 里 `--wb-credits-text-color` 兜底值 `#111`；
  或在页面上设 `--wb-credits-text-color:#333`。
- **小数分隔符**：`css_daemon.py` 顶部 `TEXT_DECIMAL_SEP`（默认 `.`）。
- **改完生效**：杀掉 node 注入器，守护进程 2 秒内自动重拉新代码。

---

## 目录

```
wb-whale-credits/
├── SKILL.md                 技能说明与完整实现细节
├── install.py               一键安装器（探测环境 / 装技能 / 补快捷方式 / 开机自启）
├── install.bat / .sh / .command
├── restart_with_cdp.bat     带调试端口重启 WorkBuddy
├── config/
│   └── credits.template.json  账本模板
└── scripts/
    ├── cdp_inject.js        核心注入器（CDP 改写那行提示）
    ├── css_daemon.py        守护进程 / 2 秒 tick / 拉活注入器
    ├── credits.py           本地记账（解析日志、去重、会话明细、保留上限）
    ├── package.py           打包
    ├── fix_shortcut.py      修复快捷方式参数
    └── whale_widget.py      历史 tkinter 悬浮窗实现（默认不启动）
```

运行时产物（`config/credits.json`、`config/runtime.json`、`config/inject.log`、`start_whale.*`）
由安装器生成，已在 `.gitignore` 中排除。

---

## 已知限制

- **依赖本机调试端口**：WorkBuddy 未带 `--remote-debugging-port` 启动时，界面不会有任何变化。
- **数字有 1~2 秒延迟**：日志写入到解析之间有 tick 间隔，属正常。
- **只在 WorkBuddy 客户端内生效**：网页版无权访问本地日志。
- **余额需手动校准一次**：客户端本地日志只有「消耗」，没有「充值」，充值后需重新 `set --balance`。

---

## 隐私

不联网、不上传任何数据。只读取 `~/.workbuddy/logs` 中形如
`[AcpUsagePublisher] ... credit=X` 的消耗行，账本存本地 JSON。
仓库里**不含**任何使用记录。

## License

MIT
