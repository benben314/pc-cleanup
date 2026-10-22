#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pc-cleanup 扫描器
按规则把磁盘上的可清理项分类，输出 JSON 报告 + 控制台摘要。

用法:
    python scan.py                 # 常规扫描（已知缓存路径 + 孤儿目录）
    python scan.py --deep          # 额外统计 C/D 盘顶层目录占用（慢）
    python scan.py --out X.json    # 指定报告路径
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
import winreg
from datetime import datetime

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
DEFAULT_OUT_DIR = r"D:\Cleanup\reports"
TMP_AGE_DAYS = 7          # Temp 里超过这么多天才算垃圾
INSTALLER_AGE_DAYS = 30   # 安装包超过这么多天算废弃
ORPHAN_MIN_SIZE = 50 * 1024 * 1024
ORPHAN_MIN_AGE_DAYS = 180

# 目录名与显示名对不上的常见应用，用于孤儿判定时排除误杀
ALIASES = [
    "钉钉", "DingTalk", "DingDing", "tdapp", "tdappdesktop", "Alibaba", "阿里",
    "微信", "WeChat", "Weixin", "QQ", "Tencent", "腾讯",
    "WPS", "Kingsoft", "迅雷", "Thunder", "网易", "NetEase", "百度", "Baidu",
    "小米", "Xiaomi", "MiMo", "抖音", "Douyin", "字节", "ByteDance", "飞书", "Feishu", "Lark",
    "网易云音乐", "CloudMusic", "Steam", "Epic", "WeGame", "米哈游", "miHoYo", "Adobe",
    "NVIDIA", "Intel", "AMD", "Realtek", "Microsoft", "Google", "Chrome", "Edge",
]

# 绝对不碰的东西（路径包含这些片段就跳过）
NEVER_TOUCH = [
    r"\windows\winsxs", r"\windows\system32", r"\windows\syswow64", r"\windows\assembly",
    r"\$windows.~bt", r"\program files\windowsapps",
    r"\documents\deepseek-harness", r"\新建文件夹\claw", r"\.git", r"\node_modules",
    r"\venv", r"\.venv", r"\whisper\models", r"\site-packages",
]


def expand(p):
    """展开 %VAR% 与 ~"""
    p = os.path.expanduser(p)
    p = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), p)
    return os.path.normpath(p)


def norm(s):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (s or "").lower())


def is_reparse(entry):
    try:
        return bool(entry.stat(follow_symlinks=False).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError):
        return False


def walk_stats(root, age_days=None):
    """返回 (总字节, 文件数, 最新修改时间戳, 错误数)。age_days 非空时只统计更老的文件。"""
    total = 0
    count = 0
    newest = 0
    errors = 0
    cutoff = time.time() - age_days * 86400 if age_days else None
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if is_reparse(e):
                                continue
                            stack.append(e.path)
                        else:
                            st = e.stat(follow_symlinks=False)
                            if cutoff and st.st_mtime > cutoff:
                                newest = max(newest, st.st_mtime)
                                continue
                            total += st.st_size
                            count += 1
                            newest = max(newest, st.st_mtime)
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
    return total, count, newest, errors


def in_never_touch(path):
    low = path.lower()
    return any(k in low for k in NEVER_TOUCH)


def installed_index():
    """从注册表 + 开始菜单收集已安装应用名，用于孤儿判定。"""
    names, locs = set(), set()
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as k:
                n = winreg.QueryInfoKey(k)[0]
                for i in range(n):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sk:
                            def gv(name):
                                try:
                                    return winreg.QueryValueEx(sk, name)[0] or ""
                                except OSError:
                                    return ""
                            dn, il = gv("DisplayName"), gv("InstallLocation")
                            if dn:
                                names.add(norm(dn))
                            if il:
                                locs.add(norm(os.path.basename(str(il).rstrip("\\/"))))
                    except OSError:
                        continue
        except OSError:
            continue
    for base in (os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
                 os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.lower().endswith(".lnk"):
                    names.add(norm(os.path.splitext(f)[0]))

    # 正在运行的进程名：能兜住"中文显示名 vs 英文目录名"对不上的情况（如钉钉 DingTalk）
    try:
        r = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                           text=True, encoding="mbcs", errors="ignore", timeout=30)
        for line in (r.stdout or "").splitlines():
            parts = line.split('","')
            if len(parts) >= 1:
                exe = parts[0].strip('"').strip()
                if exe.lower().endswith(".exe"):
                    names.add(norm(os.path.splitext(exe)[0]))
    except Exception:
        pass

    # 已知别名：目录名和显示名对不上的常见应用
    for alias in ALIASES:
        names.add(norm(alias))
    return names, locs


