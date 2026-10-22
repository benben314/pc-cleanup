import os
import struct
import datetime

BASE = "C:\\$Recycle.Bin"
CUT = datetime.datetime(2026, 10, 21, 2, 30)

rows = []
for sid in os.listdir(BASE):
    d = os.path.join(BASE, sid)
    if not os.path.isdir(d):
        continue
    try:
        names = os.listdir(d)
    except OSError:
        continue
    for f in names:
        if not f.startswith("$I"):
            continue
        p = os.path.join(d, f)
        try:
            b = open(p, "rb").read()
            size = struct.unpack("<Q", b[8:16])[0]
            ft = struct.unpack("<Q", b[16:24])[0]
            dt = datetime.datetime.fromtimestamp((ft - 116444736000000000) / 10000000)
            n = struct.unpack("<I", b[24:28])[0]
            path = b[28:28 + n * 2].decode("utf-16-le").rstrip("\x00")
            rows.append((dt, size, path))
        except Exception:
            pass
rows.sort(reverse=True)
total = sum(r[1] for r in rows)
print("回收站条目 %d 条，合计 %.1f GB" % (len(rows), total / 1073741824))

old = [r for r in rows if r[0] < CUT]
print("\n02:30 之前就在里面的（你自己的）：%d 条，%.2f GB" % (len(old), sum(r[1] for r in old) / 1073741824))
for dt, size, path in sorted(old, key=lambda x: -x[1])[:10]:
    print("  %s  %9.1f MB  %s" % (dt.strftime("%m-%d %H:%M"), size / 1048576, path[:70]))

new = [r for r in rows if r[0] >= CUT]
print("\n今晚清理进去的：%d 条，%.2f GB" % (len(new), sum(r[1] for r in new) / 1073741824))
