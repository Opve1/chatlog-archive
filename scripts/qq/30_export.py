"""QQ 明文库 -> Markdown + JSONL + 索引 CSV

    python scripts/qq/30_export.py

依赖 vendor/nt_msg_db_util（首次运行自动下载）。
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

# 本地 protobuf 运行时与生成代码的版本校验经常打架，导出用不到它，直接关掉
try:
    import google.protobuf.runtime_version as _rv
    _rv.ValidateProtobufRuntimeVersion = lambda *a, **k: None
except Exception:
    pass

from cfg import cfg, ro, safe_name, setup_stdout  # noqa: E402
from vendor_fetch import ensure  # noqa: E402

VENDOR = ensure("nt_msg_db_util")
sys.path.insert(0, str(VENDOR))
try:
    from msgdb.c2c.parser import parse_row as c2c_parse, SELECT_SQL as C2C_SQL
    from msgdb.group.exporter import parse_row as grp_parse, SELECT_SQL as GRP_SQL
except ImportError as e:
    raise SystemExit("加载 vendor/nt_msg_db_util 失败：%s\n请确认目录结构没被改动。" % e)

GROUP_TYPES = {3: "文件", 5: "引用", 6: "名片", 7: "视频", 8: "合并转发", 11: "合并转发",
               17: "系统", 19: "通话"}
CT_LABEL = {2: "图片", 3: "语音", 4: "文件", 5: "表情", 6: "视频"}


def render(msg_type, ct, text) -> str:
    if text:
        return text
    if msg_type == 2:
        return "[%s]" % CT_LABEL.get(ct, "消息")
    return "[%s]" % GROUP_TYPES.get(msg_type, "类型%s" % msg_type)


def iso(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return "1970-01-01T08:00:00"


def load_maps(d):
    """uid->QQ号、QQ号->名字、群号->群名、群名片、uid->名字。"""
    uid2uin, uin2name, prof = {}, {}, {}
    con = ro(d / "nt_msg.db")
    for uid, uin in con.execute('SELECT "48902","1002" FROM nt_uid_mapping_table'):
        if uid:
            uid2uin[uid] = uin
    con.close()

    con = ro(d / "profile_info.db")
    for uid, uin, nick, remark in con.execute(
            'SELECT "1000","1002","20002","20009" FROM profile_info_v6'):
        if uid:
            prof[uid] = (remark or nick or "")
        if uin:
            uin2name[uin] = (remark or "").strip() or (nick or "").strip() or str(uin)
            if uid and not uid2uin.get(uid):
                uid2uin[uid] = uin
    con.close()

    groups, members = {}, {}
    con = ro(d / "group_info.db")
    for code, name in con.execute('SELECT "60001","60007" FROM group_list'):
        groups[code] = (name or "").strip() or str(code)
    for code, uid, card, uin in con.execute('SELECT "60001","1000","20002","1002" FROM group_member3'):
        if uid and card:
            members[(code, uid)] = card
        if uid and uin and not uid2uin.get(uid):
            uid2uin[uid] = uin
    con.close()
    return uid2uin, uin2name, groups, members, prof


def resolve_self_uid(uid2uin) -> str:
    if cfg.qq_self_uid:
        return cfg.qq_self_uid
    if cfg.qq_self_uin:
        for uid, uin in uid2uin.items():
            if uin == cfg.qq_self_uin:
                return uid
    return ""


def main() -> int:
    setup_stdout()
    t0 = time.time()
    d = cfg.qq_plain
    if not (d / "nt_msg.db").exists():
        raise SystemExit("找不到 %s，先跑 scripts/qq/20_decrypt.py" % (d / "nt_msg.db"))

    uid2uin, uin2name, groups, members, prof = load_maps(d)
    self_uid = resolve_self_uid(uid2uin)
    print("映射：uid->QQ号 %d，QQ号->名字 %d，群 %d，群名片 %d"
          % (len(uid2uin), len(uin2name), len(groups), len(members)))
    print("本人 uid：%s（QQ %s）" % (self_uid or "未识别", cfg.qq_self_uin or "?"))
    if not self_uid:
        print("！未识别本人 uid：消息会分不清谁发的。请在 config 里填 qq.self_uid 或 qq.self_uin")

    def person_name(uid, uin_hint=None) -> str:
        uin = uin_hint or uid2uin.get(uid)
        return uin2name.get(uin) or prof.get(uid) or (str(uin) if uin else uid or "未知")

    out = cfg.qq_out
    convs: dict[tuple, list] = {}
    stats = {"c2c": 0, "group": 0}
    skipped = 0

    con = ro(d / "nt_msg.db")
    for row in con.execute(C2C_SQL):
        msg = c2c_parse(row)
        if not msg.timestamp:
            skipped += 1
            continue
        is_self = msg.sender_uid == self_uid
        name = person_name(msg.peer_uid)
        speaker = "我" if is_self else (uin2name.get(msg.sender_qq) or name)
        convs.setdefault(("单聊", name, msg.peer_uid), []).append(
            (iso(msg.timestamp), speaker, msg.sender_uid, msg.msg_type,
             render(msg.msg_type, msg.content_type, msg.text)))
        stats["c2c"] += 1
        if stats["c2c"] % 20000 == 0:
            print("  c2c %d" % stats["c2c"], flush=True)

    for row in con.execute(GRP_SQL):
        m = grp_parse(row)
        if not m["timestamp"]:
            skipped += 1
            continue
        code = m["group_id"]
        numeric = str(code).isdigit()
        gname = (groups.get(int(code)) if numeric else groups.get(code)) or str(code)
        is_self = m["sender_uid"] == self_uid
        speaker = "我" if is_self else (
            (members.get((int(code), m["sender_uid"])) if numeric else None)
            or person_name(m["sender_uid"], m["sender_qq"])
        )
        convs.setdefault(("群聊", gname, str(code)), []).append(
            (iso(m["timestamp"]), speaker, m["sender_uid"], m["msg_type"],
             render(m["msg_type"], m["content_type"], m["text"])))
        stats["group"] += 1
        if stats["group"] % 50000 == 0:
            print("  group %d" % stats["group"], flush=True)
    con.close()

    print("解析完成：单聊 %d 条，群聊 %d 条，会话 %d 个，跳过无时间戳 %d 条"
          % (stats["c2c"], stats["group"], len(convs), skipped), flush=True)

    index_rows = []
    for (kind, name, cid), msgs in convs.items():
        msgs.sort(key=lambda x: x[0])
        stem = safe_name("%s_%s" % (name, cid))
        sub = out / kind
        sub.mkdir(parents=True, exist_ok=True)
        md = sub / (stem + ".md")
        jl = out / "_jsonl"
        jl.mkdir(parents=True, exist_ok=True)
        jl = jl / (stem + ".jsonl")
        with open(md, "w", encoding="utf-8") as f, open(jl, "w", encoding="utf-8") as jf:
            f.write("# %s · %s\n\n- 标识：`%s`\n- 消息数：%d\n"
                    "- 时间：%s ~ %s\n\n---\n"
                    % (kind, name, cid, len(msgs),
                       msgs[0][0].replace("T", " "), msgs[-1][0].replace("T", " ")))
            cur_day = None
            for t, spk, uid, mt, txt in msgs:
                day = t[:10]
                if day != cur_day:
                    f.write("\n## %s\n" % day)
                    cur_day = day
                body = txt.replace("\r", "").replace("\n", "\n> ")
                f.write("- `%s` **%s**：%s\n" % (t[11:16], spk, body))
                jf.write(json.dumps({"t": t, "from": spk, "from_id": uid,
                                     "type": mt, "text": txt}, ensure_ascii=False) + "\n")
        index_rows.append([kind, name, cid, len(msgs), msgs[0][0][:10], msgs[-1][0][:10],
                           str(md)])

    index_rows.sort(key=lambda r: -r[3])
    with open(out / "_索引.csv", "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["类型", "名称", "标识", "消息数", "开始", "结束", "文件"])
        wr.writerows(index_rows)

    print("写出 %d 个会话，总消息 %d 条，索引 %s"
          % (len(index_rows), sum(r[3] for r in index_rows), out / "_索引.csv"))
    print("耗时 %.0fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
