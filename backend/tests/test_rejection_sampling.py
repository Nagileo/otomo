from __future__ import annotations

from otomo.eval.rejection_sampling import load_prompts


def test_load_prompts_from_txt(tmp_path):
    p = tmp_path / "prompts.txt"
    p.write_text("# comment\n推荐一部治愈番\n\n白色相簿2 的声优是谁\n", encoding="utf-8")
    assert load_prompts(p) == ["推荐一部治愈番", "白色相簿2 的声优是谁"]


def test_load_prompts_from_yaml_cases(tmp_path):
    p = tmp_path / "prompts.yaml"
    p.write_text("- id: a\n  question: 2026年7月有什么番\n- 解释一下孤独摇滚的梗\n", encoding="utf-8")
    assert load_prompts(p) == ["2026年7月有什么番", "解释一下孤独摇滚的梗"]
