---
name: wb-whale-credits
description: "WorkBuddy 版小鲸鱼积分挂件。当用户想查看还剩多少积分 / 显示积分余额 / 校准或更新积分 / 按消耗自动统计余额 / 打开或关闭积分挂件 / 把它安装到另一台电脑 / 提到 dsh-whale-widget 在 WorkBuddy 上的等价物时使用。默认不新建任何窗口：在本机调试端口下，把每个会话底部那行提示原位换成「积分：X 今日已用：Y 本会话已用：Z」（官方「内容由 AI 生成」那句会收起），后台纯守护进程自动记账并实时推送数字。自带一键安装器，会配好调试端口与开机自启。可在对话内渲染积分卡片、打包成插件 zip 分发。"
---

# 小鲸鱼积分挂件（WorkBuddy 版）

把 DSH 生态里 `dsh-whale-widget` 的体验搬到 WorkBuddy。灵感来自
`MeteorNOX/DeepSeek-Balance-Whale-Widget`（余额小鲸鱼 + 点击切换两页 + 悬浮快捷编辑 + 逐笔记账）。

## 边界（务必先读，别做无用功）

- `dsh-whale-widget` 是 **DSH Web/Electron 专用**插件，靠 `cordis.patch.yml` 往 DSH 界面注入 DOM，
  **不能直接安装到 WorkBuddy**。
- WorkBuddy 插件市场的类别只有 `skill / hook / mcp-app / welcomeMode / interaction / template`，
  **没有客户端 UI 挂件注入点**。那行提示在会话里是**真实 DOM**：
  `<div class="conversation-input-area__disclaimer"><div class="wb-homepage-disclaimer"><span>内容由 AI 生成，请核实重要信息</span></div></div>`；
  首页则是 CSS 伪元素 `.wb-home-route:not(.wb-calendar-route)::after`。
  两者都只能在页面运行时改，因此采用「用户级 Skill + CDP 注入 + 后台记账」，**不新建任何窗口**：
  - 注入走本机调试端口：WorkBuddy 以 `--remote-debugging-port=9223 --remote-allow-origins=*` 启动，
    `scripts/cdp_inject.js`（node，`CREATE_NO_WINDOW` 启动）每 2 秒扫 `/json/list`，对每个页面
    `Page.addScriptToEvaluateOnNewDocument` + `Runtime.evaluate`；
  - 注入内容：在 `.wb-homepage-disclaimer` 那段文字**左边**插入
    `<span data-wb-credits="slogan">积分：X 今日已用：Y 本会话已用：Z</span>`，
    官方那句「内容由 AI 生成，请核实重要信息」用 `[data-wb-credits="hidden"]{display:none}`
    收起（只隐藏那一句、不删节点，避免影响 React 渲染），整行只剩积分三项、统一加粗（`font-weight:600`），
    并且**覆盖掉继承来的淡灰**：父级 `color` 是 `rgba(0,0,0,.4)`，注入块用
    `.wb-homepage-disclaimer [data-wb-credits="slogan"]{color:var(--wb-credits-text-color,var(--wb-color-text-primary,#111))!important}`
    改成纯黑（浅色主题 `#000`，深色主题随变量自动变亮）。要调深浅就在 `:root` 上设
    `--wb-credits-text-color`（例如 `--wb-credits-text-color:#333`），或改 `cdp_inject.js` 里的 `#111` 兜底值，
    改完杀掉 node 注入器（守护 2 秒内自动重拉新代码）；
    数字取自 `credits.json`（`balance` / `todayUsage` / `ledger.bySession[会话id]`），变化即重推；
    当前会话 id 从会话列表选中项的 `data-conversation-id` 读取——选中态是 CSS Modules 哈希类名
    （`_selected_1ufy3_20`），所以匹配 `--active|is-active|_active_|selected` 并兜底 `aria-current`，
    首页无会话时省略「本会话已用」；
    **一旦认出会话 id 就以它为准**：`bySession` 里没有该会话就显示 `0`，
    绝不回退到「账本里唯一那个会话」的数字——旧实现这么做，导致点过任何会话都显示同一个数（串台），
    已修正；只有**完全认不出**选中项（首页/列表收起）才允许用 `latestUsage()` 兜底；
    `MutationObserver`（含 class 变化）+ 1.5 秒轮询保证换会话、重渲染后仍在；
  - 首页伪元素：有可见的 DOM 声明时清空它避免重复，否则直接用「积分：X 今日已用：Y 本会话已用：Z」；
  - 主题 CSS（`appearance-resources/*/skin.v2.css`）与 preload 仅作历史兜底，**实测都不生效**：
    preload 段从未被执行（`renderer.log` 里探针 0 次），主题 CSS 只在启动时读一次（色调探针无反应）；
  - 默认启动 `scripts/css_daemon.py`：**纯后台，不 import tkinter、不创建任何窗口对象**，
    连隐藏的窗口都没有；`whale_widget.py` 只是保留的历史窗口实现，默认不启动，
    只有手动运行它才会出现悬浮窗（owned window、`globalTopmost` 等开关也只属于它）。
