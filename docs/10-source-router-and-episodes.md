# 10 · Source Router 与分集（Episode）维度

> 来源：用户 2026-06-25 补充（分集讨论 + 圈层外链）。核心洞察——**不把一堆站甩给用户，而是按"用户在问什么"选最相关的 2–4 个源**。这是垂直 ACGN agent 比通用助手"更懂行"的地方，也是把可验证 Agentic-RL 从"图谱问答"扩展到"源路由 + 分集定位 + 防剧透"的关键。

## A. 分集（Episode）维度

### A.1 已核实的数据事实
`GET /v0/episodes?subject_id=X` 直接返回分集列表，每集字段：
`id`(ep_id) · `sort`(集序) · `ep`(集号) · `type`(0 正片/1 SP…) · `airdate` · `name`/`name_cn` · **`comment`(讨论数)** · `desc` · `duration` · `subject_id`。

> 实测摇曳露营△（subject 207195）：22 集一次拿全，ep_id 762290 起，第 5 集讨论数 239（最热）。

**结论**：Episode Resolver **不用爬章节页、不用假设 ep_id 连续**——API 直接给 `subject → [ep_id, sort, air, 讨论数]`。"哪集最热"的讨论数曲线现成。**唯有讨论正文（吐槽箱）需爬 `bgm.tv/ep/{id}`**（v0 无短评/吐槽 API，复用 `comments` 工具的爬取模式）。

### A.2 能力设计（落地次序）
1. **Episode Resolver**（`get_episodes` 工具，易）：`subject_id → 分集列表`。典型"作品→分集"图谱边，应作为 Bangumi 工具加入。顺带拿到讨论数曲线。
2. **分集讨论 RAG**（中）：爬 `bgm.tv/ep/{id}` 吐槽箱 → **按"主楼+回复串"切块**（不按 token，保上下文）→ 接入 `hybrid_rank`。复用短评工具的爬取 + RAG 第二刀。
3. **分集口碑雷达**（中，亮点）：讨论数曲线找高峰集 → 对高峰集做质性摘要（夸点/吐槽/名场面/争议）。**结构化（讨论数）+ UGC RAG（讨论正文）**，是"结构化图谱 + 分集粒度 UGC 检索"的复合能力，简历友好。
4. **防剧透边界 Spoiler Boundary**（中，强需求）：用户"我看到第 N 集" → 只检索 `sort ≤ N` 的分集讨论、设定只取无剧透简介或拒答 → 输出标注"已按第 N 集进度过滤"。是检索约束 + 安全，且能进评测。
5. **未来多模态**（远期）：截图 → 识别作品+集数 → 落 `ep_id` → 查该集讨论 / B站视频弹幕评论。

### A.3 红线与算法
- 讨论正文作 **RAG/检索语料，不进训练**（许可 + 质量）。
- 算法价值：讨论数 + 时间分布 + 情绪/主题聚类 → 弱监督"高能集/争议集/入坑集"。

## B. Source Router（信息源路由）

### B.1 核心抽象
现有路由分两级，本节加第三级：
- **runner 级**（已有 adaptive）：SIMPLE / SYNTHESIS / 复杂 plan。
- **信息源级（新）**：按"意图 × 作品 type/题材"选 2–4 个最相关源，区分**可信度**（事实源 vs 讨论源 vs 工具外链），而非甩一堆站。
- 现有 `get_vertical_links`（粗版）应朝此升级。

### B.2 四层信息源（按可信度）
| 层 | 源 | 用途 | 可信度 |
|---|---|---|---|
| **Canonical Facts** | Bangumi 图谱（主）· VNDB(galgame, 有 API) · erogamescape/批判空间(gal 评分) · AniList(备) | 事实/人物/staff/年份/评分 | 高 |
| **Lore RAG** | 萌娘 · 中文维基 · Fandom | 设定/梗/术语/剧情 | 中高 |
| **Discourse** | Bangumi 分集讨论 + 短评/长评 · 圈层社区(百合会/S1/NGA/贴吧) · B站漫评评论 | 口碑/争议/圈层观点 | 中（必标来源） |
| **Utility Link** | yuc/名作之壁吧(导视) · 蜜柑/VCB/动漫花园/末日(资源) · B站 UP(视频) · Pixiv(图) · 网易云/QQ音乐(乐评) | 导视/资源/媒体入口 | link-out |

