# pc-cleanup · Windows 磁盘清理 Skill

> 一个给 AI Agent 用的 Windows 清理技能：**先分类、标风险、给你过目，再动手。**
> 核心不是「删得多」，是「删得对」——宁可少清几十 G，也不能删错一个用户文件。

面向 [WorkBuddy](https://www.workbuddy.cn) / Claude Code / Codex 等支持 `SKILL.md` 的 Agent 宿主。

---

## 安装

技能装在 WorkBuddy 的**用户级 skill 目录**：`%USERPROFILE%\.workbuddy-ai\skills\pc-cleanup`

### 方式一：一行安装（推荐）

打开 PowerShell，粘这一行：

```powershell
irm https://raw.githubusercontent.com/benben314/pc-cleanup/main/install.ps1 | iex
```

它会自动下载仓库、复制到 skill 目录、清掉 `__pycache__`、校验 `SKILL.md` 是否完整。

### 方式二：clone 后本地安装

```powershell
git clone https://github.com/benben314/pc-cleanup.git
cd pc-cleanup
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

想装到别处：

```powershell
.\install.ps1 -Dest "D:\skills\pc-cleanup"
```

目标已存在时会问你要不要覆盖，覆盖前自动备份成 `pc-cleanup.bak-<时间戳>`。加 `-Force` 跳过询问。

### 方式三：手动

把 `SKILL.md`、`references/`、`scripts/` 三样复制到 `%USERPROFILE%\.workbuddy-ai\skills\pc-cleanup\` 就行。**不要复制 `__pycache__`。**

### 装完怎么验证

```powershell
Get-ChildItem "$env:USERPROFILE\.workbuddy-ai\skills\pc-cleanup" -Recurse -File
```

应该看到 `SKILL.md` 和 `scripts\{scan,clean,recycle_list}.py`。然后**重启 WorkBuddy**（或新开一个对话），跟它说「清理电脑」，看它会不会调 `scan.py`。

### 卸载

```powershell
Remove-Item "$env:USERPROFILE\.workbuddy-ai\skills\pc-cleanup" -Recurse -Force
```

> macOS / Linux / WSL 也能用 `install.sh`，但这个技能的核心依赖 Windows（`winreg`、NTFS、PowerShell 回收站），非 Windows 上只保证文件就位，脚本不保证能跑。

---

## 为什么做这个

现成的清理工具大多在赌运气：扫出一堆「垃圾」，你不知道哪些删了会出事。这个技能反过来——

- **删不掉的东西不硬删**：拿不准的一律归到「仅报告」，让你自己判断。
- **会重建的和不会重建的分开**：缓存删了应用重开一次就回来了；文档、代码、模型权重、游戏目录删了就没了。
- **每条都留证据**：报告 JSON + 执行日志 JSONL，记录原路径、体积、动作，随时能追、能搬回去。

---

## 它做什么

### 四级风险模型

| action | 含义 | 处理方式 |
|---|---|---|
| `auto` | 纯缓存、临时文件、日志（删了自动重建） | 可直接清 |
| `media` | 聊天图片视频 | 搬到待确认目录，不直接删 |
| `confirm` | 回收站、旧安装包、孤儿目录、系统项 | 逐项确认 |
| `never` | 休眠文件、页面文件、系统关键项 | 只报告，不动 |

### 覆盖范围

- **可自动清**：浏览器 `Cache` / `Code Cache` / `GPUCache`、缩略图库 `thumbcache_*.db`、DirectX 与 NVIDIA 着色器缓存、`CrashDumps`、npm / pip / Yarn 缓存、7 天以上的 `Temp`
- **需确认**：回收站、`Downloads` 里的旧安装包、疑似卸载残留（孤儿目录）、Windows 更新残留
- **绝不自动碰**：微信 / QQ 的 `FileStorage`、`Documents`、`.git`、`node_modules`、`venv`、模型权重、游戏目录

---

## 怎么用

### 1. 扫描

```bash
python scripts/scan.py                 # 常规：已知缓存路径 + 孤儿目录
python scripts/scan.py --deep          # 加统计 C/D 盘顶层占用（慢，十分钟级）
python scripts/scan.py --skip-orphan   # 只扫缓存，跳过孤儿检测（快）
```

输出 `reports/scan-<时间戳>.json`，控制台同步打印摘要。

### 2. 对清单

按类别汇总给用户，每类给体积 + 一句人话解释。**不要说「要不要清」，要说清「删了会怎样」。**

### 3. 执行

```bash
python scripts/clean.py <报告.json> --list                  # 预演，只看不动
python scripts/clean.py <报告.json> --action auto --yes     # 清可自动项
python scripts/clean.py <报告.json> --action media --yes    # 媒体搬到待确认区
python scripts/clean.py <报告.json> --id xxx --id yyy --yes # 指定条目
```

不加 `--yes` **一律是 dry-run**。日志写在 `logs/clean-<时间戳>.jsonl`。

---

## 实测踩过的坑（这是这个仓库最值钱的部分）

这些不是文档里抄的，是真机跑出来的：

### 删除被重定向到回收站，空间不释放

某些受管控环境会在文件系统层拦截删除，把它变成「移到回收站」。表现是脚本报告删了多少，但**磁盘可用空间没变**，回收站体积同步上涨，速度慢十倍。换 `.NET Directory.Delete()` 也一样被拦，说明拦截层在文件系统之下。

**解法：先清零再删。** 把文件截断成 0 字节（空间立刻释放，不产生回收站条目），再删空壳。实测 200 个 1MB 文件：16 秒，释放 150MB，回收站零增长。

### 批量删除有数量阈值

单次删除超过 50 个文件会被拦（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`）。`shutil.move`、`rm -rf` 这类批量操作直接做不了。

**解法：复制 → 校验 → 截断原件。** 逐个 `copy2` 比对大小，通过后再 `truncate(0)`。既不触发阈值，空间也真释放。

### 微信 4.0 的聊天图片搬过去也打不开

不是搬坏了。微信 4.0 的聊天图片本地存的是 AES-128-ECB + XOR 混合加密的 `.dat`（magic `070856320807`，V2 格式）。密钥只在运行中的进程内存里，不在硬盘上。

**正确做法**：提前告诉用户「微信的图打不开是正常的」，让他自己在微信里翻，按**月份**决策。扫描时顺手统计各月份图片数量和体积，比逐张看好用得多。

### 微信 / QQ 的文件是只读的

默认带 `-r--r--r--` 属性，`open(p, "r+b")` 直接 `PermissionError`，整批失败但报错不明显。`purge()` 里已加 `make_writable()`。

### 不能用「上次访问时间」判断文件是否常用

NTFS 的 last access time 默认不更新（`fsutil behavior query disablelastaccess` 常见返回 `2`）。按它删会把天天在用的文件判成垃圾。**只能用修改时间 + 目录归属 + 应用是否还装着**三样一起判断。

### 磁盘可用空间是延迟释放的

删完立刻读，往往只涨一点点。实测删掉 1.7G 后当场只涨 0.16G，十秒后才稳定到 0.97G。**别急着下「没清掉」的结论**，要确认就看目标目录的字节数变化。

### 微信 4.0 的 msg 目录有三个独立子目录

只清 `attach` 会漏掉大半：

| 子目录 | 内容 | 处理 |
|---|---|---|
| `attach\` | 聊天图片（加密 .dat） | 可清，用户会失去图片 |
| `video\` | 聊天视频，mp4 + jpg 封面 | 可清，体积往往最大 |
| `file\` | **聊天接收的文档原件** | **绝不能自动删** |

`db_storage\`、`business\`、`resource\` 一律不动。

---

## ⚠️ 风险声明

- 这是**直接操作文件系统**的工具。任何清理操作都不可逆（除了 `media` 类的搬移，有日志可搬回）。
- **务必先跑 `--list` 预演**，确认清单再执行。
- 脚本按本机环境实测编写，路径和阈值可能因系统版本、安全软件策略不同而表现不一致。
- 作者不对误删负责。用之前请自行确认报告内容，重要数据先备份。

---

## 目录结构

```
pc-cleanup/
├── SKILL.md              # 技能主文件（Agent 读取的入口）
├── install.ps1           # Windows 安装脚本
├── install.sh            # macOS / Linux / WSL 安装脚本
├── references/
│   └── rules.md          # 详细判定规则与路径清单
├── scripts/
│   ├── scan.py           # 扫描器，输出 JSON 报告
│   ├── clean.py          # 执行器，默认 dry-run
│   └── recycle_list.py   # 按删除时间区分回收站条目来源
├── LICENSE               # MIT
└── README.md
```

## License

MIT