- WorkBuddy **没有公开的积分余额 API**，但**本地会话日志里有逐笔消耗**。挂件据此自动扣减余额，
  得到的是「本地推算值」，不是官方实时余额。必须定期用官方数字校准（见下）。
  **绝不编造余额数字**；`balance` 为 `null` 时如实说「尚未设置」。

## 安装到另一台电脑

分发包：`wb-whale-credits-plugin-v1.1.0.zip`（由 `scripts/package.py` 生成，
不含任何余额、账本、日志或本机路径，打包时自动做隐私扫描）。

**依赖其实只有两样，而且 WorkBuddy 一般都自带**：
- Python 3.8+（标准库即可，**不需要 tkinter**，守护进程零窗口）；
- Node.js（跑页面注入器）。找不到时会依次翻 `~/.workbuddy/binaries/node/versions/*`、
  `~/.ai-manager/runtimes/node/*`、`Program Files\nodejs`、PATH。

**方式 A · 一键安装（推荐，Windows）**

1. 解压 zip。
2. 双击 `install.bat`（它会优先用 WorkBuddy 自带的 Python，找不到才找系统 Python）。
3. 装完按提示重启一次 WorkBuddy。

`install.py` 会一口气做完这些：

| 步骤 | 说明 |
|---|---|
| 探测环境 | Python（含 WorkBuddy 自带运行时）、Node、WorkBuddy 数据目录 |
| 部署技能 | 复制到 `<WorkBuddy 数据目录>/skills/wb-whale-credits`（已在那儿就原地装） |
| 生成配置 | 干净 `config/credits.json`（已有账本**不覆盖**）+ `runtime.json`（含 `cdpPort`） |
| **补快捷方式** | 给桌面 / 开始菜单 / 公共开始菜单 / 任务栏上**所有** `WorkBuddy*.lnk` 补 `--remote-debugging-port`。换机器最容易漏的一步，漏了就是「装好了但界面没反应」 |
| 开机自启 | Windows `HKCU\...\Run\WhaleCredits`（pythonw，无黑框）／macOS LaunchAgent／Linux autostart |
| 启守护 | 立即拉起后台进程，单实例锁保证不会双开 |

常用参数：

```bash
python install.py --check                  # 只体检，不写任何文件
python install.py --cdp-port 9224          # 9223 被占用时换端口
python install.py --no-shortcut            # 不动快捷方式
python install.py --no-autostart           # 不设开机自启
python install.py --no-start               # 装完不自动启动
python install.py --workbuddy-home PATH    # 数据目录不在默认位置
```

**方式 B · 手动两步**：把文件夹放进 `~/.workbuddy/skills/`，再 `python3 install.py`。
`--check` 可以先看环境够不够。

**方式 C · 插件市场源**：把 zip 作为本地市场源添加。包内 `.codebuddy-plugin/marketplace.json`
注册了 `wb-whale-credits` 插件，manifest 为 `wb-whale-credits/.codebuddy-plugin/plugin.json`
（`category: skill`），与内置 skill 插件形态一致。**加载后仍需跑一次 `install.py`**——
市场只负责放文件，调试端口、自启、启动这些必须落到本机。

**首次校准**（必做，否则显示 0）：WorkBuddy 左下角头像 →「套餐与积分」看官方余额，然后

```bash
python scripts/credits.py set --balance 2276.71
```

之后每笔消耗都从本地日志自动扣，不用再管。

**会话被算错 / 都显示同一个数时**（`bySession` 丢过或被校准清空）：

```bash
python scripts/credits.py rebuild-sessions        # 按日志重建各会话累计，不动余额
python scripts/credits.py rebuild-sessions --days 3
```

**重新打包**：`python3 scripts/package.py --out <输出路径>`。

