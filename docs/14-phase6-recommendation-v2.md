# 14 · Phase 6 设计稿 · Recommendation v2（aspect 情感画像 + 对话式推荐）

> 配套 docs/11 §Phase 6。目标：把推荐从"工具堆叠 + tag 画像"升级成"**aspect 情感画像驱动 + 对话式 agent**"。
> Phase 6 体量大，**分三批**落地，本稿把 v1 核心（ABSA + galgame 三源 + critiquing）写到可执行，v1.5/v2 给要点。

| 批次 | 内容 | 为什么这个顺序 |
|---|---|---|
| **v1（核心）** | ① ABSA 好球区/雷区（推荐内核）② galgame 三源融合解释 ③ critiquing 负反馈迭代 | 最有差异化、最对口你 MLLM/RL 主线 |
| **v1.5** | ④ 冷启动澄清 ⑤ 跨媒体推荐（动画→原作）| 补"无收藏用户"空白 + ACGN 特色 |
| **v2** | ⑥ comic/LN 分类 + music 特殊推荐 ⑦ 追番副驾 ⑧ 口味报告完整版 | 扩展面，依赖前面地基 |

---

## 0. 现状（已核对）

| 能力 | 现状 | 文件 |
|---|---|---|
| 推荐 pipeline | 多路召回(标签/图谱/CF/EGS) + 平衡打分 + enrich + 系列回溯 + **memory 避雷降权**(Phase 5) | [recommend/tool.py](../backend/otomo/tools/recommend/tool.py) |
| aspect 抽取 | **关键词级**：`review._ASPECT_HINTS` + `_comment_sentiment`；`analyze_user_opinions` 出 `aspect_summary` | [review/tool.py](../backend/otomo/tools/review/tool.py) / [user_analysis/tool.py](../backend/otomo/tools/user_analysis/tool.py) |
| memory | likes/dislikes/feedback/progress/**profile_snapshot**；可存可读可注入(Phase 5) | [memory/models.py](../backend/otomo/memory/models.py) |
| 追番副驾雏形 | `plan_watch_order`（系列图谱补番顺序）已存在 | [watchorder/](../backend/otomo/tools/watchorder/) |
| galgame 源 | recommend `_external_game_recall`(EGS 召回+映射)；review 对 game 查 EGS/VNDB | recommend / review |

> 结论：召回/打分/避雷/galgame 源都在，**aspect 还停在关键词级、且没驱动推荐**。Phase 6 v1 = 把 aspect 升到 LLM 级 + 建用户好球区 + 接进 Rerank/Explain。

---

## 1. 【v1 核心】ABSA 好球区/雷区 — 推荐内核

### 1.1 数据模型（扩 memory）— `memory/models.py`
```python
class AspectPreference(BaseModel):
    aspect: Literal["story","character","pacing","visual","music","direction","text","system","voice","general"]
    label: str                      # 中文：剧情/角色/作画…
    polarity: Literal["like","dislike"]
    weight: float = 0.5             # 该方面对用户的重要度（命中频次/强度聚合）
    evidence_count: int = 0
    sample: str = ""                # 一条代表性原话片段
    confidence: float = 0.5

class UserAspectProfile(BaseModel):
    username: str
    subject_type: str = "anime"
    likes: list[AspectPreference] = []      # 好球区：在意且正向（"作画""细腻情感"）
    dislikes: list[AspectPreference] = []    # 雷区：在意且负向（"拖沓""党争"）
    updated_at: str = ""
```
存进 `UserMemory` 加一字段 `aspect_profiles: dict[str, UserAspectProfile]`（按 subject_type），跨会话复用、前端可见。

### 1.2 抽取 — LLM 批量 ABSA（**你 MLLM 方向的发挥点**）
- 输入：用户**看过作品的私评**（`analyze_user_opinions` 已拿到 `all_comment_samples`：`作品名：短评`）。
- 一次 LLM 调用**批量**抽（控制成本，不是每条一次）：prompt 让模型输出 `[{subject, aspect, polarity, snippet}]` JSON。
- 聚合：按 aspect 统计正/负频次与强度 → `UserAspectProfile`（取 top-k 好球区/雷区，weight=频次归一，confidence 随样本量）。
- 关键认知（写进 prompt）：**一条评价对不同方面情感可相反**（"作画神但剧情拖"→ visual:like + story:dislike）；整体情感会丢信息，必须 aspect 级。
- 降级：LLM 不可用/无私评 → 回退现有关键词级 `_ASPECT_HINTS`（不阻塞）。

### 1.3 工具
- **新增 `build_aspect_profile`**（或扩 `analyze_user_opinions` 加 `aspect_profile` 输出）：跑 ABSA → 存 `memory.aspect_profiles[subject_type]` → 返回好球区/雷区。
- prompt 引导：推荐/"为什么适合我"前可调它；用户私评变化时刷新。

### 1.4 接入 recommend
- **Profile 步**：`run` 开头读 `memory.aspect_profiles`（和 Phase 5 的 dislikes 并列）。
- **Rerank 步**（在现有打分加两项，和 `memory_penalty` 同位置）：
  - `aspect_like_bonus`：候选命中好球区 → 加分（候选 aspect 来源见下）。
  - `aspect_dislike_penalty`：命中雷区 → 减分（比 tag 级避雷更细）。
  - 候选 aspect 来源（v1 务实，避免每候选都 review）：① 候选 `tags`（tag→aspect 粗映射，复用 `_ASPECT_HINTS` 反查）② 已 enrich 的 `review_consensus`/`aspect_summary`（recommend 已会补 review evidence）。精确 per-候选 ABSA 留 v2。
- **Explain 步**：推荐理由从「tag 命中」升级到「**aspect 命中**」——"你偏好『作画』，这部作画口碑强；你雷『党争』，这部没有"。这是可解释推荐，契合 source/confidence 主线。

### 1.5 验收
- `build_aspect_profile` 对有私评的用户产出好球区/雷区（"作画/剧情=like，拖沓/党争=dislike"）。
- `recommend_subjects` 解释里出现 aspect 级理由（不只 tag）。
- 命中雷区的候选被降权（在 dislikes tag 级之上更细）。

---

## 2. 【v1 核心】galgame 三源融合解释 — 招牌

现状：recommend 对 game 有 EGS 召回+映射、review 对 game 查 EGS/VNDB。Phase 6 把**解释做完整**（不是再接源）：

galgame 推荐/评价时，输出结构化三源对比：
- **Bangumi 中文圈**（评分/收藏锚点）· **EGS 日本 gal 圈**（中央値/data 数/排名）· **VNDB 国际 VN 圈**（评分/tag/发售）
- **内容&剧透风险**（R18/猎奇/长篇，结合 memory 雷区）· **是否冷门挖宝**（高分低人气）
- **source routing 标注**：哪个源命中、冲突时信谁（= docs/11 Phase 7 RL 的 source-routing reward 维度）

落地：`review_subject`（game 分支）已聚合三源，Phase 6 给它一个 galgame 专用的 `consensus` 模板 + 前端 ReviewEvidencePanel 三源并排对比（已有 ratings 卡，补"三圈层"分组）。

> **战略连接**：galgame 三源的"何时用哪源、信谁"决策，是你 RL 的最佳试验场——v1 先把决策**显式化为结构化输出**，Phase 7 再把它变成 reward。

---

## 3. 【v1 核心】critiquing 负反馈迭代 — agent 形态

让推荐可"对话式修正"（区别于传统推荐系统）：

- **会话记住上次推荐**：`state.short_term["last_recommend"]`（候选 + args）。
- **critiquing 解析**：用户说"换一批 / 要短的 / 不要这个画风 / 太致郁了 / 更冷门点" →
  - 映射到推荐参数：短→集数约束、冷门→`niche=true`、换一批→排除上次结果、画风/题材→加 dislikes（临时或写 memory）。
  - 区分**真/假负反馈**（[ReFINe] 思路）：明确"不要X"=真负（写 memory dislike）；"再换换"=探索（不写 memory，只本轮排除）。
- 实现：新增轻量 `refine_recommendation` 工具（或 recommend 加 `exclude_ids`/`constraints` 参数 + prompt 引导 agent 识别 critiquing）。
- 前端：推荐面板下加 critiquing chips（"换一批 / 要短的 / 更冷门 / 别这题材"），点击带 constraint 重发——复用 Phase 3 的 chips 模式。

> **与 RL 连接**：critiquing→修正 是多轮偏好对齐，可做 Actor-Critic/DPO 的训练信号（docs/11 Phase 7 已列）。

---

## 4. 【v1.5】冷启动澄清 + 跨媒体推荐

- **冷启动澄清**：用户无收藏/收藏少（`get_taste_profile` 返回稀疏）→ agent 用**少量澄清问题**建临时画像（"最近看的/喜欢的类型/想要的氛围"），写进 memory（低置信）。**克制**：少问、问对、可跳过（PrefDisco 警示 29% 澄清反效果）。前端用 followup chips 承载。
- **跨媒体推荐**：喜欢某动画 → 推原作漫画/轻小说/galgame/音乐。复用 `get_subject_relations`（跨 type 关系，recommend 已有系列回溯的图谱基础）。`recommend_subjects` 加 `cross_media=true` 或新工具。

---

## 5. 【v2】分媒介 + 追番副驾 + 口味报告

- **comic / light novel 分类**：Bangumi book 混合 → 先按 tags/title 分 comic/LN/novel，再分别推荐（漫画看作者/连载/动画化；LN 看改编/卷数/坑不坑）。
- **music 特殊推荐**：不照搬评分逻辑——分 ACG 音乐条目（OP/ED/角色歌/OST/声优歌手）+ MusicBrainz 元数据；理由按"你喜欢的作品/声优相关、同风格、情绪场景"。**不抓乐评**。
- **追番副驾**：扩 `plan_watch_order` → 补番顺序 + 智能排期（从想看里推"这周看哪 3 部"）+ 追番情报（在追的更新/高能集）+ 搁置盘活。Bangumi 给数据、Otomo 给决策。
- **口味报告完整版**：Phase 1 人格卡 + 本 Phase aspect 好球区/雷区 → 可分享「年度总结」图。

---

## 6. 五步 pipeline 全景（现状 → Phase 6）

```
1. User Profile     [现状] 收藏+tag画像+memory偏好/避雷
                    [+v1]  aspect 好球区/雷区(ABSA)   [+v1.5] 冷启动澄清
2. Candidate Recall  [现状] 标签/图谱/CF/外部榜单
                    [+v1.5] 跨媒体召回
3. Evidence Enrich   [现状] Bangumi评分/EGS/VNDB/AniList/yuc/review
                    [+v1]  galgame 三源结构化
4. Rerank            [现状] 偏好匹配+质量+新颖度+memory避雷
                    [+v1]  aspect 好球区加分/雷区惩罚   [+v1]  critiquing 约束
5. Explanation       [现状] tag 命中/quality_badges
                    [+v1]  aspect 命中解释 + galgame 三源 + source routing 标注
```

---

## 7. 落地顺序 + 验收 + eval

**顺序（v1 核心先）**
1. `models.py` 加 `AspectPreference`/`UserAspectProfile` + memory 存取 → **单测**（聚合逻辑纯函数）。
2. `build_aspect_profile` 工具（LLM 批量 ABSA + 降级）。
3. recommend 接 aspect（Profile 读 + Rerank 加权 + Explain 升级）。
4. galgame 三源解释模板 + 前端三圈层分组。
5. critiquing（recommend constraints/exclude + 前端 chips）。
6. v1.5/v2 按批推进。

**eval（加进 golden_cases.yaml）**
```yaml
- id: rec_aspect_explain
  question: 根据我的口味推荐动画，并说说为什么适合我。
  expect_tools: ["recommend_subjects"]
  expect_panels: ["recommend_subjects"]
  note: 解释应出现 aspect 级理由（不只 tag）
- id: rec_galgame_three_source
  question: 推荐几部高口碑 galgame，Bangumi、批判空间、VNDB 都参考一下。
  expect_tools: ["recommend_subjects"]
  note: 应出现 galgame 三源对比解释
- id: rec_critique_shorter
  question: 这些太长了，换几部短一点的。
  note: 应识别 critiquing → 加集数/niche 约束重推（多轮，承接上一条）
```

**验收**：aspect 好球区驱动推荐解释；galgame 三源对比可见；critiquing 能修正结果；命中雷区降权。

---

## 8. 与 Agentic-RL 的连接（Phase 7 入口）
Phase 6 每个 v1 子项都产出**可评测信号** = RL reward 原料：
- **aspect 抽取准不准**（人工/弱标注）→ 抽取 reward。
- **aspect 好球区匹配**（推荐命中用户在意的方面）→ 推荐 reward 一维。
- **source routing 合理命中**（galgame 三源信谁）→ source-routing reward。
- **多轮 critiquing 对齐**（修正后更符合用户）→ Actor-Critic/DPO 信号。

> 这就是为什么 Phase 6 是"产品功能和后训练咬合最紧"的一块——做完它，Phase 7 的 reward 维度基本就备齐了。

---

## 9. 边界 / 不做
- v1 不做 per-候选精确 ABSA（每候选 review 太慢）——用 tags+已有 review evidence 近似，精确留 v2。
- aspect 抽取是**弱信号**：带 source+confidence，推断雷区低置信、可被 memory 显式覆盖。
- critiquing 的"探索性换一批"不写 memory（只本轮排除），只有明确"不要X"才写 dislike。
- music 不抓网易云/QQ 乐评（成本风险）。
- LLM ABSA 控制成本：批量抽 + 缓存进 memory，不每次推荐重抽。
