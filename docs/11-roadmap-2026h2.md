# 11 · 产品与后训练路线（2026 H2 主线）

> 来源：综合 **2026-06-28** 用户与朋友的方案讨论 + 联网对标（AG-UI / Mem0 / ABSA / Video-RAG / CRS）+ 本轮代码审查后的判断。
> 承接 docs/07（旧路线，已被本文取代为当前主线）、docs/10（Source Router 与分集维度）。
> 定位不变：**算法为矛、工程为盾**——前端让能力"可见"，eval/verifier/RL 是简历内核，二者都服务于 2027 求职（Agent 平台基建 + 后训练）。

---

## 0. 定位与贯穿主线

### 0.1 一句话定位
**Otomo 是一个对每条信息都标注「来源」与「置信度」的 ACGN 知识图谱 Agent。**
（source-aware + confidence-aware 是项目的统一哲学，不是某个功能。）

### 0.2 贯穿主线：来源 / 置信度认识论
项目已经事实上贯穿了同一套认识论，本路线把它**显式命名**，后续每个功能都是它的实例：

| 体现处 | "高置信 / 真值" | "低置信 / 需标注" |
|---|---|---|
| Memory（Phase 5） | `explicit_user` 用户明说 | `derived_from_feedback` 推断偏好 |
| aspect 情感画像（Phase 6） | 用户明评的方面情感 | LLM 推断的好球区/雷区（低置信） |
| critiquing 负反馈（Phase 6） | 确凿负信号 | 打分习惯/噪声导致的假负反馈 |
| 图谱 Verifier（Phase 7） | 真值集合命中 | 幻觉 vs 真值外的真实 |
| review_subject | `source_matrix` 命中源 | `confidence` / `hidden_for_spoiler` |
| recommend | `quality_badges` 实证 | `explicit_tag_matches` 空=画像邻近补充 |
| EGS/yuc 映射（Phase 4） | `mapping_confidence≥0.85` | 弱匹配"制作公司需谨慎引用" |

> 面试叙事：讲这条主线 = 把"一堆工具"升维成"一个有设计哲学的系统"，比讲任何单功能都强。

### 0.3 现状快照（本轮已验证，不是臆测）
- **已落地**：Bangumi 图谱工具全套、画像、多路召回推荐（标签/图谱/CF/EGS 前置召回）、review 评价融合（Bangumi+EGS+VNDB+AniList+MusicBrainz、aspect 维度褒贬、source_matrix）、季番分诊 season_guide_brief、yuc 导视、B站导视搜索+评论、好友同步率/私评/弃坑分析、spoiler 政策判定、RAG 第二刀。28 测试通过、6 个外部抓取实测全通。
- **关键事实纠正（影响 Phase 2 范围）**：`BangumiClient._get` 已走 `_TTLCache`——subject/收藏/episodes **早已缓存**；真正裸奔的只有不走 client 的外部源（EGS/yuc/B站/好友页 HTML）。
- **缺口**：`ObservationEvent` 只透 `summary/sources/entities`，**typed `data` 没给前端**（但 `to_observation()` 已把它 dump 给 LLM，数据在管道里）；`LongTermMemory` 是 namespace/key→JSON stub；`profile/tool.py` 构造接了 `ltm` 但**连存都没存**；推荐对**无收藏的冷启动用户**还没有路径。

### 0.4 外部对标（联网结论，指导设计）
- **AG-UI 协议**（CopilotKit 2025，LangGraph/Pydantic AI/Mastra + MS/Oracle 采用）：事件分 5 类——Lifecycle / Text / Tool Call / **State Management** / Special。**Otomo 的 `AgentEvent` 已是其雏形，唯一缺 State Management 类**（`STATE_SNAPSHOT`/`STATE_DELTA`）。Phase 1/3/5 本质都在补这一类。
- **Mem0**（2025 生产级长期记忆）：核心是"LLM 抽**结构化** facts 而非存 raw chunk" + 分层 scope + **冲突时 ADD/UPDATE/DELETE/NOOP self-edit 而非 append**。Otomo 的 memory 方案对标了前两点，**缺第三点（consolidation）**。
- **ABSA（aspect-based sentiment analysis）**：成熟方向，抽 (aspect, sentiment) 对乃至 ACOS 四元组；关键认知"一条评价对不同方面情感可相反，整体情感丢信息，必须 aspect 级"。支撑 Phase 6 内核升级。
- **Video-RAG（NeurIPS 2025）**：视频理解不必硬啃视觉帧，用 ASR/OCR 抽文本语料接 RAG。支撑 Phase 4 的 B站视频内容化。
- **对话式推荐（CRS）**：LLM CRS 用**澄清问题做 preference elicitation + critiquing 修正**，解决冷启动与"用户不会表达需求"；但澄清需**克制**（PrefDisco：29% 澄清反而变差）。critiquing→Actor-Critic/DPO（T-PRA, ACL 2025）直通 RL。支撑 Phase 6 的对话式交互、Phase 7 的多轮偏好优化。