## 目录

```
wb-whale-credits/
├── SKILL.md                         本文件
├── .codebuddy-plugin/plugin.json    插件 manifest（仅分发包内）
├── install.py / install.bat / install.command / install.sh   跨平台安装与自检
├── start_whale.bat / .command / .sh 启动器（启动 scripts/css_daemon.py，install.py 写成本机版）
├── config/
│   ├── credits.template.json       干净模板（分发包内）
│   ├── credits.json                余额 / 今日 / 账本 / 窗口位置（安装后生成，不分发）
│   ├── runtime.json                Python、Node、cdpPort 探测结果（不分发）
│   └── python_path.txt              GUI Python 路径（不分发）
└── scripts/
    ├── credits.py                  配置 CLI + 日志增量记账引擎
    ├── css_daemon.py               纯后台守护（默认启动，零窗口）
    ├── cdp_inject.js               CDP 注入器（Node，被守护进程监督）
    ├── cdp.js                      CDP 诊断小工具（list / eval / eval-file）
    ├── fix_shortcut.py             给 .lnk 二进制补调试端口参数
    ├── whale_widget.py             历史 tkinter 窗口实现（默认不启动）
    └── package.py                  打包成插件 zip
```

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `WORKBUDDY_HOME` | WorkBuddy 数据目录 | `~/.workbuddy` |
| `WB_WHALE_LOGS` | 会话日志目录 | `<WORKBUDDY_HOME>/logs` |
| `WB_WHALE_CONFIG` | 配置文件路径 | `<技能目录>/config/credits.json` |
| `WB_WHALE_PORT` | 守护进程单实例锁端口 | `48765` |
| `WB_CDP_PORT` | WorkBuddy 调试端口 | `runtime.json` 的 `cdpPort`，再退 `9223` |

所有路径都从 `__file__` 与用户主目录推导，**不写死任何用户名或盘符**；
字体按系统实际可用字体自动挑选（Windows 雅黑 / macOS 苹方 / Linux Noto / 通用 sans）。

## 自动记账怎么工作

**数据源**：`<WORKBUDDY_HOME>/logs/<日期>/<会话>.log` 里两类逐笔流水

```
[AcpUsagePublisher] [CreditUsageSource] source=... requestId=... messageId=... credit=2.59
[GenerationToolCreditUsageHook] publish credit start tool=ImageGen callId=... stageId=... credit=6.23
```

- 按 `requestId|messageId` / `callId|stageId` 去重（`modelUsage` 与 `rawUsage` 是同一笔的两条上报）；
- **必须带行首时间戳**（`^[2026/11/16 08:11:43.134]`）才算真实流水。少了这条校验，对话里被记录进日志的
  日志片段（脚本源码、贴出的日志文本）会被误当成消耗扣钱——真实流水 302 行全带时间戳，误判行 0 带，
  这条校验就是为此加的；
- 按字节偏移**增量**扫描，只扫今天和昨天的日志目录；
- 只统计 **校准基准时间之后**的新增消耗，历史消耗不会重复扣；
- 每笔记入 `ledger.events`、按日汇总到 `ledger.history`，同步扣减 `balance`、累加 `todayUsage`；
- **保留上限**（`prune_ledger()`，每次记账后自动执行）：`events` 只留最近 **2000 笔且 90 天内**，
  `seen` 去重键留 4000 条、`history` 留 90 天、`bySession` 留 120 个；稳态 `credits.json` 约 600 KB，
  不会无限膨胀。裁剪只动明细、**不影响 `balance` / `todayUsage`**（它们是累计值）。
  常量在 `credits.py` 顶部 `EVENTS_MAX` / `EVENTS_KEEP_DAYS` / `SEEN_MAX` / `HISTORY_KEEP_DAYS`；
- `seen` 被裁到 4000 条后，若某份日志被轮转重写且事件既超出该窗口、又晚于 `baselineAt`，理论上有重复扣的可能
  （极低概率）；真发生了用官方数字 `set --balance` 校准一次即可归零；
- 挂件每 10 秒自动跑一次 `credits.sync()`（无变化时完全不写盘），改配置后 2 秒内刷新显示。

