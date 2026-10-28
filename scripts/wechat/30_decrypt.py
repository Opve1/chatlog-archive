"""微信明文库：用 keys.json 把 db_storage 里的库整库解密。

    python scripts/wechat/30_decrypt.py

产物：work/wechat/decrypted/...（保持原来的子目录结构，可直接被 sqlite3 打开）
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cfg import cfg, setup_stdout  # noqa: E402
from vendor_fetch import ensure  # noqa: E402


def main() -> int:
    setup_stdout()
    db_dir = cfg.require_wechat()
    keys = cfg.wechat_keys
    out = cfg.wechat_plain
    if not keys.exists():
        raise SystemExit("找不到 %s，先跑 20_get_keys.py" % keys)

    tool = next(iter(ensure("wcdb-key-tool").rglob("wcdb_key_tool_windows.py")), None)
    if not tool:
        raise SystemExit("vendor/wcdb-key-tool 结构异常")

    t0 = time.time()
    print("解密 %s -> %s" % (db_dir, out), flush=True)
    rc = subprocess.run([sys.executable, str(tool), "decrypt", "--db-dir", str(db_dir),
                         "--keys", str(keys), "--output", str(out)]).returncode
    if rc != 0:
        print("解密进程返回 %d，看上面的报错。" % rc)

    # --- 自检：能否当普通 SQLite 打开 ---
    dbs = sorted(out.rglob("*.db"))
    ok, bad = 0, []
    for p in dbs:
        try:
            con = sqlite3.connect("file:%s?mode=ro" % str(p).replace("\\", "/"), uri=True)
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            con.close()
            ok += 1
        except sqlite3.DatabaseError as e:
            bad.append((p.name, str(e)))
    print("\n解密完成：%d 个库, %.1fs" % (len(dbs), time.time() - t0))
    print("可正常打开 %d 个" % ok)
    for name, err in bad[:8]:
        print("  打不开 %s: %s" % (name, err))

    msg_dir = out / "message"
    shards = sorted(msg_dir.glob("message_[0-9]*.db")) if msg_dir.exists() else []
    print("消息分片：%s" % ([p.name for p in shards] or "！（没找到 message/message_*.db）"))
    if not shards:
        print("！下一步导出会失败：确认 db_dir 选到了 db_storage 而不是上一层")
        return 1
    return 0 if ok and not bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