---

## 1. 排序总览

```
Phase 1   结构化 state 事件协议(对标 AG-UI) + 前端三证据面板 + 角色声优探索/口味卡  ┐ 并行起步
Phase 1.5 eval/cases.jsonl 起步(先 5 个,之后每过一个 Phase +3~5)                      ┘
Phase 2   外部源 TTL 缓存(复用 client._TTLCache 抽装饰器,~半天)
Phase 3   剧透状态产品化(= 一个 AG-UI state event 实例)
Phase 4   EGS/yuc 映射可观测 + B站视频 ASR 语料(外部源深化)
Phase 5   Memory v1 + consolidation(ADD/UPDATE/DELETE/NOOP)
Phase 6   Recommendation v2(galgame 优先,分媒介) + aspect 情感画像 + 对话式交互 + 追番副驾 + 口味报告
Phase 7   图谱 Verifier / benchmark / Agentic-RL + 多模态识番/GraphRAG
```

**相对你们原方案的三处实质调整**（其余完全沿用）：
1. **Eval 提前**到 1.5，与 Phase 1 并行——它是 RL 的 environment+reward 雏形，不是测试。
2. **Phase 1 明确对标 AG-UI 的 State Management 事件**，而非临时给 ObservationEvent 加字段。
3. **Phase 5 memory 补 consolidation**，而非纯 append。

> 原独立的"远期能力"与"情感画像升级"已按提前价值**下放进各 Phase**（见各 Phase 内 ⊕ 标记），全局映射见 §5 速查表。

---

## 2. 各 Phase 详述

### Phase 1 · 结构化 state 事件协议 + 前端证据面板
**目标**：让后端已返回的结构化证据在前端"可见"，并把它做成一套对标 AG-UI 的 typed 事件协议。

**现状缺口**：`ObservationEvent` 不带 typed payload；前端 [page.tsx](../frontend/app/page.tsx) 只渲染 markdown/source cards/trace/followup。

**范围**
- 做：给 `ObservationEvent` 增加 `data`（typed result 透出）；新增一类 **`StateEvent`**（`snapshot`/`delta`，承载 spoiler/memory/结构化证据）；前端按 `tool name` 分发到证据组件。
- 不做：不引入 AG-UI 的库依赖（借鉴其事件分类即可，保持手搓契约可控）；不重写 runner。

**关键设计 — AG-UI 事件映射**
| AG-UI 类 | Otomo 现状 | Phase 1 动作 |
|---|---|---|
| Lifecycle | 隐含于 stream | 可选显式 run_started/finished |
| Text | `AnswerDeltaEvent` | 已有 ✓ |
| Tool Call | `ToolCallEvent` + `ObservationEvent` | **补 typed `data` payload** |
| **State** | ✗ | **新增 `StateEvent`：spoiler 模式 / memory 注入 / 结构化证据** |

**三个证据面板（先做）**
- `ReviewEvidencePanel` ← `review_subject`：各源评分卡（Bangumi/EGS/VNDB/AniList/MusicBrainz）+ 样本量 + `confidence`；`aspect_summary`（剧情/角色/节奏/作画正负分歧）；剧透风险标识。
- `TasteAffinityPanel` ← `compare_user_taste`：`rating_/collection_/user_space_/peer_space_/extreme_similarity` + 共同高分/低分/最大分歧 + `confidence_reasons`。
- `SeasonGuidePanel` ← `season_guide_brief`：作品卡(Bangumi 评分 + yuc 放送/PV/官网) + `fit/reason/evidence` + B站导视入口 + 可选 `guide_comment_digests`。
- （扩展）`RecommendPanel` ← `recommend_subjects`：`reasons/evidence/external_mappings/quality_badges/notes`。

