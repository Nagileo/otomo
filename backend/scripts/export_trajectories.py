"""RL 轨迹导出：把部署期攒下的对话轨迹 + 👍👎 反馈，清洗成训练可用格式。

    python -m scripts.export_trajectories                       # 只看统计
    python -m scripts.export_trajectories --sft out/sft.jsonl   # SFT（messages 格式）
    python -m scripts.export_trajectories --sft out/sft.jsonl --only-rated   # 只要 👍 轮次
    python -m scripts.export_trajectories --dpo out/dpo.jsonl   # 偏好对（同问题 👍/👎 成对）

清洗：
- 文本级脱敏：email / Bearer token / URL 查询串里的 token|key|secret / webhook 地址；
- 默认剥掉 system prompt（策略模型训练时另配），--include-system 保留；
- 👎 轮次默认不进 SFT（它们是 DPO 的 rejected 原料）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otomo.config import settings  # noqa: E402

_SCRUBS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer <token>"),
    (re.compile(r"(?i)([?&](?:token|key|secret|access_token|api_key|password)=)[^&\s\"']+"), r"\1<redacted>"),
    (re.compile(r"https?://(?:discord(?:app)?\.com/api/webhooks|open\.feishu\.cn/open-apis/bot|sctapi\.ftqq\.com)/[^\s\"']+"), "<webhook>"),
]


def _scrub(text: str) -> str:
    for pat, rep in _SCRUBS:
        text = pat.sub(rep, text)
    return text


def _scrub_messages(messages: list[dict], include_system: bool) -> list[dict]:
    out = []
    for m in messages:
        if m.get("role") == "system" and not include_system:
            continue
        m = dict(m)
        if isinstance(m.get("content"), str):
            m["content"] = _scrub(m["content"])
        out.append(m)
    return out


def _traj_dir() -> Path:
    p = Path(settings.trajectory_dir)
    return p


def load_all() -> tuple[list[dict], dict[str, dict]]:
    d = _traj_dir()
    turns: list[dict] = []
    feedback: dict[str, dict] = {}
    if not d.exists():
        return turns, feedback
    for f in sorted(d.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if f.name == "feedback.jsonl":
                feedback[rec.get("turn_id", "")] = rec  # 同轮多次反馈，后者覆盖
            else:
                turns.append(rec)
    return turns, feedback


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sft", default="", help="SFT 输出路径（messages jsonl）")
    ap.add_argument("--dpo", default="", help="DPO 偏好对输出路径")
    ap.add_argument("--only-rated", action="store_true", help="SFT 只导出被 👍 的轮次")
    ap.add_argument("--include-system", action="store_true")
    ap.add_argument("--min-tools", type=int, default=0, help="至少调用过 N 个工具的轮次才导出")
    args = ap.parse_args()

    turns, feedback = load_all()
    rated = {t: f["rating"] for t, f in feedback.items()}
    ups = sum(1 for r in rated.values() if r == "up")
    downs = sum(1 for r in rated.values() if r == "down")
    with_tools = sum(1 for t in turns if t.get("tools_called"))
    print(f"轨迹 {len(turns)} 轮（含工具 {with_tools}）· 反馈 {len(rated)}（👍{ups} 👎{downs}）· 目录 {_traj_dir()}")

    if args.sft:
        out = Path(args.sft)
        out.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for t in turns:
                rating = rated.get(t.get("turn_id", ""))
                if rating == "down":
                    continue
                if args.only_rated and rating != "up":
                    continue
                if len(t.get("tools_called") or []) < args.min_tools:
                    continue
                if not t.get("final_answer"):
                    continue
                f.write(json.dumps({
                    "messages": _scrub_messages(t.get("messages") or [], args.include_system),
                    "meta": {
                        "turn_id": t.get("turn_id"), "ts": t.get("ts"), "rating": rating,
                        "tools_called": t.get("tools_called"), "usage_tokens": t.get("usage_tokens"),
                        "runner": t.get("runner"),
                    },
                }, ensure_ascii=False) + "\n")
                n += 1
        print(f"SFT → {out}（{n} 条）")

    if args.dpo:
        # 同一 session 内相同用户问题、一个 👍 一个 👎 → 偏好对（真实分布里天然稀少，够种子用）
        by_q: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for t in turns:
            key = (t.get("session_id", ""), (t.get("user_message") or "").strip())
            if key[1]:
                by_q[key].append(t)
        out = Path(args.dpo)
        out.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for (_sid, q), group in by_q.items():
                chosen = [t for t in group if rated.get(t.get("turn_id", "")) == "up" and t.get("final_answer")]
                rejected = [t for t in group if rated.get(t.get("turn_id", "")) == "down" and t.get("final_answer")]
                if not chosen or not rejected:
                    continue
                f.write(json.dumps({
                    "prompt": _scrub(q),
                    "chosen": _scrub(chosen[0]["final_answer"]),
                    "rejected": _scrub(rejected[0]["final_answer"]),
                    "meta": {"session_id": _sid, "chosen_turn": chosen[0].get("turn_id"), "rejected_turn": rejected[0].get("turn_id")},
                }, ensure_ascii=False) + "\n")
                n += 1
        print(f"DPO → {out}（{n} 对）")


if __name__ == "__main__":
    main()