def has_exe(path, max_depth=3):
    """目录里（浅层）有没有可执行文件，用来排除绿色软件。"""
    base_depth = path.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(path):
        if root.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
        for f in files:
            if f.lower().endswith((".exe", ".dll")):
                return True
    return False


# ---------------------------------------------------------------- 规则表
# action: auto=可自动清 / confirm=需确认 / media=搬去待确认区 / never=只报告
def build_rules():
    L = os.environ.get("LOCALAPPDATA", "")
    R = os.environ.get("APPDATA", "")
    U = os.environ.get("USERPROFILE", "")
    return [
        # ---- 一级：纯缓存，删了会自动重建
        dict(name="用户临时文件", paths=[os.path.join(L, "Temp")], action="auto",
             category="临时文件", age=TMP_AGE_DAYS,
             note="只清 7 天前的，正在被占用的会跳过"),
        dict(name="系统临时文件", paths=[r"C:\Windows\Temp"], action="auto",
             category="临时文件", age=TMP_AGE_DAYS),
        dict(name="IE/系统网络缓存", paths=[os.path.join(L, r"Microsoft\Windows\INetCache")],
             action="auto", category="缓存"),
        dict(name="缩略图数据库", paths=[os.path.join(L, r"Microsoft\Windows\Explorer")],
             action="auto", category="缓存", glob="thumbcache_*.db",
             note="只删 thumbcache_*.db，不动同目录其他文件"),
        dict(name="崩溃转储", paths=[os.path.join(L, "CrashDumps")], action="auto",
             category="日志"),
        dict(name="DirectX 着色器缓存", paths=[os.path.join(L, "D3DSCache")], action="auto",
             category="缓存"),
        dict(name="NVIDIA 着色器缓存", paths=[os.path.join(L, r"NVIDIA\DXCache")],
             action="auto", category="缓存"),
        dict(name="Chrome 缓存", action="auto", category="浏览器缓存", glob_dirs=[
            os.path.join(L, r"Google\Chrome\User Data", "*", "Cache"),
            os.path.join(L, r"Google\Chrome\User Data", "*", "Code Cache"),
            os.path.join(L, r"Google\Chrome\User Data", "*", "GPUCache"),
            os.path.join(L, r"Google\Chrome\User Data", "*", "Service Worker", "CacheStorage"),
        ]),
        dict(name="Edge 缓存", action="auto", category="浏览器缓存", glob_dirs=[
            os.path.join(L, r"Microsoft\Edge\User Data", "*", "Cache"),
            os.path.join(L, r"Microsoft\Edge\User Data", "*", "Code Cache"),
            os.path.join(L, r"Microsoft\Edge\User Data", "*", "GPUCache"),
        ]),
        dict(name="npm 缓存", paths=[os.path.join(R, "npm-cache")], action="auto",
             category="开发缓存"),
        dict(name="pip 缓存", paths=[os.path.join(L, "pip", "Cache")], action="auto",
             category="开发缓存"),
        dict(name="Yarn 缓存", paths=[os.path.join(L, "Yarn", "Cache")], action="auto",
             category="开发缓存"),

        # ---- 二级：确认后清
        dict(name="回收站", paths=[r"C:\$Recycle.Bin", r"D:\$RECYCLE.BIN"], action="confirm",
             category="回收站", note="清空后不可恢复"),
        dict(name="Windows 更新下载残留",
             paths=[r"C:\Windows\SoftwareDistribution\Download"], action="confirm",
             category="系统", admin=True, note="需要管理员权限"),
        dict(name="旧系统目录", paths=[r"C:\Windows.old"], action="confirm",
             category="系统", note="升级残留，通常几十 G"),

        # ---- 媒体：搬到待确认目录，不直接删
        dict(name="微信聊天图片视频", action="media", category="聊天媒体", glob_dirs=[
            os.path.join(U, r"Documents\xwechat_files", "*", "msg", "attach"),
            os.path.join(U, r"Documents\WeChat Files", "*", "FileStorage", "Image"),
            os.path.join(U, r"Documents\WeChat Files", "*", "FileStorage", "Video"),
        ], note="搬走后微信里这些图会显示已过期，确认没用再整体删"),
        dict(name="QQ 聊天图片视频", action="media", category="聊天媒体", glob_dirs=[
            # QQ NT 版（新版）
            os.path.join(U, r"Documents\Tencent Files", "*", "nt_qq", "nt_data", "Pic"),
            os.path.join(U, r"Documents\Tencent Files", "*", "nt_qq", "nt_data", "Video"),
            os.path.join(U, r"Documents\Tencent Files", "*", "nt_qq", "nt_data", "File"),
            # QQ 旧版
            os.path.join(U, r"Documents\Tencent Files", "*", "Image"),
            os.path.join(U, r"Documents\Tencent Files", "*", "Video"),
            os.path.join(U, r"Documents\Tencent Files", "*", "FileRecv"),
        ]),
        dict(name="QQ 表情缓存", action="auto", category="缓存", glob_dirs=[
            os.path.join(U, r"Documents\Tencent Files", "*", "nt_qq", "nt_data", "Emoji"),
        ], note="表情包缓存，删了会重新下载"),

        # ---- 永不自动：只统计
        dict(name="休眠文件", paths=[r"C:\hiberfil.sys"], action="never", category="系统",
             note="关闭需 powercfg /h off，会失去快速启动"),
        dict(name="页面文件", paths=[r"C:\pagefile.sys"], action="never", category="系统",
             note="不建议动"),
    ]


