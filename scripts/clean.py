#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pc-cleanup 执行器
读取 scan.py 生成的报告，按类别清理。

用法:
    python clean.py <report.json> --list                # 只看会动哪些
    python clean.py <report.json> --action auto         # 清可自动项
    python clean.py <report.json> --action media        # 媒体搬到待确认区
    python clean.py <report.json> --id xxx --id yyy     # 指定条目
    python clean.py <report.json> --action confirm --yes

默认 dry-run，加 --yes 才真的执行。
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime

STAGE_ROOT = r"D:\Cleanup\media"
LOG_DIR = r"D:\Cleanup\logs"
NEVER_TOUCH = [
    r"\windows\winsxs", r"\windows\system32", r"\windows\syswow64", r"\windows\assembly",
    r"\program files\windowsapps",
    r"\新建文件夹\claw", r"\.git", r"\node_modules", r"\venv", r"\whisper\models",
    r"\site-packages",
]


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def safe_path(p):
    """拒绝危险路径。"""
    ap = os.path.abspath(p)
    if ap.count(os.sep) <= 1:
        return False, "盘符根目录"
    low = ap.lower()
    for k in NEVER_TOUCH:
        if k in low:
            return False, "命中禁改清单: %s" % k
    if low.startswith(r"c:\windows") and "temp" not in low:
        return False, "系统目录"
    return True, ""


def clear_recycle_bin(drive):
    """清空指定盘的回收站。"""
    ps = "Clear-RecycleBin -DriveLetter '%s' -Force -ErrorAction Stop" % drive
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stderr or "").strip()[:200]


def move_to_stage(src, item_name, ts):
    """把媒体文件搬到待确认目录，保留相对结构。"""
    dst_root = os.path.join(STAGE_ROOT, item_name, ts)
    rel = os.path.basename(src.rstrip("\\/"))
    dst = os.path.join(dst_root, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        dst = dst + "-dup%d" % int(time.time() % 1000)
    shutil.move(src, dst)
    return dst


def make_writable(p):
    """去掉只读属性。微信/QQ 的聊天媒体默认是 -r--r--r--，不先改属性会 PermissionError。"""
    try:
        st = os.stat(p)
        if not st.st_mode & stat.S_IWRITE:
            os.chmod(p, st.st_mode | stat.S_IWRITE)
    except OSError:
        pass


def purge(path):
    """
    直删：先把文件截断成 0 字节（立即释放空间，不经过回收站），再删空壳。
    某些环境会把删除重定向到回收站，只删不截断等于白干。
    返回 (释放字节, 跳过文件数)。
    """
    freed = 0
    skipped = 0
    if os.path.isfile(path):
        try:
            sz = os.path.getsize(path)
            make_writable(path)
            with open(path, "r+b") as f:
                f.truncate(0)
            freed += sz
            os.remove(path)
        except OSError:
            skipped += 1
        return freed, skipped

    dirs_to_remove = []
    for root, dirs, files in os.walk(path):
        dirs_to_remove.append(root)
        for name in files:
            p = os.path.join(root, name)
            try:
                sz = os.path.getsize(p)
                make_writable(p)
                with open(p, "r+b") as f:
                    f.truncate(0)
                freed += sz
            except OSError:
                skipped += 1
                continue
            try:
                os.remove(p)
            except OSError:
                pass
    for d in sorted(dirs_to_remove, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass
    return freed, skipped


def rmtree_tolerant(path):
    """删目录，跳过被占用的文件。返回 (实际删除字节, 失败文件数)。"""
    deleted = 0
    failed = 0
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
                os.remove(p)
                deleted += sz
            except OSError:
                failed += 1
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass
    return deleted, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--action", default=None)
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    with open(args.report, encoding="utf-8") as f:
        rep = json.load(f)
    items = rep.get("items", [])
    if args.id:
        sel = [i for i in items if i["id"] in args.id]
    elif args.action:
        sel = [i for i in items if i["action"] == args.action]
    else:
        sel = items
    if not sel:
        print("没有匹配的条目")
        return

    total = sum(i["size"] for i in sel)
    print("选中 %d 项，合计 %s" % (len(sel), human(total)))
    for i in sel:
        print("  [%s] %-24s %9s" % (i["action"], i["name"], human(i["size"])))

    if args.list or not args.yes:
        if not args.yes:
            print("\n这是预演（dry-run）。确认无误后加 --yes 执行。")
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(LOG_DIR, "clean-%s.jsonl" % ts)
    logf = open(log_path, "a", encoding="utf-8")
    freed = 0
    failed = []

    def rec(entry):
        logf.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logf.flush()

    for item in sel:
        if item["action"] == "never":
            continue
        for p in item["paths"]:
            path = p["path"]
            ok, why = safe_path(path)
            if not ok:
                print("跳过 %s（%s）" % (path, why))
                rec(dict(ts=ts, path=path, action="skip", reason=why))
                continue

            # 回收站特殊处理
            if path.rstrip("\\/").lower().endswith("$recycle.bin"):
                drive = path[0].upper()
                good, err = clear_recycle_bin(drive)
                if good:
                    freed += p["size"]
                    print("已清空 %s 盘回收站  %s" % (drive, human(p["size"])))
                    rec(dict(ts=ts, path=path, action="empty_recycle_bin", size=p["size"]))
                else:
                    failed.append((path, err))
                    rec(dict(ts=ts, path=path, action="fail", error=err))
                continue

            try:
                if item["action"] == "media":
                    dst = move_to_stage(path, item["name"], ts)
                    freed += p["size"]
                    print("已搬移 %s → %s" % (path, dst))
                    rec(dict(ts=ts, path=path, action="move", dst=dst, size=p["size"]))
                elif os.path.isdir(path) or os.path.isfile(path):
                    got, bad = purge(path)
                    freed += got
                    tail = "" if not bad else "（%d 个文件被占用，已跳过）" % bad
                    print("已清除 %s  %s%s" % (path, human(got), tail))
                    rec(dict(ts=ts, path=path, action="purge", size=got, skipped=bad))
                else:
                    print("跳过 %s（已不存在）" % path)
                    rec(dict(ts=ts, path=path, action="skip", reason="not_found"))
                    rec(dict(ts=ts, path=path, action="delete_file", size=p["size"]))
            except Exception as e:
                failed.append((path, str(e)))
                print("失败 %s：%s" % (path, e))
                rec(dict(ts=ts, path=path, action="fail", error=str(e)))

    logf.close()
    print("\n合计释放 %s" % human(freed))
    if failed:
        print("有 %d 项没做成（多半是被占用或权限不足）：" % len(failed))
        for p, e in failed[:10]:
            print("  %s — %s" % (p, e[:80]))
    print("日志: %s" % log_path)
    if any(i["action"] == "media" for i in sel):
        print("媒体文件已放到: %s" % os.path.join(STAGE_ROOT, ts))


if __name__ == "__main__":
    main()
