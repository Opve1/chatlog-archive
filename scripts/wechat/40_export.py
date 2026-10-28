"""微信明文库 -> Markdown + JSONL + 索引 CSV

    python scripts/wechat/40_export.py

三个必须处理的结构陷阱（改代码时别丢）：
  1. 消息按时间切成 message_0..5.db，同一会话散在多个分片 -> **先合并再落盘**
  2. 每个分片里每个会话一张表，表名 Msg_<md5(wxid)> -> 靠 contact.db / Name2Id 反查
  3. local_type = (子类型 << 32) | 基础类型；WCDB_CT_message_content > 0 时正文是 zstd
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cfg import cfg, ro, safe_name, setup_stdout  # noqa: E402

try:
    import zstandard as zstd
    ZCTX = zstd.ZstdDecompressor()
except Exception:
    ZCTX = None

TYPES = {1: "文本", 3: "图片", 34: "语音", 42: "名片", 43: "视频", 47: "表情",
         48: "位置", 49: "应用消息", 50: "通话", 51: "状态", 62: "短视频", 63: "直播",
         10000: "系统", 10002: "系统", 10003: "撤回"}
APP_KIND = {"3": "音乐", "5": "链接", "6": "文件", "19": "聊天记录", "33": "小程序",
            "36": "小程序", "51": "视频号", "57": "引用回复", "63": "视频号直播",
            "87": "群公告", "2000": "转账", "2001": "红包"}


def decompress(blob, ct) -> str:
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob
    if not ct:
        return blob.decode("utf-8", "replace")
    for fn in (lambda b: ZCTX.decompress(b) if ZCTX else None,
               lambda b: __import__("zlib").decompress(b)):
        try:
            r = fn(blob)
            if r:
                return r.decode("utf-8", "replace")
        except Exception:
            continue
    return blob.decode("utf-8", "replace")


def render(lt: int, content: str) -> str:
    sub = (lt >> 32) if lt and lt > 0xFFFFFFFF else 0
    base = lt & 0xFFFFFFFF
    if not content:
        return "[空]"
    if base == 1:
        return content
    if base == 3:
        return "[图片]"
    if base == 34:
        return "[语音]"
    if base in (43, 62):
        return "[视频]"
    if base == 47:
        return "[表情]"
    if base == 42:
        return "[名片]"
    if base == 48:
        return "[位置]"
    if base == 50:
        return "[通话]"
    if base in (10000, 10002, 10003):
        txt = re.sub(r"<!\[CDATA\[|\]\]>", "", re.sub(r"<[^>]+>", " ", content))
        return "[系统] " + re.sub(r"\s+", " ", txt).strip()[:300]
    if base == 49:
        m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", content, re.S)
        title = m.group(1).strip() if m else ""
        t = re.search(r"<type>(\d+)</type>", content)
        kind = APP_KIND.get(t.group(1) if t else "", "")
        if not kind:
            kind = {0: "应用", 1: "应用", 6: "文件", 8: "应用"}.get(sub, "应用")
        extra = ""
        if kind == "引用回复":
            ref = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content)).strip()
            extra = " | " + ref[:150]
        elif kind == "文件":
            sz = re.search(r"<totallen>(\d+)</totallen>", content)
            if sz:
                extra = " (%d KB)" % round(int(sz.group(1)) / 1024)
        return "[%s] %s%s" % (kind, title or "(无标题)", extra)
    return "[%s] %s" % (TYPES.get(base, "类型%d" % base), content[:120])


def md5_name(wxid: str) -> str:
    return "Msg_" + hashlib.md5(wxid.encode()).hexdigest()


def load_contacts(plain: Path):
    con = ro(plain / "contact" / "contact.db")
    info = {}
    for username, remark, nick, alias in con.execute(
            "select username, remark, nick_name, alias from contact"):
        info[username] = {"name": (remark or "").strip() or (nick or "").strip() or username,
                          "nick": nick or "", "alias": alias or ""}
    n2id = {r[0]: r[1] for r in con.execute("select rowid, username from name2id")}
    con.close()
    return info, n2id


def main() -> int:
    setup_stdout()
    t0 = time.time()
    plain = cfg.wechat_plain
    out = cfg.wechat_out
    if not plain.exists():
        raise SystemExit("找不到 %s，先跑 30_decrypt.py" % plain)

    info, n2id = load_contacts(plain)
    print("联系人 %d，name2id %d" % (len(info), len(n2id)), flush=True)

    by_md5 = {md5_name(u): u for u in info}
    for u in n2id.values():
        by_md5.setdefault(md5_name(u), u)

    shards = sorted((plain / "message").glob("message_[0-9]*.db"))
    if not shards:
        raise SystemExit("没有 message/message_*.db，检查 work/wechat/decrypted 结构")
    print("消息分片：%s" % [p.name for p in shards], flush=True)

    self_wxid = cfg.self_wxid
    if not self_wxid:
        print("！未识别 self_wxid：所有发言人都会显示成 wxid。请在 config 里手填。")

    merged: dict[str, list] = {}
    for dbp in shards:
        con = ro(dbp)
        id2user = {r[0]: r[1] for r in con.execute("select rowid, user_name from Name2Id")}
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table' and name like 'Msg_%'")]
        for tbl in tables:
            username = by_md5.get(tbl) or id2user.get(0) or tbl
            bucket = merged.setdefault(username, [])
            rows = con.execute(
                "select local_id, local_type, create_time, real_sender_id, "
                "message_content, WCDB_CT_message_content, sort_seq from \"%s\"" % tbl).fetchall()
            for local_id, lt, ctime, sender_id, content, ct, seq in rows:
                when = datetime.fromtimestamp(ctime) if ctime else None
                sender = id2user.get(sender_id) or ""
                who = "我" if (not sender or sender == self_wxid) else (
                    info.get(sender, {}).get("name") or sender)
                bucket.append((seq or 0, local_id, when, who, sender, lt,
                               render(lt, decompress(content, ct))))
        con.close()
        print("  %s 完成（累计会话 %d，消息 %d，%.0fs）"
              % (dbp.name, len(merged), sum(len(v) for v in merged.values()),
                 time.time() - t0), flush=True)

    (out / "单聊").mkdir(parents=True, exist_ok=True)
    (out / "群聊").mkdir(parents=True, exist_ok=True)
    (out / "_jsonl").mkdir(parents=True, exist_ok=True)

    index = []
    for username, rows in merged.items():
        rows.sort(key=lambda r: (r[0], r[1]))
        is_group = username.endswith("@chatroom")
        meta = info.get(username, {})
        display = meta.get("name") or username
        ts = [r[2] for r in rows if r[2]]
        stem = safe_name(display)
        sub = "群聊" if is_group else "单聊"
        path = out / sub / (stem + ".md")
        n = 1
        while path.exists():
            path = out / sub / ("%s_%d.md" % (stem, n))
            n += 1

        lines = ["# %s" % display, "",
                 "- 微信 ID：`%s`" % username,
                 "- 昵称：%s" % (meta.get("nick") or "—"),
                 "- 消息数：%d" % len(rows)]
        if ts:
            lines.append("- 时间范围：%s ~ %s" % (min(ts).strftime("%Y-%m-%d"),
                                                  max(ts).strftime("%Y-%m-%d")))
        jl = []
        last_day = None
        for seq, local_id, when, who, sender, lt, text in rows:
            day = when.strftime("%Y-%m-%d") if when else "未知日期"
            if day != last_day:
                lines.append("\n## %s" % day)
                last_day = day
            stamp = when.strftime("%H:%M") if when else "--:--"
            lines.append("- `%s` **%s**：%s" % (stamp, who, text.replace("\n", " / ")))
            jl.append({"t": when.isoformat() if when else None, "from": who,
                       "from_id": sender, "type": lt, "text": text})
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out / "_jsonl" / ("%s_%s.jsonl" % (stem, username.replace("@", "_")))).write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in jl), encoding="utf-8")
        index.append({"类型": sub, "名称": display, "微信ID": username,
                      "消息数": len(rows),
                      "开始": min(ts).strftime("%Y-%m-%d") if ts else "",
                      "结束": max(ts).strftime("%Y-%m-%d") if ts else "",
                      "文件": str(path)})

    with open(out / "_索引.csv", "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["类型", "名称", "微信ID", "消息数", "开始", "结束", "文件"])
        wr.writeheader()
        for row in sorted(index, key=lambda r: -r["消息数"]):
            wr.writerow(row)

    total = sum(r["消息数"] for r in index)
    print("\n导出完成：%d 个会话，%d 条消息，%.0fs" % (len(index), total, time.time() - t0))
    print("索引：%s" % (out / "_索引.csv"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