def scan_rule(rule):
    items = []
    targets = []
    for p in rule.get("paths", []):
        targets.append((expand(p), None))
    for g in rule.get("glob_dirs", []):
        import glob as _glob
        for hit in _glob.glob(expand(g)):
            targets.append((hit, None))
    for p, _ in targets:
        if not os.path.exists(p):
            continue
        if in_never_touch(p):
            continue
        # 单文件规则
        if os.path.isfile(p):
            st = os.stat(p)
            items.append(dict(path=p, size=st.st_size, files=1,
                              newest=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                              errors=0))
            continue
        # 通配文件规则（如 thumbcache_*.db）
        if rule.get("glob"):
            import glob as _glob
            for f in _glob.glob(os.path.join(p, rule["glob"])):
                try:
                    st = os.stat(f)
                    items.append(dict(path=f, size=st.st_size, files=1,
                                      newest=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                                      errors=0))
                except OSError:
                    pass
            continue
        size, count, newest, errors = walk_stats(p, rule.get("age"))
        if count == 0:
            continue
        items.append(dict(path=p, size=size, files=count,
                          newest=datetime.fromtimestamp(newest).strftime("%Y-%m-%d") if newest else "-",
                          errors=errors))
    return items


def scan_orphans():
    """找出疑似没有应用本体的残留目录。"""
    names, locs = installed_index()
    bases = [
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        r"C:\ProgramData",
    ]
    out = []
    cutoff = time.time() - ORPHAN_MIN_AGE_DAYS * 86400
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        try:
            entries = list(os.scandir(base))
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False) or is_reparse(e):
                    continue
                n = norm(e.name)
                if len(n) < 3 or in_never_touch(e.path):
                    continue
                # 有匹配的已安装应用就跳过
                if any(n in x or x in n for x in names if len(x) >= 3):
                    continue
                if any(n == x for x in locs):
                    continue
                size, count, newest, errors = walk_stats(e.path)
                if size < ORPHAN_MIN_SIZE or not newest or newest > cutoff:
                    continue
                # 目录里还有 exe/dll，可能是绿色软件，不判定为孤儿
                if has_exe(e.path):
                    continue
                out.append(dict(path=e.path, size=size, files=count,
                                newest=datetime.fromtimestamp(newest).strftime("%Y-%m-%d"),
                                errors=errors))
            except OSError:
                continue
    return out


