#!/usr/bin/env python3
"""delivery_stats.py — 递送接受率统计（周复盘数据源）。

扫描 daily/*.md 里的递送埋点行：
  - 递送：[条目ID] → 接受/忽略      （AGENTS.md 规定的标准格式）
按自然周（周一~周日）汇总：递送总数、接受、忽略、未标注，接受率。

用法：
  python tools/delivery_stats.py [--vault <路径>] [--weeks 4]

vault 缺省 = 本脚本所在目录的上一级。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# 埋点行：以「- 递送」开头，行内带结论词；ID 可选
RE_LOG = re.compile(
    r"^\s*-\s*递送[：:]\s*(?:\[(?P<id>[^\]]+)\])?\s*(?P<rest>.+?)\s*(?:→|->)\s*(?P<verdict>\S+)",
    re.M,
)
RE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def week_key(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def collect(daily: Path) -> dict[str, list[dict]]:
    weeks: dict[str, list[dict]] = defaultdict(list)
    for f in sorted(daily.glob("*.md")):
        dm = RE_DATE.match(f.name)
        if not dm:
            continue
        day = date.fromisoformat(dm.group(1))
        try:
            text = f.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        for m in RE_LOG.finditer(text):
            verdict = m.group("verdict").strip("。， ")
            weeks[week_key(day)].append({
                "date": day.isoformat(),
                "id": m.group("id") or (m.group("rest")[:30]),
                "verdict": verdict,
            })
    return weeks


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", help="vault 根路径（缺省 = 脚本上级目录）")
    ap.add_argument("--weeks", type=int, default=0, help="只显示最近 N 周（0=全部）")
    args = ap.parse_args()

    vault = Path(args.vault).resolve() if args.vault else Path(__file__).resolve().parent.parent
    weeks = collect(vault / "daily")
    keys = sorted(weeks)
    if args.weeks:
        keys = keys[-args.weeks:]
    if not keys:
        print("没有找到任何递送埋点行（格式：- 递送：[ID] → 接受/忽略）")
        return

    print(f"{'周(周一起)':<12} 递送  接受  忽略  未标注  接受率")
    for k in keys:
        items = weeks[k]
        acc = sum(1 for i in items if "接受" in i["verdict"] and "不" not in i["verdict"])
        ign = sum(1 for i in items if "忽略" in i["verdict"])
        other = len(items) - acc - ign
        rate = f"{acc / len(items):.0%}" if items else "-"
        print(f"{k:<12} {len(items):>4}  {acc:>4}  {ign:>4}  {other:>4}   {rate}")
        for i in items:
            print(f"    {i['date']} [{i['id']}] -> {i['verdict']}")

    print("\n参考：一周主动接受 ≥3 次 → 递送有价值；连续两周为 0 → 和学生商量降频或换内容")


if __name__ == "__main__":
    main()
