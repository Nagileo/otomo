"""轻互动域：今日番签（omikuji）+ ACGN 答题（quiz）。点子池转正（用户拍板）。

设计要点：
- 番签**确定性**：同一用户同一天抽到同一签（hash(username+date) 做种子）——"签"的仪式感
  来自每天只有一次命运，刷新不换签。
- quiz 的答案**只进面板 payload、绝不进正文**：前端 QuizPanel 客户端判分，
  LLM 出题后正文只说"来答题吧"，否则出题即剧透。
"""
from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from ...agent.contracts import Citation, Tool, ToolResult
from ..bangumi.client import BangumiClient

# 匿名/空想看列表的兜底池（公认经典，id 稳定）
_CLASSIC_POOL: list[tuple[int, str]] = [
    (253, "新世纪福音战士"), (326, "白色相簿2"), (876, "CLANNAD"), (1428, "轻音少女"),
    (2585, "命运石之门"), (9717, "魔法少女小圆"), (10380, "冰菓"), (37460, "月色真美"),
    (110467, "紫罗兰永恒花园"), (140001, "莉可丽丝"), (183878, "少女歌剧"), (208908, "孤独摇滚！"),
    (219200, "葬送的芙莉莲"), (285776, "摇曳露营△"), (28900, "白箱"), (78405, "四月是你的谎言"),
]

_FORTUNES = ["大吉", "中吉", "吉", "小吉", "末吉"]
_ADVICE = {
    "大吉": ["宜：今晚就开坑，一口气三集不亏", "宜：安利给朋友，今天说服力 +30%"],
    "中吉": ["宜：先看第一集试试水温", "忌：睡前开虐番"],
    "吉": ["宜：配着晚饭看，下饭指数合格", "宜：顺手标个「在看」"],
    "小吉": ["宜：先收藏，周末再开", "忌：跳 OP——今天的 OP 有惊喜"],
    "末吉": ["宜：重温一部老番回血", "忌：今天开长篇大坑"],
}


class OmikujiArgs(BaseModel):
    username: str | None = Field(None, description="不传用当前账号；匿名用经典池")


class OmikujiResult(BaseModel):
    date: str
    fortune: str
    subject_id: int
    subject_name: str
    image: str | None = None
    from_pool: str = "wishlist"     # wishlist=你的想看 / classics=经典池
    advice: list[str] = Field(default_factory=list)
    lucky_tag: str = ""
    caveats: list[str] = Field(default_factory=list)


class AnimeOmikujiTool(Tool):
    name = "anime_omikuji"
    description = (
        "今日番签：每天从你的想看列表（匿名用经典池）确定性抽一部「今日之番」+ 运势签文。"
        "同一天重复抽结果不变（签的仪式感）。用户说'抽签/今日番签/今天看什么听天由命'时用。"
    )
    args_model = OmikujiArgs
    result_model = OmikujiResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: OmikujiArgs) -> ToolResult[OmikujiResult]:
        username = args.username
        if not username:
            try:
                me = await self.client.get_me()
                username = me.get("username") or str(me.get("id"))
            except Exception:  # noqa: BLE001
                username = "guest"
        today = date.today().isoformat()
        seed = int(hashlib.sha256(f"{username}:{today}".encode()).hexdigest()[:12], 16)
        rng = random.Random(seed)

        pool: list[dict[str, Any]] = []
        from_pool = "classics"
        if username != "guest":
            try:
                items = await self.client.get_all_user_collections(username, 2, 1, max_items=400)
                pool = [it["subject"] for it in items if it.get("subject", {}).get("id")]
                if pool:
                    from_pool = "wishlist"
            except Exception:  # noqa: BLE001
                pool = []
        if not pool:
            pool = [{"id": sid, "name_cn": name} for sid, name in _CLASSIC_POOL]
            from_pool = "classics"

        pick = rng.choice(pool)
        fortune = rng.choices(_FORTUNES, weights=[15, 25, 30, 20, 10])[0]
        tags = [t.get("name") for t in pick.get("tags") or [] if isinstance(t, dict) and t.get("name")]
        lucky = rng.choice(tags) if tags else rng.choice(["治愈", "百合", "热血", "日常", "音乐"])
        images = pick.get("images") or {}
        return ToolResult(
            ok=True,
            data=OmikujiResult(
                date=today,
                fortune=fortune,
                subject_id=int(pick["id"]),
                subject_name=pick.get("name_cn") or pick.get("name") or "神秘作品",
                image=images.get("large") or images.get("common") or images.get("grid"),
                from_pool=from_pool,
                advice=list(_ADVICE[fortune]),
                lucky_tag=lucky,
                caveats=["同一天重复抽签结果不变；想看列表为空时用经典池。"],
            ),
            sources=[Citation(title=pick.get("name_cn") or pick.get("name") or "subject",
                              url=f"https://bgm.tv/subject/{pick['id']}", source="bangumi")],
        )