**覆盖范围与漂移**（要如实告诉用户）：能覆盖本地模型调用与生成类工具消耗；
每日赠送积分由下面的「每日补给」补记；**不覆盖**套餐周期重置、活动奖励、
网页端/其它设备的消耗、服务端直接扣费，以及日志被清理前的历史。
免费模型（如 `hy4free`）不产生 credit 记录。
所以余额仍会 drift，建议用户每隔一段时间从官方渠道抄一次真实数字，
用 `set --balance` 校准（该命令会重置 `ledger.baselineAt`，之后只扣新产生的消耗）。

**补充账目**：日志没记到的消耗可用 `consume --amount N --note "..."` 手动补记一笔。

### 每日补给（官方每日赠送积分）

按用户确认的政策（**每天赠送 100 积分**），守护进程跨天首次 `sync()` 时自动给余额补 +100：

- **数据源性质**：本地日志里**没有**赠送流水（全是消耗），这笔是按用户已知政策**本地补记**，
  不是从日志或服务端读到的真值。账户真实余额以官方渠道为准，定期 `set --balance` 校准即可。
- **日期去重**：记在 `ledger.dailyGrant.grantedDays`，同一自然日最多补一次，不会因守护反复轮询重复叠加。
- **校准防叠加**：`set --balance` 会把当天标记为已补——因为官方数字本身已含当天赠送；
  如果你是在校准后改主意想补当天，用 `grant --now --force`。
- **未校准不补**：`balance` 为 null 时跳过补给（没有基准，补了没意义）。
- **补给作为负数流水**（`kind="每日补给"`，`cost=-100`）写入 `events`，可在 `ledger` 里审计。
- **开关与金额**：默认开、每次 +100；`grant --off` 关闭，`grant --amount 50` 改金额，
  `grant --on` 重开，`grant --now` 手动补今天。
- **可移植性**：这些配置都在账本 JSON 里，随 `credits.json` 走；不装这份新版脚本的机器不受影响。

### 界面「⊕100」一键开关

积分行右侧挂了一个蓝色的 **`⊕100`** 徽标，点一下就给本地账本加 100（走 `grant --now --force`，
效果等同命令行）。链路与两个已踩平的坑：

- **点击回传用页面队列，不用 `Runtime.addBinding`**：页面点击把次数写进 `window.__wbGrantQueue`，
  node 在 `tick()` 里用 `Runtime.evaluate` 读走并清零。`addBinding` 只对注册后新建的 execution
  context 生效——注入器重启时页面早就加载好，binding 打不到现存 context，点击会石沉大海。
- **徽标 onclick 每次注入都重绑**：注入脚本是热更新的，但页面里徽标 DOM 是首次注入时创建的；
  若只在「不存在时创建」分支里绑 onclick，注入器重启后旧徽标留着旧闭包，新版逻辑上不去。
  所以 `go()` 里每次都对已存在的徽标重新赋值 `onclick`。
- 1 秒内连点会被防抖合并；单次轮询读到 N 次点击则循环补 N 笔（上限 10）。
- 徽标 `title` 明写「不影响官方账户」——这个开关只改本地账本显示，碰不到服务端余额。

## 命令速查

CLI 不需要 tkinter，用任意 Python 3 运行即可（Windows 用 `py -3` / `python`）：

```bash
SK="$HOME/.workbuddy/skills/wb-whale-credits"     # Windows 用 %USERPROFILE%\.workbuddy\skills\...
python3 "$SK/scripts/credits.py" show              # 余额 / 今日 / 基准 / 最近一笔
python3 "$SK/scripts/credits.py" paths             # 显示配置与日志路径（排查安装问题）
python3 "$SK/scripts/credits.py" sync              # 从日志同步新增消耗
python3 "$SK/scripts/credits.py" sync --dry-run    # 只试算不写入
python3 "$SK/scripts/credits.py" set --balance 1234.56   # 官方数字校准（重置基准）
python3 "$SK/scripts/credits.py" consume --amount 12.5   # 手动补记一笔消耗
python3 "$SK/scripts/credits.py" grant                  # 查看每日补给状态
python3 "$SK/scripts/credits.py" grant --off            # 关闭每日补给（--on 重开）
python3 "$SK/scripts/credits.py" grant --amount 50      # 改补给金额为 50
python3 "$SK/scripts/credits.py" grant --now --force    # 手动强制补今天一次
python3 "$SK/scripts/credits.py" ledger --limit 10       # 查看逐笔流水与按日汇总
python3 "$SK/scripts/credits.py" reset-today            # 今日已用清零
python3 "$SK/install.py" --check                   # 安装环境体检
```