### B.3 意图 → 优先源 映射
| 用户意图 | 优先源（按序） |
|---|---|
| 事实 / 人物 / staff / 声优 / 评分 | Bangumi 图谱（galgame 补 VNDB / 批判空间） |
| 某一集大家怎么看 / 争议 | **Bangumi 分集讨论优先** → B站漫评 / 评论补 |
| 梗 / 设定 / 术语 | 萌娘 / 维基 RAG |
| 新番导视 · 数据向 | 名作之壁吧（最推）· yuc.wiki（时间表/RSS） |
| 新番导视 · 评价向 | 泛式 / 瓶子君152 / FlowerMX（百合向）· B站 UP 白名单 |
| 圈层讨论 | 百合→百合会(bbs.yamibo.com)/FlowerMX/峻岸喀秋莎；芳文社→芳文观星台/大猫猫组；扭曲党争→萌战吧/S1/NGA；galgame→VNDB+批判空间+绯月/月幕；轻小说→轻之国度/真白萌 |
| 在哪看 / 下载 | **蜜柑（收录最全，优先）** → VCB-Studio（BD/压制） → 动漫花园/末日（备）；正版平台优先 |
| 图 / 音乐 | Pixiv / 推特；网易云 / QQ音乐（歌单/评论区） |

### B.4 站点清单（用户提供 + 整理；只外链不抓取）
- **导视**：名作之壁吧 `space.bilibili.com/2859372`（数据向，最推）· 泛式 `/63231` · 瓶子君152 `/730732` · FlowerMX-花梦 `/13181306`（百合）· yuc.wiki（数据/时间表/RSS）
- **资源**：蜜柑 mikanani.me（最全/RSS订阅）· VCB-Studio vcb-s.com（BD压制）· 动漫花园 share.dmhy.org · 末日 share.acgnx.se
- **圈层**：百合会 bbs.yamibo.com · 芳文观星台 `/1585955812` · 大猫猫组 `/526330959` · 萌战吧(贴吧) · galgame：VNDB vndb.org(API) / erogamescape / 绯月 bbs.kfpromax.com / 月幕 ymgal.games / galgame贴吧 · 轻小说：轻之国度 lightnovel.fun / 真白萌 masiro.me
- **媒体**：Pixiv / x.com（图）· 网易云 music.163.com / QQ音乐 y.qq.com（乐评）
- **综合**：NGA · S1 stage1st · MAL · 小红书 · 贴吧 · QQ群
- **二游**（讨论多，Bangumi 可搜）：米哈游/库洛/鹰角/散爆/叠纸/深蓝/悠星/黄鸡

### B.5 B站定位（不是普通外链）
B站 = 视频搜索 + 创作者社区 + 评论语料。**分阶段**，不一上来爬全站（反爬/账号风险）：
- **v0**：搜索外链卡片 + **UP 白名单**（按题材：泛式/瓶子=综合漫评，FlowerMX=百合，芳文观星台/大猫猫组=芳文，名作之壁吧=数据导视）。
- **v1**：用户给定 BV/URL 或新番导视场景 → 取元数据/标题/公开评论摘要；有字幕优先摘字幕。
- **v2**：可合规拿字幕/弹幕/评论再做视频 RAG，否则只 link-out。

### B.6 红线
- 资源站**只 link-out**：不下载、不托管、不代理、不教绕限制。
- 讨论/评论作 RAG 语料、不进训练；必标来源。
- 圈层 provider 走白名单映射，**不做全网乱搜**（兜底才用 web_search）。

### B.7 定论（2026-06-25，与用户敲定）
**Source Router = 策略层 / 评测层，不是运行时大对象**：
- **内部源选择**：靠 prompt 源分层原则 + 工具 `description`，**不造 `route_sources` 大工具**（agent 选工具本身就是 routing；大对象只会多一跳、多误判、多延迟）。
- **外链精选**：`get_vertical_links` 是合理的"外链层 Source Router"——它不是告诉 agent 用什么工具，而是**替用户精选 2-4 个外部站点**（输入意图、输出链接），是产品能力不是多余路由。
- **评测层（关键）**：把 source routing 做成 **eval 维度**——检查"该用 Bangumi 没乱用 web""问分集有没有查 `episode_comments`""问 galgame 是否用 VNDB"。既不多一跳、又让"选对源"可验证，**直接接上 Agentic-RL 的 source routing reward**（路由从运行时负担变成可评测/可训练的能力）。
- **后期边界**：工具涨到 40-60 个时，可加**非用户可见的轻量 tool-subset selector**（只为减少暴露给 LLM 的 schema 数、降延迟），是性能优化、不是现在的产品功能。
- **AniList / Fandom**：作 Canonical / Lore 的**兜底添头**（主源查不到再补），主体不动摇。
- **B站评论**：保持 link-out + UP 白名单为默认；已开始支持用户给定视频/新番导视场景的单页公开评论摘要。未来再考虑视频评论作 RAG 知识库。保持不大规模爬（反爬 / 账号风险）。