class QuizArgs(BaseModel):
    count: int = Field(5, ge=3, le=10, description="题目数量")
    username: str | None = Field(None, description="不传用当前账号；题目出自你看过的作品（答得出）")


class QuizQuestion(BaseModel):
    q: str
    options: list[str]
    answer_index: int          # 只进面板 payload；正文绝不写答案
    explain: str = ""


class QuizResult(BaseModel):
    source: str = "my_watched"     # my_watched / classics
    questions: list[QuizQuestion] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def _year_question(rng: random.Random, name: str, year: int) -> QuizQuestion:
    offsets = rng.sample([-3, -2, -1, 1, 2, 3], 3)
    options = [str(year)] + [str(year + o) for o in offsets]
    rng.shuffle(options)
    return QuizQuestion(
        q=f"《{name}》首播于哪一年？",
        options=options,
        answer_index=options.index(str(year)),
        explain=f"《{name}》首播于 {year} 年。",
    )


def _studio_question(rng: random.Random, name: str, studio: str, distractors: list[str]) -> QuizQuestion:
    options = [studio] + rng.sample(distractors, 3)
    rng.shuffle(options)
    return QuizQuestion(
        q=f"《{name}》的动画制作公司是？",
        options=options,
        answer_index=options.index(studio),
        explain=f"《{name}》由 {studio} 制作。",
    )


class AcgnQuizTool(Tool):
    name = "generate_acgn_quiz"
    description = (
        "从你看过的作品出一组 ACGN 选择题（首播年份/制作公司），答案藏在面板里由前端判分。"
        "用户说'考考我/出题/quiz/答题'时用。出题后正文只邀请答题，绝不透露任何答案。"
    )
    args_model = QuizArgs
    result_model = QuizResult

    def __init__(self, client: BangumiClient) -> None:
        self.client = client

    async def run(self, args: QuizArgs) -> ToolResult[QuizResult]:
        username = args.username
        source = "classics"
        subjects: list[dict[str, Any]] = []
        if not username:
            try:
                me = await self.client.get_me()
                username = me.get("username") or str(me.get("id"))
            except Exception:  # noqa: BLE001
                username = None
        if username:
            try:
                items = await self.client.get_all_user_collections(username, 2, 2, max_items=600)
                subjects = [it["subject"] for it in items if it.get("subject", {}).get("id")]
                if len(subjects) >= 8:
                    source = "my_watched"
            except Exception:  # noqa: BLE001
                subjects = []
        if source == "classics":
            got = []
            for sid, _name in _CLASSIC_POOL[:12]:
                try:
                    got.append(await self.client.get_subject(sid))
                except Exception:  # noqa: BLE001
                    continue
            subjects = got

        rng = random.Random()  # quiz 每次都出新题（和番签相反，重复可玩）
        rng.shuffle(subjects)
        # 制作公司候选：抽样一部分拉 persons，取"动画制作"
        studios: dict[int, str] = {}
        for subj in subjects[: min(14, len(subjects))]:
            sid = int(subj["id"])
            try:
                persons = await self.client.get_subject_persons(sid)
            except Exception:  # noqa: BLE001
                continue
            for person in persons or []:
                if str(person.get("relation") or "") == "动画制作" and person.get("name"):
                    studios[sid] = str(person["name"])
                    break

        questions: list[QuizQuestion] = []
        studio_names = list(dict.fromkeys(studios.values()))
        used: set[int] = set()
        for subj in subjects:
            if len(questions) >= args.count:
                break
            sid = int(subj["id"])
            if sid in used:
                continue
            name = subj.get("name_cn") or subj.get("name") or ""
            date_str = str(subj.get("date") or "")
            # 交替出题型；工作室题需要 ≥4 个不同公司
            if sid in studios and len(studio_names) >= 4 and len(questions) % 2 == 1:
                distractors = [x for x in studio_names if x != studios[sid]]
                if len(distractors) >= 3:
                    questions.append(_studio_question(rng, name, studios[sid], distractors))
                    used.add(sid)
                    continue
            if len(date_str) >= 4 and date_str[:4].isdigit():
                questions.append(_year_question(rng, name, int(date_str[:4])))
                used.add(sid)
        if len(questions) < 3:
            return ToolResult(ok=False, error="可出题的素材不足（收藏太少或元数据缺失）")
        return ToolResult(
            ok=True,
            data=QuizResult(
                source=source,
                questions=questions[: args.count],
                caveats=["答案在面板内由前端判分；正文不含答案。"],
            ),
            sources=[Citation(title="Bangumi 图谱出题", url="https://bgm.tv", source="bangumi")],
        )


def build_fun_tools(client: BangumiClient) -> list[Tool]:
    return [AnimeOmikujiTool(client), AcgnQuizTool(client)]