> **通过自动化工具启动时的坑（Windows 实测）**：直接用 `Start-Process` / `pythonw` / `subprocess`
> 起的进程会随工具会话一起被清理（Windows 作业对象，`CREATE_BREAKAWAY_FROM_JOB` 会被拒绝）。
> 可靠做法是让**已在运行的桌面 shell** 代跑：`Start-Process explorer.exe -ArgumentList "<bat 绝对路径>"`。
> 用户手动双击启动器不受此限制。验证是否真的起来：
> `Get-NetTCPConnection -LocalPort 48765 -State Listen`（端口可用 `WB_WHALE_PORT` 改）。
> 改完代码要重启守护：先按 PID 停掉旧 pythonw，再用 Explorer 启动 start_whale.bat。

（仅手动运行历史脚本 `whale_widget.py` 时才有）挂件右键菜单：校准余额 / 立即同步消耗 / 今日已用清零 / 恢复卡片 → 右下角 / 立即刷新 /
「仅 WorkBuddy 前台显示」开关 /「浮在所有窗口之上（全局置顶）」开关（默认关）/
「贴在标题栏（迷你条）」开关 /「贴在底部提示条（关=回标题栏）」开关（默认开）/
「左键切换两页（笔数 / 剩余）」开关（默认关）/ 退出。
**纯文字条形态默认单页**：左键点击不做任何切换，永远只显示「积分：X 今日已用：Y」；
勾选「左键切换两页」才恢复第二页「今日已用：Y · N 笔 · 剩余：X」。卡片形态不受此限制，
左键仍可在「剩余积分 ↔ 今日已用」两页间切换。`PAGE_COUNT` 控制页数。

## 触发后的行为

1. **问「还剩多少积分」** → 先跑 `credits.py sync` 再 `show`（拿到最新推算值），
   然后在对话中用 Visualizer 渲染一张小鲸鱼积分卡片（浅色主题：浅底深字；
   显示剩余积分、今日已用、最近一笔）。说明这是本地推算值，需要时可校准。
2. **用户报出官方余额** → 用 `credits.py set --balance <数字>` **校准**（不是简单覆盖），
   回复「已校准，之后只扣新增消耗」。校准只重置 `baselineAt`，**不清空 `bySession`**
   （它是各会话历史累计，纯展示用；清空会让所有旧会话变 0，看起来像「每个会话都一样」）。
   校准还会把当天标记为「每日补给已补」，因为官方数字已含当天赠送；
   若用户明确要求当天额外补 100，用 `grant --now --force`。
3. **用户要求「每天自动加积分 / 自动领每日赠送」** → 说明边界：**插件改不了官方账户**，
   只能按用户确认的政策在本地账本补记；若官方实际上没有每日赠送，补进去就是假数字，
   务必先确认政策再开。确认后用 `grant --on --amount 100`（默认已开），
   跨天由守护自动补；用 `grant` 查看状态、`grant --off` 关闭。
   若要「按一下加 100 的开关」，直接告诉用户积分行右侧有蓝色 **`⊕100`** 徽标，点一下即加 100
   （改的是本地账本，不碰官方账户）；徽标不见时重启注入器让它重新注入。
4. **用户问「能否按消耗自动统计」** → 按上面「自动记账怎么工作」解释数据源、去重、基准与漂移边界，
   并跑一次 `sync` 展示实际扣减结果。
5. **「打开 / 启动守护」** → Windows 用 Explorer 方式启动 `start_whale.bat`（运行 css_daemon.py），
   macOS / Linux 直接双击或 `bash start_whale.command` / `.sh`；不会弹出任何窗口。
6. **「关闭挂件 / 停止守护」** → 按 PID 停掉 `css_daemon.py` 进程（端口 48765 的拥有者），
   不要用 `taskkill /IM pythonw.exe` 批量结束（会误杀其它程序）。
7. **「把它做成一个插件 / 装到别的电脑」** → 按上面「安装到另一台电脑」说明分发包与三种安装方式，
   必要时跑 `scripts/package.py` 重新打包。
8. **用户说「本会话已用不对 / 切换会话数字不变」** → 先跑 `rebuild-sessions` 重建各会话累计，
   再确认 `cdp_inject.js` 的 `label()` 在认出会话 id 后不会回退到别的会话数字（旧版会串台）。
9. **用户问「能不能把 dsh-whale-widget 直接装到 WorkBuddy」** → 按「边界」如实解释生态差异。

## 对话内积分卡片模板（SVG，light 主题）

