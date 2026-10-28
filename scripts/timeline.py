"""合并微信 + QQ 的时间轴：年度活跃 / 重要关系。

    python scripts/timeline.py

不重新扫库：直接读两边的 _jsonl/（正则抓 "t": "YYYY-MM"）和 _索引.csv，几十秒出结果。
"""
from __future__ import annotations

import collections
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cfg import cfg, setup_stdout  # noqa: E402

TS_RE = re.compile(rb'"t":\s*"(\d{4})-(\d{2})')
TOP_N = 120


def main() -> int:
    setup_stdout()
    platforms = {"微信": cfg.wechat_out, "QQ": cfg.qq_out}
    present = {k: v for k, v in platforms.items() if (v / "_索引.csv").exists()}
    if not present:
        raise SystemExit("两边都没有 _索引.csv：先跑导出脚本")

    year = collections.defaultdict(collections.Counter)          # 平台 -> 年 -> 条数
    conv_year = collections.defaultdict(collections.Counter)     # (平台, 会话) -> 年 -> 条数
    index = {}

    for plat, root in present.items():
        with open(root / "_索引.csv", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                index[(plat, Path(row["文件"]).stem)] = row
        for p in sorted((root / "_jsonl").glob("*.jsonl")):
            cy = conv_year[(plat, p.stem)]
            with open(p, "rb") as f:
                for line in f:
                    m = TS_RE.search(line)
                    if m:
                        y = m.group(1).decode()
                        year[plat][y] += 1
                        cy[y] += 1
        print("%s: %d 个会话文件" % (plat, len(list((root / "_jsonl").glob("*.jsonl")))))

    out = cfg.timeline_dir
    out.mkdir(parents=True, exist_ok=True)

    years = sorted(set().union(*[set(c) for c in year.values()]) if year else [])
    total = {p: sum(c.values()) for p, c in year.items()}
    lines = ["# 总览：年度活跃", "", "按消息条数统计，逐年对比各平台。", "",
             "| 年份 | " + " | ".join(present) + " | 合计 |",
             "|---|" + "---:|" * (len(present) + 1)]
    for y in years:
        vals = [year[p][y] for p in present]
        lines.append("| %s | %s | %s |" % (y, " | ".join("%d" % v for v in vals), sum(vals)))
    lines.append("| **合计** | %s | %s |" % (" | ".join("**%d**" % total.get(p, 0) for p in present),
                                            sum(total.values())))
    (out / "总览-年度活跃.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    rows = []
    for (plat, stem), info in index.items():
        try:
            n = int(info["消息数"])
        except (ValueError, KeyError):
            continue
        cy = conv_year.get((plat, stem)) or collections.Counter()
        peak = max(cy.items(), key=lambda kv: kv[1])[0] if cy else ""
        rows.append((n, plat, info.get("类型", ""), info.get("名称", ""),
                     info.get("开始", ""), info.get("结束", ""), peak, len(cy)))
    rows.sort(reverse=True)

    rel = ["# 重要关系总览", "",
           "共 %d 个会话，按消息量排序取前 %d。" % (len(rows), min(TOP_N, len(rows))), "",
           "| # | 平台 | 类型 | 名称 | 消息数 | 开始 | 结束 | 峰值年 | 活跃年数 |",
           "|---:|---|---|---|---:|---|---|---|---:|"]
    for i, (n, plat, typ, name, s, e, peak, ny) in enumerate(rows[:TOP_N], 1):
        rel.append("| %d | %s | %s | %s | %d | %s | %s | %s | %d |"
                   % (i, plat, typ, name.replace("|", "\\|"), n, s, e, peak, ny))
    (out / "重要关系总览.md").write_text("\n".join(rel) + "\n", encoding="utf-8")

    print("\n" + "\n".join(lines))
    print("\n会话 %d 个，输出目录：%s" % (len(rows), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