**⊕ 探索视图 + 可分享产出（自原远期提前——都是"已有结构化数据的可视化"，与面板同源、可一起做）**
- `CharacterVoiceExplorer` ← 图谱（`get_subject_characters`/`get_character_persons`/`get_person_subjects`）：声优出演网络、角色关系图、"这个 CV 还配过哪些高分作"的漫游。
- **口味报告卡（基础版）** ← `get_taste_profile`：把画像渲成「二次元人格卡」、可导出分享（小红书式），传播 + demo 友好；aspect 好球区增强随 Phase 6。

**产出 & 验收**：四类工具的结构化结果在前端成卡片；弱匹配/低置信在 UI 上可见（不被 LLM 文本掩埋）。

**配套 eval**：`如何评价装甲恶鬼村正？`（应出 review 三源卡 + aspect）；`我和某用户口味像不像？`（应出 affinity 报告含 confidence_reasons）。

---

### Phase 1.5 · Eval Harness 起步（与 Phase 1 并行）
**目标**：建立 `eval/cases.jsonl`，把"可评测"从一开始就长在项目里。这是通向 Phase 7 RL 的地基。

**每个 case 的 schema**
```json
{
  "query": "...",
  "should_call": ["season_guide_brief"],
  "should_not_call": ["web_search"],
  "verifiable_facts": ["制作公司=...", "声优=..."],
  "spoiler_risk": "none|mild|full",
  "expected_sources": ["bangumi", "yuc"]
}
```

**首批 5 个（之后每过一个 Phase 加 3~5）**
1. `2026年7月有什么番适合我？` → season_guide_brief，不应 web_search
2. `如何评价装甲恶鬼村正？` → review_subject（game→EGS/VNDB），事实可验证
3. `按我的好友推荐动画` → sync_user_recommendations(auto_friends)
4. `我看到第5集，后面别剧透，第5集大家怎么看？` → spoiler 边界 + 分集讨论，spoiler_risk 受控
5. `推荐治愈 galgame，Bangumi 和批判空间都参考` → recommend(game)+EGS

**验收**：每个 case 能跑出"调用了哪些工具 / 命中哪些源 / 是否越界剧透"，可人工/脚本判分。

> 维度（source routing 命中、剧透不越界、事实可验证）= 后续 RL reward 的直接原料。

---

### Phase 2 · 外部源 TTL 缓存
**目标**：稳 demo、降外部压力、可讲"限流/延迟/成本控制"。