用 `show_widget` 渲染，viewBox 从 `0 0 680 ` 开始；所有形状必须显式填色；
示例数字必须替换成 `credits.json` 的真实值，`balance` 为 `null` 时写「尚未设置」。

```svg
<svg viewBox="0 0 680 180" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="680" height="180" rx="18" fill="#F2FAFA" stroke="#D5E9E8"/>
  <ellipse cx="86" cy="96" rx="52" ry="34" fill="#3AA8A0"/>
  <path d="M128 96 L160 68 L154 124 Z" fill="#2E8E8B"/>
  <ellipse cx="96" cy="112" rx="34" ry="16" fill="#8FD9D2"/>
  <circle cx="66" cy="86" r="4" fill="#16333A"/>
  <text x="190" y="80" font-size="15" fill="#5C7A80" font-family="sans-serif">剩余积分</text>
  <text x="190" y="132" font-size="42" font-weight="bold" fill="#16333A" font-family="sans-serif">{{balance}}</text>
  <text x="400" y="132" font-size="16" fill="#5C7A80" font-family="sans-serif">今日已用 {{today}} · 最近 -{{last}}</text>
</svg>
```

## 注意

- 默认启动的是独立后台进程 `css_daemon.py`：**没有窗口**，只跑定时记账并把文案写进主题 CSS；
  WorkBuddy 退出后进程与记账仍在（开机登录自启）。没启动守护时对话里只能看到卡片，
  自动记账与首页文案都不会更新。历史脚本 `whale_widget.py` 只有手动运行才会出现悬浮窗。
- 只读 `<WORKBUDDY_HOME>/logs` 的日志；**只写**本技能的 `config/credits.json`，
  不要改 WorkBuddy 自身的数据文件。
- 窗口位置（仅历史窗口实现会用到）保存在 `credits.json` 的 `window` 字段，拖拽后自动写回；`x=null` / `y=null` 表示贴屏幕右下角（按当前分辨率自适应）。
- **显示策略（默认零窗口）**：启动器运行 `scripts/css_daemon.py` —— 纯后台守护，
  **不 import tkinter、不创建任何窗口对象**（连隐藏的 Tk root 都没有）。占用 48765 单实例锁，
  每 10 秒 `credits.sync()` 记账、每 2 秒 `update_css_slogan()` 回写主题 CSS（兜底），
  并用 `CREATE_NO_WINDOW` 监督 `scripts/cdp_inject.js`（挂了自动重启，找不到 node 就静默跳过）。
  `window.cssSlogan` 只对历史脚本 `whale_widget.py` 有意义，默认启动链路完全不加载它。
  **前提**：WorkBuddy 必须以调试端口启动（`HKCU\...\Run` 里的 `WorkBuddy.WorkBuddy` 已带
  `--remote-debugging-port=9223 --remote-allow-origins=*`；桌面与开始菜单的快捷方式由
  `scripts/fix_shortcut.py` 直接把参数写进 `.lnk` 的 Shell Link 结构，绕开被安全策略拦的 COM）。
  用户若用没打补丁的图标启动，命令行里没有端口，注入会静默失效——先跑一次 `fix_shortcut.py` 再重启应用。
  端口没开时注入器只是空转等待，不影响记账。诊断用 `node scripts/cdp.js list|eval|eval-file`。
  下面的 owned window / 前台检测逻辑**仅属于历史窗口实现**：
  `window.globalTopmost=false`（默认）时，挂件被设成
  WorkBuddy 主窗口的 owned window（`SetWindowLongPtrW(GWLP_HWNDPARENT, host_hwnd)`）：
  始终在 WorkBuddy 之上、随它移动 / 最小化，但**不设 HWND_TOPMOST**，切到别的程序时
  随宿主一起退到后面并被自然遮住；窗口本体不 `withdraw()`，`tick` 与每 10 秒 `sync` 照常运行。
  `watch_host()` 每 150ms 重新查一次宿主窗口（WorkBuddy 重启 / 换窗口会自动重挂），
  矩形变化就重定位；窗口最小化时矩形落在 -32000 一带，直接忽略以免挂件跳到左上角
  （面积 < 100000 逻辑像素的小弹窗也不会被误当成宿主）。
  宿主最小化会让 Windows 隐藏 owned window，而 Tk 仍以为窗口可见、`set_visible(True)` 被短路，
  所以 `ensure_window_shown()` 会检测 `IsWindowVisible`，用 `deiconify` + `SW_SHOWNA`
  显示回来并复位坐标与 Z 序；宿主暂时找不到时**保留 owner 不解除**，否则窗口再也不会自动出现；
  同时 `hide_with_host()` 用系统级 `SW_HIDE` 让挂件随宿主一起隐藏（不用 `withdraw()`，避免漂移），
  宿主回来后由 `ensure_window_shown()` 再显示。
  `window.globalTopmost=true`（或右键「浮在所有窗口之上」）→ 解除 owner 并恢复全局置顶。
  `window.onlyOnWorkBuddy=true`（或右键「仅 WorkBuddy 前台显示」）→ 只有 `WorkBuddy.exe`
  （或挂件自身及弹窗）在前台才显示，连续 5 次（约 0.75 秒宽限）不是就隐藏。
  进程名可用环境变量 `WB_WHALE_HOST_PROCESSES` 覆盖（逗号分隔）；非 Windows 无法检测前台进程
  与 owner，自动退化为普通常显窗口。