def scan_installers():
    """下载目录里的旧安装包。"""
    roots = [os.path.join(os.environ.get("USERPROFILE", ""), "Downloads")]
    out = []
    cutoff = time.time() - INSTALLER_AGE_DAYS * 86400
    for root in roots:
        if not os.path.isdir(root):
            continue
        for cur, _d, files in os.walk(root):
            for f in files:
                if not f.lower().endswith((".exe", ".msi", ".zip", ".7z", ".rar", ".iso")):
                    continue
                p = os.path.join(cur, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size < 20 * 1024 * 1024 or st.st_mtime > cutoff:
                    continue
                out.append(dict(path=p, size=st.st_size, files=1,
                                newest=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                                errors=0))
    return out


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--skip-orphan", action="store_true")
    args = ap.parse_args()

    os.makedirs(DEFAULT_OUT_DIR, exist_ok=True)
    t0 = time.time()
    report = dict(generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), items=[])

    def add(name, category, action, hits, note="", admin=False):
        if not hits:
            return
        total = sum(h["size"] for h in hits)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or "item"
        report["items"].append(dict(
            id="%s-%d" % (slug, len(report["items"])),
            name=name, category=category, action=action, note=note, admin=admin,
            size=total, files=sum(h["files"] for h in hits),
            paths=[dict(path=h["path"], size=h["size"], files=h["files"],
                        newest=h["newest"]) for h in hits],
        ))

    print("扫描中…", flush=True)
    for rule in build_rules():
        hits = scan_rule(rule)
        add(rule["name"], rule["category"], rule["action"], hits,
            rule.get("note", ""), rule.get("admin", False))

    if not args.skip_orphan:
        print("检测孤儿目录…", flush=True)
        add("疑似孤儿目录", "卸载残留", "confirm", scan_orphans(),
            "四处都找不到对应应用，八成把握，确认后删")

    add("下载目录里的旧安装包", "安装包", "confirm", scan_installers(),
        "30 天以上、20MB 以上的安装包")

    if args.deep:
        print("统计盘符顶层占用（慢）…", flush=True)
        tops = []
        for drive in ("C:\\", "D:\\"):
            try:
                for e in os.scandir(drive):
                    if e.is_dir(follow_symlinks=False) and not is_reparse(e):
                        s, c, nw, _ = walk_stats(e.path)
                        tops.append(dict(path=e.path, size=s, files=c,
                                         newest=datetime.fromtimestamp(nw).strftime("%Y-%m-%d") if nw else "-"))
            except OSError:
                pass
        tops.sort(key=lambda x: -x["size"])
        report["top_dirs"] = tops[:25]

    report["elapsed"] = round(time.time() - t0, 1)
    out = args.out or os.path.join(DEFAULT_OUT_DIR, "scan-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    # 控制台摘要
    order = {"auto": 0, "media": 1, "confirm": 2, "never": 3}
    report["items"].sort(key=lambda x: (order.get(x["action"], 9), -x["size"]))
    label = {"auto": "可自动清", "media": "搬去待确认", "confirm": "需确认", "never": "仅报告"}
    cur = None
    for it in report["items"]:
        if it["action"] != cur:
            cur = it["action"]
            print("\n[%s]" % label[cur])
        print("  %-24s %9s  %5d 个文件" % (it["name"], human(it["size"]), it["files"]))
        for p in it["paths"][:4]:
            print("      %s  (%s, %s)" % (p["path"], human(p["size"]), p["newest"]))
        if len(it["paths"]) > 4:
            print("      …另有 %d 处" % (len(it["paths"]) - 4))
    print("\n报告: %s" % out)
    print("耗时 %.1f 秒" % report["elapsed"])


if __name__ == "__main__":
    main()