**范围（已缩小）**：Bangumi 侧已缓存，**只需把 `client._TTLCache` 抽成通用 async 装饰器**，套到 4 个外部源：`egs_rank/search`、`yuc_season`、`bili_search/comments`、`bangumi_friends_html`。先内存 TTL 或文件缓存，上线前再换 Redis。
**合规依据**：Bangumi 官方要求明确 UA、走 v0 API（[api 文档](https://bangumi.github.io/api/) / [仓库](https://github.com/bangumi/api)）——缓存即"对请求行为负责"。
**验收**：重复查询命中缓存；外部源抖动时 demo 不卡死。

---

### Phase 3 · 剧透状态产品化
**目标**：把已有的后端剧透能力做成**显式产品能力**（ACGN Agent 的领域特色）。

**现状**：后端已有 `AgentState.short_term["spoiler"]`、"看到第N集"识别、`get_episode_comments` 硬过滤、API 可传 `spoiler_mode/progress_episode`；**前端不显示、不确认**。

**范围**：对话顶部显示 `spoiler_mode` + "当前进度：第 N 集"（作为一个 `StateEvent` 实例）；问结局/反转时弹 followup chips（无剧透/轻微/完整）；回答来源旁标"已按第 N 集过滤"。
**设计原则**：剧透是**不可逆高风险动作**，只能**显式确认**（followup chips），**不能用情感分析等弱信号隐式推断**——这是 0.2 主线的硬约束。长期剧透偏好持久化到 Phase 5 memory。
**配套 eval**：复用 1.5 的 case 4，加"用户明确允许剧透后可展开"反例。

---

### Phase 4 · EGS / yuc 映射可观测 + 外部源深化
**目标**：减少错配、不把弱匹配当强事实；并把外链类外部源从"只导航"升级为"读内容"。

**范围（映射可观测）**：EGS 补 `mapping_warnings`（失败/冲突候选也暴露，不只成功的 `external_mappings`）；yuc 把 `match_confidence/matched_by` 透到前端；**弱匹配时 UI 与 prompt 都不强说制作公司**（已在 yuc 修复里埋了"弱匹配需谨慎引用"，此处让它可见）。

**⊕ B站视频 ASR 语料（自原远期提前——务实、轻量）**：把 B站导视/漫评从"只外链"升级为"读内容"——对标 **Video-RAG**（NeurIPS 2025），用 **ASR(口播转文字)+OCR(字幕/标题)** 抽"UP 主说了什么"成文本语料接进 RAG（比硬啃视觉帧便宜、对解说类有效）；配合"具体 BV 优先 + 搜索页兜底"的外链降级（`_bili_json` 守卫触发时退回永远有效的搜索页）。**重型的识番(VLM)/帧检索留 Phase 7。**

**验收**：前端能看到映射置信度、错配可被发现；B站视频能产出可检索的解说语料。

---

### Phase 5 · Memory v1（显式结构化 + consolidation）
**目标**：长期记住软偏好/避雷/剧透偏好/推荐反馈——Bangumi 收藏负责"看过什么"，Otomo memory 负责"对话中的软偏好"。**先结构化 JSON，不上向量库。**

**数据结构**
```json
{
  "username": "Nagileo",
  "preferences": {
    "likes": ["百合", "日常", "治愈"],
    "dislikes": ["强党争", "重口", "胃痛"],
    "spoiler_default": "none"
  },
  "progress": { "摇曳露营": { "episode": 5 } },
  "feedback": [
    { "subject_id": 253, "signal": "like", "note": "喜欢户外治愈感",
      "source": "explicit_user", "confidence": 0.9, "ts": "..." }
  ],
  "affinity_cache": { "peer_username": { "...": "..." } },
  "profile_snapshot": { "top_tags": ["...日"] }
}
```
> 每条记忆带 **`source`**（`explicit_user` / `bangumi_profile` / `derived_from_feedback`）+ **`confidence`**——推断的偏好低置信，不当事实。这是 0.2 主线在 memory 的实例。

**工具**：`get_user_memory` / `remember_user_preference` / `forget_user_memory` / `record_recommendation_feedback`。

**关键设计 — consolidation（对标 Mem0，你们方案缺的一环）**
`remember_user_preference` **不直接 append**：提取新信号 → 与已有比对 → 决策 **ADD / UPDATE / DELETE / NOOP**。
例："喜欢百合" 后又 "最近不想看百合" ⇒ 不是两条矛盾记录，而是 UPDATE/移到 dislikes 或降权。与 `forget` 配套。

**接入 Agent**：每轮开始把 memory 注入 system prompt（"长期偏好：百合/日常；默认无剧透；最近避雷：党争"，作为一个 `StateEvent`）；用户出强信号时写回。

**边界/红线**：只记 ACGN 推荐/评价相关；不偷记敏感信息；必须可 `forget`；推断偏好低置信。

**配套 eval**：`别再推党争番` → 写入 dislikes 且后续推荐避开；`这个推荐不错` → record feedback。

---

### Phase 6 · Recommendation v2（galgame 优先，分媒介）
**目标**：把现有推荐从"工具堆叠"重构成清晰 pipeline——**不推倒重写**。同时从"推荐系统"升级为"推荐 **agent**"（可澄清、可 critiquing、可主动、可学习）。

**⊕ 内核升级：从 tag 画像到 aspect 情感画像（推荐 v2 的核心方向，自原 §5 并入）**
> 来源：用户 2026-06-28 强调"文字情感分析非常重要，从用户对各作品的评价里得到好球区/雷区，不局限于番"。这是 Phase 6 的**核心支柱**，不是边角。
- **问题**：现状**以 tag 为主**（标签召回 + `top_tags` 加权）太粗——只知喜欢「百合」标签，不知喜欢百合的**什么**，也不知给某百合番打低分**是雷了「党争」而非「百合」本身**。
- **升级**：从评价**文字**做 **aspect 级情感分析（ABSA）** 建模「好球区/雷区」，不局限 anime，galgame/novel/comic/music 同理。
- **学术对标**：ABSA 抽 (aspect, sentiment) 对乃至 **ACOS 四元组**；关键认知"一条评价对不同方面情感可相反（作画神但剧情拖）——整体情感丢信息，必须 aspect 级"；与推荐结合构造 **user-aspect sentiment matrix**（ANR/FSER/ABSA+CF），天然可解释，契合 source/confidence 主线。
- **Otomo 衔接**：已有 `review._ASPECT_HINTS`/`aspect_summary` + `analyze_user_opinions` 私评情感，但**关键词级弱信号、且没接进打分**。升级路径：① 关键词级 `_sentiment` → **LLM 级 ABSA**（**正是你 MLLM/后训练发挥点**，可做成可评测任务）② 建 user-aspect 好球区/雷区表 ③ 接进 Profile+Rerank（命中加分/雷区惩罚）④ 解释从「tag 命中」升到「aspect 命中」。
- **与 RL 连接**：aspect 抽取准不准 = 可评测；好球区 = 细粒度偏好表示，可作推荐 reward 一部分。
- **红线**：从公开/授权评价抽的**弱信号**，带 source+confidence，推断雷区低置信、可被用户显式覆盖（接 memory）。

**五步 pipeline**
```
1. User Profile     Bangumi 收藏 + aspect 好球区/雷区 + memory 偏好/避雷（无收藏→对话式冷启动）
2. Candidate Recall  标签 / 图谱 / 好友同步 / 外部榜单 / 跨媒体
3. Evidence Enrich   Bangumi 评分 / EGS / VNDB / AniList / MusicBrainz / yuc / B站
4. Rerank            偏好匹配(tag+aspect) + 质量证据 + 新颖度 + 好友支持 + 避雷惩罚(memory)
5. Explanation       为什么推荐 / 风险 / 来源 / 置信度（支持 critiquing 迭代）
```

**分媒介优先级与策略**（galgame > anime > comic/LN > music）
- **galgame（招牌，最高优先）**：Bangumi game 画像/收藏 + EGS（中央值/data 数/排行）+ VNDB（别名/发售日/国际评分/tag）+ memory 避雷（R18/胃痛/猎奇/长篇）。输出：为什么适合你 / 中文圈口碑 / 日本 gal 圈 / 国际 VN 圈 / 内容&剧透风险 / 是否冷门挖宝。
  > **战略连接**：三源融合的"何时用哪源、冲突信谁" = docs/10 的 source routing reward 维度。**galgame 推荐 = source routing 的最佳试验场，直通 Phase 7 RL**。
- **anime**：重点不是再接源，而是把**解释**做强——"你为什么会喜欢它 / 为什么可能不适合 / 要不要等完结"。staff/company 图谱召回 + 好友同步 + 季番导视 + 防剧透入坑 + "冷门高分"/"换口味"模式。同步率召回可补一维 **评分差/MSE 严格度**（社区 "Your Angle" 用余弦、"同步率的徒劳"提 MSE）：余弦看方向、MSE 看打分严格度，互补。
- **comic / light novel**：先解决**分类**（Bangumi book 混合漫画/小说/轻小说）→ book + tags/title → comic/LN/novel；再做跨媒体（喜欢动画→原作漫画/轻小说）。
- **music**：不照搬动画评分逻辑。分 ACG 音乐条目推荐（OP/ED/角色歌/OST/声优歌手）+ MusicBrainz/VGMdb 元数据补充；理由按"你喜欢的作品/声优相关、同风格同作曲、情绪场景"。**不抓网易云/QQ 评论**（成本风险不划算）。

**最先落地的三个小功能**（按 ROI）
1. **推荐反馈 memory**（依赖 Phase 5）——"喜欢/不喜欢/别推这种"写回，对推荐提升最直接。
2. **跨媒体推荐**——喜欢动画→原作漫画/轻小说/galgame/音乐，最契合 ACGN 场景、最有特色。
3. **galgame 推荐增强**——三源解释做完整，作为项目亮点。

**⊕ 对话式推荐交互（自联网补充——"推荐 agent"区别于"推荐系统"的核心）**
- **冷启动 / 偏好澄清**：新用户或收藏少时，不依赖 Bangumi 历史，通过**少量澄清问题**（最近看的/喜欢的类型/想要的氛围）快速建画像（对标 GATE / clarifying questions）。**克制原则**：研究（PrefDisco）发现约 29% 的澄清反而让对齐变差——**少问、问对、随时可跳过**，呼应"弱信号不驱动强动作"。
- **critiquing 负反馈迭代**：对推荐结果说"换一个 / 要短的 / 不要这个画风 / 太致郁了"→ 实时修正候选。区分**"假负反馈 vs 真负反馈"**（ReFINe：打分习惯/噪声导致的假负 vs 确凿负信号）——又一处 source/confidence 主线。
- **主动推荐**：培养潜在兴趣的多步推荐（对标 T-PRA）。
- **与 RL 连接**：critiquing → Actor-Critic / DPO 多轮偏好优化（T-PRA, ACL 2025），是 Phase 7 又一个 reward 抓手。

**⊕ 追番副驾（自原远期提前——推荐能力的延伸：从"推新作"到"管在追的"。Bangumi 给数据、Otomo 给决策）**
- **补番顺序**：想看列表里哪些有前作没补 → 系列图谱排"先 A 后 B"（`plan_watch_order` 雏形已有，可最先做）。
- **智能排期**：结合口味，从"想看"推"这周看哪 3 部 / 今晚挑个短的"。
- **追番情报**：在追的番这周更新了哪些 + 哪集讨论数暴涨(高能集) + 口碑变化。
- **搁置盘活**：搁置里哪些评分回升/已完结值得回坑（`analyze_abandoned`+评分）。

**⊕ 可分享口味报告（完整版）**：Phase 1 的人格卡 + 本阶段 aspect 好球区/雷区 → 完整「年度总结」。

**配套 eval**：galgame 三源推荐 case；跨媒体（动画→原作）case；"避雷生效"case；aspect 命中解释 case；冷启动澄清 case；critiquing"换一个要短的"case；追番副驾"补番顺序正确"case。

---

### Phase 7 · 图谱 Verifier / Benchmark / Agentic-RL
**目标**：把前面所有"可评测维度"收敛成 verifier + benchmark，作为 Agentic-RL 的 reward/环境。

**输入已就绪**：canonical `EntityRef`、set-F1、路径有效率、幻觉感知（docs 已设计）；source routing / 剧透不越界 / 映射置信度 / aspect 命中 / 多轮 critiquing 对齐（Phase 1.5~6 积累的 eval 维度）。
**RL reward 候选**：图谱事实正确性 + source routing 合理命中（软偏好，不硬禁用）+ 剧透不越界 + 映射不错配 + aspect 好球区匹配 + 多轮 critiquing 偏好对齐。
**前置条件**：A100（暂用 DeepSeek API 做 rejection sampling 数据侧）；policy 选型见既有判断（弱 policy 可能负优化，谨慎）。

**⊕ 研究级扩展（自原远期归此——重型 / 需多模态或图谱缝合）**
- **多模态识番 / 帧检索**：截图 → 识别作品/角色 → 落 `subject_id`/`character_id` → 接图谱与讨论（VLM；轻量的视频 ASR 语料已在 Phase 4 起步，这里做帧检索）。
- **GraphRAG**：从语料抽实体关系建图、检索带图结构（对标 GraphRAG/LightRAG/VGent）。Otomo 已有 canonical 图谱，把图谱与 RAG/视频缝合是天然亮点。
- **多轮偏好优化**：把 Phase 6 的 critiquing 交互做成 Actor-Critic/DPO 的多轮 RL 训练（对标 T-PRA），用用户模拟器扩数据。

---

## 3. 横切关注点

- **Eval 穿插**：每个 Phase 完成即补 3~5 个固定 case（评价矩阵 / 同步率 / 新番导视 / 剧透过滤 / memory 反馈 / aspect 解释 / 冷启动 / critiquing / galgame 推荐）。目标 20~30 个。
- **抓取合规**：外部源只读公开数据、标来源、B站走 body `code` 守卫（已修）、不持久化受限语料（萌娘 ai-train=no 红线）。
- **Memory 隐私**：见 Phase 5 红线。evolving memory 有"错误累积反馈环 / 敏感数据滞留"风险（Mem0 governance 研究指出），故 provenance + forget + 低置信推断是硬约束。

## 4. 附录：关键判断备忘
- Source Router 是**策略层/评测层**（prompt 分层 + 工具 description + vertical_links + eval 维度），**不造运行时大路由工具**（多一跳/误判/延迟）。
- 评价融合对**每个类型**都要输出"共识/分歧/置信度/适合你/剧透风险"，避免沦为"来源罗列器"。
- 收束仍在：好友同步/私评/弃坑虽已实现并审查通过，产品打磨次序仍以本路线为准。
- 外链策略：**具体直达优先 + 导航/搜索页降级兜底**——B站具体视频(BV)/评论已能抓取，但 API 不稳，`_bili_json` 守卫触发时退回搜索页。
- 澄清要克制：preference elicitation 不是问得越多越好（PrefDisco 警示 29% 反效果），少问、问对、可跳过。

## 5. 能力落位速查（原"远期"与"情感画像"已下放到各 Phase）

按"依赖收藏数据 + canonical 图谱 / 体现 agent 形态"筛选保留（通用 LLM 主场——剧情解说/翻译/同人创作/角色扮演——不碰），已整合进对应 Phase：

| 能力 | 落位 | 早/中/晚 | 一句话 |
|---|---|---|---|
| 角色/声优探索网络 | Phase 1 | 早 | 图谱已有，做前端可视化漫游 |
| 可分享口味报告(基础) | Phase 1 | 早 | 画像→人格卡，传播/demo |
| B站视频 ASR 语料 | Phase 4 | 中 | 口播→文本→RAG（Video-RAG 思路，轻量、不啃帧） |
| **aspect 情感画像（推荐内核）** | **Phase 6** | **中** | **tag→aspect 好球区/雷区；ABSA，你 MLLM/RL 发挥点** |
| **对话式交互(冷启动澄清/critiquing)** | **Phase 6** | **中** | **推荐 agent ≠ 推荐系统；critiquing→DPO 接 RL** |
| 追番副驾(补番顺序/排期/情报/盘活) | Phase 6 | 中 | Bangumi 给数据、Otomo 给决策；`plan_watch_order` 雏形已有 |
| 口味报告(aspect 增强) | Phase 6 | 中 | 叠加好球区/雷区的完整年度总结 |
| 多模态识番/帧检索 | Phase 7 | 晚 | 需 VLM，重型 |
| GraphRAG | Phase 7 | 晚 | 图谱+文本/视频缝合，研究级 |

> 提前判断依据：前两项是"已有结构化数据的可视化"(零新依赖)进 Phase 1；视频 ASR 轻量进 Phase 4；aspect 情感画像/对话式交互/追番副驾/口味报告 均属推荐范畴进 Phase 6；识番/GraphRAG 重型留 Phase 7。

---

> Sources（联网对标）：
> [AG-UI 协议（MarkTechPost）](https://www.marktechpost.com/2025/09/18/bringing-ai-agents-into-any-ui-the-ag-ui-protocol-for-real-time-structured-agent-frontend-streams/) ·
> [AG-UI 17 事件类型（CopilotKit）](https://www.copilotkit.ai/blog/master-the-17-ag-ui-event-types-for-building-agents-the-right-way) ·
> [Mem0 长期记忆架构（arXiv 2504.19413）](https://arxiv.org/pdf/2504.19413) ·
> [Bangumi API 文档](https://bangumi.github.io/api/) · [Bangumi API 仓库](https://github.com/bangumi/api) ·
> [Aspect 推荐综述 LSA（arXiv）](https://arxiv.org/pdf/2603.21243) · [ABSA+协同过滤（Springer）](https://link.springer.com/chapter/10.1007/978-3-032-14531-4_24) · [可解释细粒度情感推荐 FSER（PMC）](https://pmc.ncbi.nlm.nih.gov/articles/PMC9596275/) · [Video-RAG（arXiv 2411.13093）](https://arxiv.org/abs/2411.13093) ·
> [LLM 澄清问题做偏好引导（arXiv 2510.12015）](https://arxiv.org/html/2510.12015v1) · [Tunable 主动推荐 Agent T-PRA（ACL 2025）](https://aclanthology.org/2025.acl-long.944/) · [负反馈精炼 ReFINe（ACM WWW 2025）](https://dl.acm.org/doi/10.1145/3701716.3715538)
