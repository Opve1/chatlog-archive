"""用 key.json 解密 QQ 需要的几个库。

    python scripts/qq/20_decrypt.py
    python scripts/qq/20_decrypt.py --also files_in_chat.db emoji.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from cfg import cfg, setup_stdout  # noqa: E402
import qq_crypto  # noqa: E402

CORE = ["nt_msg.db", "profile_info.db", "group_info.db"]


def tables(p: Path) -> list[str]:
    con = sqlite3.connect("file:%s?mode=ro" % str(p).replace("\\", "/"), uri=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    finally:
        con.close()


def main() -> int:
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--also", nargs="*", default=[], help="额外要解密的库文件名")
    args = ap.parse_args()

    db_dir = cfg.require_qq()
    key, ps, res, it = qq_crypto.load_key(cfg.qq_key_json)
    out_dir = cfg.qq_plain
    out_dir.mkdir(parents=True, exist_ok=True)
    print("参数：页 %d / 保留 %d / 迭代 %d" % (ps, res, it), flush=True)

    t0 = time.time()
    for name in CORE + list(args.also):
        src = db_dir / name
        dst = out_dir / name
        if not src.exists():
            print("跳过（不存在）%s" % name)
            continue
        if dst.exists() and dst.stat().st_size == src.stat().st_size - qq_crypto.HEADER:
            print("已解密，跳过 %s" % name)
            continue
        print("解密 %s (%.1f MB) …" % (name, src.stat().st_size / 1048576), flush=True)
        qq_crypto.decrypt_db(src, dst, key, ps, res, it, quiet=False)

    print("\n校验：")
    ok = True
    for name in CORE:
        p = out_dir / name
        if not p.exists():
            continue
        try:
            ts = tables(p)
            print("  %-18s %d 张表  %s" % (name, len(ts), ", ".join(ts[:6]) + (" …" if len(ts) > 6 else "")))
        except sqlite3.DatabaseError as e:
            ok = False
            print("  %s 打不开：%s" % (name, e))
    print("\n耗时 %.1fs" % (time.time() - t0))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