- 窗口尺寸（历史脚本，默认不启动）由 `scripts/whale_widget.py` 顶部的 `SCALE` 控制（设计稿 236×92，当前 0.8 → 188×74）：`render()` 按设计稿坐标绘制后整体 `canvas.scale`，字号用 `fs()` 单独缩放。用户说「小一点 / 大一点」就调 `SCALE`，改完重启挂件生效。
- **三种形态（历史脚本 `whale_widget.py`，默认不启动）**：`window.titleBarDock=true`（默认）→ 纯文字条，由 `window.dockPosition` 决定停靠位：
  - `dockPosition="bottom"`（默认）→ **260×28 底部提示条**：`render_title()` 逐行铺
    `BOTTOM_GRADIENT_TOP (239,241,238)` → `BOTTOM_GRADIENT_BOTTOM (245,247,244)` 的浅灰渐变；
    宽度取 260 逻辑 px（325 物理 px）：用户提供的原生提示带截图宽 313 物理 px，覆盖层略宽可确保
    窗口尺寸 / 位置变化时旧提示不从两侧漏出；高度刻意取 28：上沿落在输入框下边缘那条浅绿细线
    （物理 y=959）之下，不把界面原有的线压断，
    字色 `BOTTOM_FG #929391`，`apply_alpha()` 强制不透明以完整盖住原提示文字；位置由
    `host_window()` 取矩形，配合 `BOTTOM_ANCHOR=0.597`（聊天区水平中心，实测物理中心 x≈1148）
    / `BOTTOM_CENTER_DY=27`（文字中心距窗口底边，实测物理 y≈1005）算出。
  - `dockPosition="title"` → **210×30 标题栏条**：`TITLE_BG #E3ECE4` 与标题栏同色、`TITLE_FG` 灰字，
    用 `TITLE_ANCHOR=0.5`（水平正中）/ `TITLE_BAR_H=42` / `TITLE_DY=5`（与菜单文字上下对齐，
    实测原差约 4.6 逻辑像素）。
  - 两种都用 `fmt_plain()`（无千分位分隔符）与 `TEXT_DECIMAL_SEP="."`（小数分隔符用点号，如 `2103.93`）、居中单行、位数变长自动缩字号，每 150ms 跟随窗口变化，
    `apply_geometry()` 按停靠位分支计算。`dockPosition` 可用右键「贴在底部提示条」或改配置热更新切换。
  - `titleBarDock=false` → 188×74 卡片（保留鲸鱼图案与 `fmt()` 千分位格式），用 `window.x/y`。
- 纯文字条形态下不允许自由拖拽（会与跟随冲突），**默认只显示第 0 页「积分：X 今日已用：Y」、
  左键不切页**；`window.twoPages=true`（或右键「左键切换两页」开关）才启用第 1 页
  「今日已用：Y · N 笔 · 剩余：X」。单页模式下 `render_title()` 强制 `state=0`、
  `on_release()` 直接 return；卡片形态仍用 `PAGE_COUNT=2`。
- `ledger.seen` / `ledger.events` 已由 `prune_ledger()` 控制上限（4000 / 2000 笔 + 90 天），不再无限增长；
  换机或清空日志后建议重新校准一次。
- 分发包**绝不能**包含 `config/credits.json`、`runtime.json`、`python_path.txt`（含本机余额与路径）；
  `scripts/package.py` 已自动排除并做隐私扫描。