## C. 算法层（Agentic-RL 平移，拓宽 moat）
把可验证奖励从"图谱多跳"扩展到垂直 agent 的更多维度：
- **source routing reward**：事实问题该走图谱、口碑问题该走讨论——选对源给奖励。
- **episode grounding reward**：用户问第 N 集，是否命中正确 `ep_id`。
- **spoiler safety reward**：是否越过用户观看进度（强约束）。
- **citation reward**：答案事实能否被 Bangumi/VNDB/yuc 等权威源链接支持。
- **discourse summary eval**：观点聚类是否覆盖主流主题、是否混淆少数与主流。

## D. 落地次序（待办）
1. Episode Resolver（`get_episodes`）+ 分集讨论数曲线 —— 易、高价值、复用图谱。
2. 分集讨论 RAG + 分集口碑雷达 —— 复用 comments 爬取 + hybrid。
3. Spoiler Boundary —— 检索约束，强需求 + 可评测。
4. `get_vertical_links` 升级为 Source Router（意图×题材选源 + 可信度分层 + UP 白名单）。
5. VNDB API 接入（galgame canonical 补 Bangumi）。
6. B站 v0（搜索外链 + UP 白名单）；v1/v2 视合规推进。
7. 算法：上述 reward 纳入 verifier / RL（待 Phase 3）。
## E. 2026-06-26 落地状态

已实现：
- `recommend_subjects` 的 galgame EGS 前置召回已有严格 EGS 标题 -> Bangumi ID 对齐：只接受 exact title 或安全版本差异，避免《兰斯10》误配《兰斯9》、《樱之刻》误配《樱之诗》。
- `review_subject` 已做统一评价矩阵雏形：Bangumi 为主，anime/book 补 AniList，game/galgame 补 EGS/VNDB，music 补 MusicBrainz 元数据。
- `list_bangumi_friends` / `sync_user_recommendations(auto_friends=true)` 已支持 best-effort 解析 Bangumi 好友页并做同好高分未看推荐。
- `compare_user_taste` 已支持 Your Angle 风格的同步率雏形：共同评分余弦、用户空间/peer 空间/并集空间相似度、共同高低分、最大分歧；`sync_user_recommendations` 已用 `peer_weight` 做好友推荐加权。
- `assess_spoiler_policy` 已支持自然语言剧透状态：默认无剧透，识别“看到第 N 集/别剧透/可以剧透/结局”等信号，并写入会话运行时状态。
- `get_episode_comments` 已有工具层 `max_episode_sort` 硬过滤；`analyze_abandoned_subjects` 可结合 `ep_status` 和附近分集讨论做弃坑节点分析。
- `search_bilibili_guide_videos` 已返回 B站白名单导视视频元数据，`get_bilibili_video_comments` 已能抽取单视频公开评论样本。
- `get_bilibili_video_comments` 已复用方面级抽取，返回 `aspect_opinions` 与 `opinion_summary`，用于总结导视/漫评评论区的期待点、担心点和争议点。
- `season_guide_brief(include_video_comments=true)` 已能把白名单导视视频评论摘要接入新番导视结果，默认仍不抓评论以控制成本和风险。
- `explain_acgn_meme` 已接入萌娘百科，用于梗/术语/出处解释。
- `review_subject` 已增加方面级口碑雏形：story/character/pacing/visual/music/direction/text/system/voice/general + positive/negative/mixed + spoiler_risk。
- `analyze_user_opinions` 已返回用户私评的 `aspect_opinions`，可用于解释用户具体喜欢/讨厌的是剧情、角色、节奏、作画等哪个方面。

仍保守处理：
- B站视频内容/字幕、百合会/S1/NGA/贴吧等全文话语源 RAG 暂不做大规模抓取。
- MusicBrainz 只作为音乐元数据源，不作为音乐口碑评分源；VGMdb 暂不接第三方非官方镜像。
- 用户私评情感仍是关键词级弱信号，后续再集中升级为方面级情感抽取。
- 同步率仍是在线实时计算，好友页来自 HTML best-effort 解析；后续可加入缓存、更多 peer 筛选策略和更接近 Your Angle 的报告页。
