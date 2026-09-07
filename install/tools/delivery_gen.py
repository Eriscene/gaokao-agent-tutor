#!/usr/bin/env python3
"""今日递送聚合（高考陪学版）——把 vault 里「值得递给学生」的内容汇成 daily 页的「今日递送」小节。

三个来源（最多 3 条，不列表轰炸）：
  1. 复习队列到期项：各科 00-恢复入口.md 表格里「下次到期 ≤ 今天」的行
     （超期最久的优先，每天重现直到处理，不进去重状态）
  2. 薄弱点：恢复入口「当前薄弱点」小节的条目（每条只递一次；解决靠会话，不靠重复递）
  3. 倒计时里程碑：tutor.json 的 exam_date 命中里程碑天数时递一条

去重状态存 .data/delivery_state.json（条目 ID → 递送日期），递过的不重复出现。

用法:
  python tools/delivery_gen.py [--vault <路径>] [--date YYYY-MM-DD] [--dry-run] [--self-test]

vault 缺省 = 本脚本所在目录的上一级（tools/ 装在 vault 根下）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

MAX_ITEMS = 3
MILESTONES = (300, 200, 150, 100, 60, 30, 14, 7, 3, 1, 0)

SECTION_BEGIN = "<!-- delivery:begin（delivery_gen.py 维护，手改内容下次会被覆盖） -->"
SECTION_END = "<!-- delivery:end -->"

_QUEUE_ROW = re.compile(
    r"^\s*\|\s*([^|]+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]*?)\s*\|"
)


def h8(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


class Tutor:
    def __init__(self, vault: Path):
        self.vault = vault
        self.daily = vault / "daily"
        self.state_file = vault / ".data" / "delivery_state.json"
        self.config = {}
        cfg = vault / "tutor.json"
        if cfg.is_file():
            try:
                self.config = json.loads(cfg.read_text(encoding="utf-8"))
            except Exception:
                pass

    def entries(self) -> list[Path]:
        return sorted(
            p for p in self.vault.glob("*/00-恢复入口.md") if p.parent.name != "daily"
        )


def load_state(f: Path) -> dict[str, str]:
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("delivered", {})
        except Exception:
            pass
    return {}


def save_state(f: Path, state: dict[str, str]) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps({"delivered": state}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fresh(state: dict[str, str], item_id: str, today: date) -> bool:
    """跨天不重复递；当天已递过的条目保持留存（重跑不换当天页面内容）。"""
    delivered = state.get(item_id)
    if delivered is None:
        return True
    try:
        return date.fromisoformat(delivered) == today
    except ValueError:
        return False


# ---- 来源 1：复习队列到期项 -------------------------------------------------

def due_reviews(t: Tutor, today: date) -> list[dict]:
    """四列：知识点|上次复现|下次到期|反馈。取第 3 列「下次到期」判断，超期最久优先。"""
    out: list[dict] = []
    for entry in t.entries():
        try:
            text = entry.read_text(encoding="utf-8")
        except Exception:
            continue
        for line in text.splitlines():
            m = _QUEUE_ROW.match(line)
            if not m:
                continue
            topic, _last, due_s, feedback = m.groups()
            if "知识点" in topic or "到期节奏" in line:
                continue
            try:
                due = date.fromisoformat(due_s)
            except ValueError:
                continue
            if due <= today:
                out.append({
                    "subject": entry.parent.name,
                    "topic": topic,
                    "due": due_s,
                    "overdue": (today - due).days,
                    "feedback": feedback,
                })
    out.sort(key=lambda x: (-x["overdue"], x["subject"]))
    return out


# ---- 来源 2：薄弱点 -----------------------------------------------------------

_WEAK_HEAD = re.compile(r"^##\s*当前薄弱点.*$", re.MULTILINE)


def weak_points(t: Tutor) -> list[dict]:
    out: list[dict] = []
    for entry in t.entries():
        try:
            text = entry.read_text(encoding="utf-8")
        except Exception:
            continue
        m = _WEAK_HEAD.search(text)
        if not m:
            continue
        section = text[m.end():]
        nxt = re.search(r"^##\s", section, re.MULTILINE)
        if nxt:
            section = section[:nxt.start()]
        for line in section.splitlines():
            s = line.strip()
            if s.startswith("- ") and not s.startswith("- （"):
                item = s[2:].strip()
                if item:
                    out.append({
                        "subject": entry.parent.name,
                        "text": item,
                        "id": f"weak:{entry.parent.name}:{h8(item)}",
                    })
    return out


# ---- 来源 3：倒计时里程碑 ------------------------------------------------------

def countdown(t: Tutor, today: date) -> list[dict]:
    exam_s = t.config.get("exam_date")
    if not exam_s:
        return []
    try:
        days_left = (date.fromisoformat(exam_s) - today).days
    except ValueError:
        return []
    if days_left not in MILESTONES:
        return []
    if days_left == 0:
        text = "今天高考。正常发挥，你已经把该做的都做了。"
    else:
        text = f"距高考 {days_left} 天——要不要按当前计划校准一下本周节奏？"
    return [{"id": f"countdown:{days_left}", "text": text}]


# ---- 聚合 + 写入 ---------------------------------------------------------------

def review_link(t: Tutor, subject: str, topic: str) -> str:
    """从 topic 提取「题NN」找到对应笔记；找不到退回该科恢复入口。"""
    m = re.search(r"题(\d{1,3})", topic)
    sub_dir = t.vault / subject
    if m:
        for f in sorted(sub_dir.glob(f"题{m.group(1)}-*.md")):
            return f"（笔记：{f.stem}）"
    return "（见恢复入口）"


def collect(t: Tutor, today: date, state: dict[str, str]) -> list[dict]:
    items: list[dict] = []

    for r in due_reviews(t, today)[:2]:
        overdue = f"，超期 {r['overdue']} 天" if r["overdue"] else ""
        items.append({
            "id": f"review:{r['subject']}:{h8(r['topic'])}",
            "ephemeral": True,
            "text": f"{r['subject']}复习到期：{r['topic']}（{r['due']}{overdue}）{review_link(t, r['subject'], r['topic'])}",
        })

    for w in weak_points(t):
        if len(items) >= MAX_ITEMS:
            break
        if not fresh(state, w["id"], today):
            continue
        items.append({"id": w["id"], "text": f"{w['subject']}薄弱点待复测：{w['text'][:40]}"})

    for c in countdown(t, today):
        if len(items) >= MAX_ITEMS:
            break
        if not fresh(state, c["id"], today):
            continue
        items.append({"id": c["id"], "ephemeral": True, "text": c["text"]})

    return items


def render(items: list[dict]) -> str:
    lines = [SECTION_BEGIN, "## 今日递送", ""]
    if not items:
        lines.append("(今天没有待递送的内容，按恢复入口的「今天优先」走)")
    for it in items:
        # 行内带 ID，供埋点与周统计：- 递送：[ID] 文本 → 接受/忽略
        lines.append(f"- 递送候选：[{it['id']}] {it['text']}")
    lines.append(SECTION_END)
    return "\n".join(lines) + "\n"


def write_daily(t: Tutor, today: date, section: str) -> Path:
    f = t.daily / f"{today.isoformat()}.md"
    if f.exists():
        text = f.read_text(encoding="utf-8")
        pattern = re.compile(
            re.escape(SECTION_BEGIN) + r".*?" + re.escape(SECTION_END), re.DOTALL
        )
        # 孤儿小节兜底：begin 标记被外部改动丢失，只剩标题到下一个二级标题
        orphan = re.compile(r"^## 今日递送\s*$(?:(?!^## ).)*", re.MULTILINE | re.DOTALL)
        if pattern.search(text):
            text = pattern.sub(lambda _: section.rstrip("\n"), text)
        elif orphan.search(text):
            text = orphan.sub(lambda _: section.rstrip("\n"), text)
        else:
            text = text.rstrip("\n") + "\n\n" + section
    else:
        text = (
            f"---\ndate: {today.isoformat()}\n---\n\n"
            f"# {today.isoformat()}\n\n## 今日目标\n\n\n"
            + section
            + "\n## Agent 记录\n\n（教练会话流水与递送埋点写在这里）\n"
        )
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


def self_test() -> None:
    """回归断言：到期行/表头行/薄弱点解析/占位行过滤/倒计时里程碑。"""
    import tempfile

    today = date(2026, 9, 7)
    with tempfile.TemporaryDirectory() as td:
        t = Tutor(Path(td))
        sub = Path(td) / "数学"
        sub.mkdir()
        (sub / "00-恢复入口.md").write_text(
            "# 数学 · 恢复入口\n\n"
            "## 当前薄弱点（≤3 条）\n\n"
            "- 极值点偏移构造方向（卡过）\n"
            "- （只有被纠正且要再测的点才进来）\n\n"
            "## 复习队列\n\n"
            "| 知识点 | 上次复现 | 下次到期 | 反馈 |\n"
            "|---|---|---|---|\n"
            "| 隐零点设而不求（题03） | 2026-09-06 | 2026-09-07 | 卡 |\n"
            "| 切线设切点法（题01） | 2026-09-05 | 2026-09-08 | 一般 |\n"
            "| 未到期占位（题99） | 2026-09-01 | 2026-09-20 | — |\n",
            encoding="utf-8",
        )
        (Path(td) / "tutor.json").write_text(
            json.dumps({"exam_date": "2027-06-07"}), encoding="utf-8"
        )

        reviews = due_reviews(t, today)
        assert len(reviews) == 1, f"应只命中 1 条到期（隐零点），实得 {reviews}"
        assert reviews[0]["overdue"] == 0 and "隐零点" in reviews[0]["topic"]

        weaks = weak_points(t)
        assert len(weaks) == 1, f"应只命中 1 条薄弱点（过滤占位行），实得 {weaks}"
        assert weaks[0]["subject"] == "数学" and "极值点偏移" in weaks[0]["text"]

        # 2026-09-07 距 2027-06-07 为 273 天，不在里程碑里 → 无倒计时条目
        assert countdown(t, today) == []

        items = collect(t, today, {})
        assert len(items) == 2, f"聚合应为 2 条（复习+薄弱点），实得 {items}"
    print("self-test OK：到期/表头/占位/薄弱点/倒计时 分支全过")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", help="vault 根路径（缺省 = 脚本上级目录）")
    ap.add_argument("--date", help="覆盖今天（YYYY-MM-DD）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件、不记状态")
    ap.add_argument("--self-test", action="store_true", help="跑回归断言后退出")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    vault = Path(args.vault).resolve() if args.vault else Path(__file__).resolve().parent.parent
    today = date.fromisoformat(args.date) if args.date else date.today()
    t = Tutor(vault)
    state = {} if args.dry_run else load_state(t.state_file)
    items = collect(t, today, state)

    print(f"== 今日递送 {today}（vault: {vault}，{len(items)} 条） ==")
    for it in items:
        print(f"  [{it['id']}] {it['text']}")

    if args.dry_run:
        print("\n(dry-run，未写入)")
        return

    f = write_daily(t, today, render(items))
    # state 记目标日期（--date 覆盖值）：预生成后同日重跑，条目不被换掉
    now = today.isoformat()
    for it in items:
        if not it.get("ephemeral"):
            state[it["id"]] = now
    save_state(t.state_file, state)
    print(f"written: {f}")
    print(f"state: {t.state_file}（{len(state)} 条已递送）")


if __name__ == "__main__":
    main()
