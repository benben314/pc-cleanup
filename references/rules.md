# 清理规则与判定依据

## 一、为什么不能用"上次访问时间"

Windows NTFS 的 last access time 默认不更新。这台机器：

```
fsutil behavior query disablelastaccess
→ DisableLastAccess = 2  (由系统管理)
```

含义是访问时间基本不写盘。按"一年没访问就删"的逻辑，会把天天打开但没修改的照片、文档、代码判成垃圾。**只用修改时间（mtime）+ 目录归属 + 应用是否还装着**三样一起判断。

## 二、三级风险模型

### L1 可自动清（删了会自动重建，最坏情况是应用重开一次变慢）

| 位置 | 说明 |
|------|------|
| `%LOCALAPPDATA%\Temp` | 只清 7 天前的；占用中的跳过 |
| `C:\Windows\Temp` | 同上 |
| `%LOCALAPPDATA%\Microsoft\Windows\INetCache` | 系统网络缓存 |
| `%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache_*.db` | 缩略图库，只删这一种文件，不动同目录其他内容 |
| `%LOCALAPPDATA%\CrashDumps` | 崩溃转储 |
| `%LOCALAPPDATA%\D3DSCache` | DirectX 着色器缓存 |
| `%LOCALAPPDATA%\NVIDIA\DXCache` | NVIDIA 着色器缓存，常见几个 G |
| `...\Google\Chrome\User Data\<profile>\{Cache,Code Cache,GPUCache,Service Worker\CacheStorage}` | 浏览器缓存，不影响 cookie 和登录态 |
| `...\Microsoft\Edge\User Data\<profile>\{Cache,Code Cache,GPUCache}` | 同上 |
| `%APPDATA%\npm-cache`、`%LOCALAPPDATA%\pip\Cache`、`%LOCALAPPDATA%\Yarn\Cache` | 开发缓存，删了下次装包重新下 |

### L2 需确认

| 位置 | 风险 |
|------|------|
| `C:\$Recycle.Bin`、`D:\$RECYCLE.BIN` | 清空后不可恢复，先让用户自己翻一遍 |
| `C:\Windows\SoftwareDistribution\Download` | 需管理员；删了不影响已装更新 |
| `C:\Windows.old` | 系统升级残留，通常几十 G，删了不能回滚系统 |
| 下载目录里 30 天以上、20MB 以上的 exe/msi/zip/iso | 多半是装完没删的安装包 |
| 疑似孤儿目录 | 见第四节 |

### L3 永不自动

`C:\hiberfil.sys`（休眠文件，关掉会失去快速启动）、`C:\pagefile.sys`（页面文件）、系统目录、用户文档、代码仓库、`.git`、`node_modules`、`venv`、模型权重、游戏目录。

## 三、看着像缓存、其实是用户数据的黑名单

这些目录名字里有 cache/temp/data，但内容是用户资产，一律不碰：

- `Documents\xwechat_files\*\msg\attach`、`Documents\WeChat Files\*\FileStorage`（微信聊天图片、文件、语音）
- `Documents\Tencent Files\*\Image`、`\Video`、`\FileRecv`（QQ）
- 浏览器 `User Data\Default\{Local Storage, IndexedDB, Login Data, Cookies}`（登录态和站点数据）
- 任何 `AppData` 下带 `User Data`、`Profiles`、`Save`、`Saves`、`存档` 的目录

聊天媒体不直接删，走 `media` 通道搬到 `D:\Cleanup\media\`，用户过目后再整体删。搬走后微信里对应图片会显示"已过期"，这点必须提前告知。

## 四、孤儿目录判定（最容易被误杀的一环）

判定为"疑似孤儿"需要同时满足：

1. 位于 `%LOCALAPPDATA%`、`%APPDATA%`、`Program Files`、`Program Files (x86)`、`ProgramData` 之一
2. 名字规范化后（去符号、转小写、保留中文）长度 ≥ 3
3. 在以下四个来源里都匹配不到：
   - 注册表 `Uninstall` 的 DisplayName 与 InstallLocation
   - 开始菜单 `.lnk` 文件名
   - **正在运行的进程名**（`tasklist`）
   - 内置别名表 `ALIASES`（钉钉/DingTalk/tdapp、微信/WeChat、迅雷/Thunder 这类中英对不上的）
4. 目录里浅层（3 层内）没有 `.exe` / `.dll`——有就是绿色软件，不能算残留
5. 体积 ≥ 50MB，且最新修改时间早于 180 天

不满足任一条就不报。实测这台机器上，一开始把 `DingTalk_108`（345MB）和 `tdappdesktop`（146MB）误判成孤儿，原因是钉钉显示名是中文、目录名是英文，加入进程名匹配和别名表后才正确排除。**这类误判是常态，所以孤儿目录永远走 `confirm`，不自动删。**

## 五、执行时的保护

- 拒绝盘符根目录（`C:\`、`D:\`）
- 拒绝命中禁改清单的路径（`windows\system32`、`winsxs`、`.git`、`node_modules`、`venv`、模型目录、工作区）
- 回收站走 `Clear-RecycleBin -DriveLetter X`，不直接删目录
- 每条操作写 JSONL 日志，含原路径、体积、动作，可追溯

## 六、经验值

- 全盘 `du` 扫描极慢（D 盘 453G 用了 11 分钟），`--deep` 只在需要看顶层占用时用
- 常规扫描（缓存 + 孤儿）在 SSD 上 1~7 秒
- 缓存类通常能回收 5~8G（浏览器 + 着色器 + 包管理器缓存是大头）
- 回收站往往比所有缓存加起来还大，先问用户再清
