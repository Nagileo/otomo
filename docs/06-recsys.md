# 06 · 推荐系统设计与学习路线

> 作者强于 LLM/RL、弱于推荐——本模块是**系统性补 recsys 的学习线**，同时是简历硬通货。关键认知：现代工业推荐 = "embedding + ANN + 学习型打分器"，前沿（DPO/GRPO 重排、LLM-as-feature）**正是把 LLM 后训练栈用到排序上**——这是作者相对纯 recsys 候选人的差异化。

## 1. 标准 recall→rank 漏斗

| 阶段 | 任务 | 量级 | 模型 | 优化目标 |
|---|---|---|---|---|
| **召回 Recall** | 目录→候选 | 数万→~500-1000 | CF / 双塔+ANN / item2vec / 内容相似 | Recall、速度 |
| （预排） | 便宜粗筛 | 1000→200 | 轻量打分 | 延迟 |
| **排序 Rank** | 精排序 | 数百→有序 | **LightGBM/LambdaMART** | NDCG |
| **重排 Re-rank** | 规则/多样性/解释 | 终列表 | 规则/MMR/LLM | 多样性、UX |

数据信号：**物品元数据**（tag/staff/声优/评分/排名/年代/类型）+ **用户收藏/评分**（隐式+显式）。

## 2. 方法 → anime 数据映射

- **内容过滤（day-1 MVP，零协同数据）**：元数据建物品向量，推与用户高分项相似的。冷启动友好。
- **协同过滤 CF**：item-based（"看了 X 也看 Y"，item-item cosine，可解释经典基线）；user-based 一般跳过。
- **矩阵分解 MF（最重要的经典法，必实现）**：ALS/BPR（`implicit`，隐式信号）或 **LightFM**（hybrid MF+元数据，天然冷启动）。教会隐向量与隐式反馈。
- **embedding 召回**：item2vec（把用户评分序列当"句子"跑 word2vec，便宜好用）；双塔（user/item 双塔，dot-product≈相关，item embedding 进 Faiss ANN）。
- **LTR 重排**：消费 per-(user,item) 特征（含**交叉特征**：与用户 top-tag 重叠、与用户高分 CV 重叠、年代差）。pointwise→pairwise→**listwise（LambdaMART，直接优化 NDCG，目标）**。

## 3. 冷启动

- **新物品**：内容过滤 + LightFM hybrid，靠元数据可推（新番未有评分也能推）。
- **新用户**：① 从少量已评建内容画像即时内容召回；② 流行度/质量先验（Bangumi rank/score 去偏）兜底；③ **LLM 派生特征（作者的优势）**：LLM 把简介/tag 归纳为主题/情绪/套路（"异世界、慢热恋爱、忧郁"），作物品塔/重排的稠密侧特征（文献报冷启动 NDCG 提升可达 ~40%）；④ **会话式偏好引导**（agent 问 2–3 句 → 转内容查询），是 LLM agent 最干净的冷启动 UX。

## 4. 公开数据集（离线训练/评测，见 [02-data-sources §4](02-data-sources.md)）

Anime Recommendations DB（入门基线）→ Anime Dataset 2023（上规模）；元数据用 AniList 富化；域内对齐/部署用 Bangumi15M（离线）+ Bangumi API（线上用户本人数据）。

## 5. 评测

指标：Recall@K、**NDCG@K（头牌）**、HitRate@K、MAP、MRR。
划分：**时间分割**优先（避免未来泄漏），固定负采样。
**必报流行度基线**；显式讨论流行度偏置、数据泄漏、offline≠online、覆盖率/多样性。
（细节见 [05-evaluation §3](05-evaluation.md)。）

## 6. 与 LLM/Agent 的交汇（作者最强角度）

1. **LLM-as-feature-extractor**（高价值低风险，先做）：LLM 把简介/tag/评论转结构化特征+文本 embedding → 物品塔/重排侧特征，直击冷启动。
2. **重排器作为 agent 工具**：`recommend(user_profile, filters) → ranked list`，agent 决定何时调、如何从对话设 filter、如何解释。系统工程，简历友好。
3. **会话式推荐 + LLM 解释/重排**：多轮偏好引导（"更忧郁点、少热血"）；LLM 对 GBDT top-N 重排并生成**有依据的解释**（"因为你给 X 高分，且同 CV、同慢热基调"）。
4. **RL 偏好后训练（capstone · 作者主场）**：agent 记录 accept/reject/"more like this"/"not interested" → 原生偏好对 → **DPO**(chosen/rejected list) 微调 LLM 重排器、去位置/流行度偏置；进阶 **GRPO/Rank-GRPO**（NDCG/hit 作奖励，多轮对话推荐）。**多数 recsys 候选人不会 RL，多数 RL 候选人没做过漏斗——这正是作者的独特叙事。**

## 7. 分阶段建设（每阶段=可运行 demo + 一张"超越上一基线"的指标表）

- **S0 数据&评测脚手架（先做，地基）**：加载 Anime Rec DB；user×item 矩阵；时间分割+leave-one-out；Recall@K/NDCG@K/HitRate@K/MRR；**流行度基线行**。展示 eval 严谨。
- **S1 经典召回**：内容 + item-CF + **ALS/BPR MF**。报各法 vs 流行度。
- **S2 漏斗核心（centerpiece）**：工程化交叉特征 → **LightGBM lambdarank**。报重排相对纯召回的 NDCG@10 提升。
- **S3 LLM 特征 + 冷启动**：加 LLM 派生特征 + 新用户引导；在冷启动用户切片单独报 NDCG 提升。
- **S4 深召回（可选）**：双塔 + Faiss，报召回/延迟。
- **S5 Agent 集成**：漏斗封装为工具 + 会话引导 + LLM 解释。
- **S6 RL capstone（差异化）**：accept/reject → 偏好对 → DPO（再 GRPO/Rank-GRPO），报离线偏好准确率/NDCG 提升，诚实标注 offline-vs-online。

**先做 S0→S2**（约 70% 的实用 recsys：漏斗形态、隐式反馈、隐向量、LTR 目标、诚实指标）；S3–S6 与作者 LLM/RL 强项融合，做出少有候选人具备的东西。

工具：`implicit`、`LightFM`、`lightgbm(lambdarank)`、`faiss`、可选 `recbole`。

## 8. 重度用户 / 新颖性：多策略召回（务必配合使用，不止离线）

**问题（已实测）**：重度用户在其核心题材上，热门标签召回会**饱和**——@sunshineclover 看过 600+ 日常/百合/校园番，"据口味"无标签推荐几乎返回空（热门的都看过了）。这是内容+流行度召回的固有局限。

**原则**：用**多路召回的并集 + 统一重排**来治；**CF 只是其中一路，必须和下面这些配合**，不能只指望离线。

| 策略 | 怎么逃出"热门已看完" | 在线? | 信号 |
|---|---|---|---|
| **图谱召回（staff/制作组/声优谱系）** | 从最爱作品 → 监督/班底/声优 → 其未看的其他作品 | ✅ | Bangumi 图谱边 |
| **LLM 提名 + 图谱验证** | LLM 凭长尾知识提名冷门 → Bangumi 逐个验证(存在/未看/评分)防幻觉 | ✅ | LLM 参数知识 + 图谱真值 |
| **冷门模式（反流行度）** | 口味标签内专挑低人气高分（heat 反向 / 质量÷人气） | ✅ | 评分 vs 人气 |
| **语义 / vibe 召回（embedding）** | 简介/评论 embedding 找"气质同、标签不同"的作品 | 半 | 文本 embedding |
| **口味扩展（邻接探索）** | 推核心题材的邻接领域（标签共现/LLM 推理） | ✅(LLM) | 共现 / LLM |
| **跨媒体** | 动画党往 galgame/小说/漫画 推（多类型设计天然支持） | ✅ | 图谱跨 type |
| **交互式提问** | "挖宝 / 换心情 / 邻近题材？"——agent 引导 | ✅ | 对话 |
| **协同过滤 CF（离线训练 → 在线 provider）** | "和你像的人也爱 X"，挖图谱/内容召回不到的隐藏口味 | ✅(§9 已落地) | user×item 矩阵 |

**工程形态**：每个策略 = 一个 **recall provider** → 候选**并集** → 去重 + 多样性(MMR，已做) → 排序（在线加权 / 离线 LTR）。多路召回是工业推荐标配。

**落地次序**：在线先补 **图谱召回 + 冷门模式 + LLM 提名验证**（立刻治当下饱和、且最契合"LLM+图谱"差异化）；离线 CF 随 S1 跟上；最终把离线模型作为其中一路 provider 融进来。**简历叙事**：多路召回融合 + LLM/图谱 provider + 离线 LTR——比单一 CF 丰富得多。

## 9. 离线↔在线闭环：Bangumi 原生 CF 协同召回（已落地 ✅）

§8 表里"协同过滤 CF"曾是 **❌ 需训练**——本节把它落地成在线的一路 provider，让离线**真正反哺**在线。

### 为什么之前"离线帮不上在线"
- 离线 MAL 数据集主键是 **MyAnimeList `anime_id`**，在线全程 **Bangumi `subject_id`**——两套主键不通，离线训出的 i2i/embedding 喂不进在线。
- 在线 `recommend` 只有标签 / 图谱召回（Bangumi API 无跨用户共现）→ **天生缺协同信号**。
- 结论：MAL 线的 S0–S2 是**方法论 / 简历资产**，对在线 0 反哺。要闭环，必须换 Bangumi 原生数据底座。

### 闭环做法（`recsys-offline/bangumi_*`）
```
自采 Bangumi 公开收藏(subject_id) → ALS/ItemCF → 导出 i2i 相似度表
  → 拷给 backend → 在线 recommend 读表做协同召回(看过 X 的人也看 Y)
```
- **采集** `bangumi_collect`：数字 UID 区间拉 `/v0/users/{uid}/collections`（公开免 token），礼貌限流 + 并发 + 断点续传。原始 user-item 数据本地训练用、不提交（隐私），只发布聚合 i2i 表。
- **训练** `run_bangumi_cf`：leave-one-out 对比流行度基线。自采 ~1.5k 用户 / 9 万交互（过滤后 815 用户 / 2672 物品）下：流行度 NDCG@10=0.062 → ItemCF 0.127(+105%) → **ALS 0.186(+199%)**。小数据时 ALS 欠拟合、ItemCF 更稳，故导出模型可选。
- **接入**：i2i 表 key=subject_id 与在线一致，加载为协同召回 provider；i2i score 量纲随模型波动，故用 **rank 衰减累加**（跨模型稳定）、封顶 1.5；权重 **协同 > 图谱**（图谱"同制作组"是弱信号、易同质霸榜，故压权 0.9 + 每制作组限量 8 部）。缺 i2i 表时该路静默跳过（优雅降级）。

### 价值与诚实标注
- **治饱和**：协同能挖内容 / 图谱召回不到的隐藏口味——端到端实测，给百合 / 日常重度用户召回了《荒野的寿飞行队》这类跨制作组发现。
- **真闭环 + 叙事**：自建数据集 → 离线训练 → 产出直接上线，比"公开集刷 NDCG"高一档。
- **现状局限**：自采规模仍偏小（~1.5k 用户、密度 3%），i2i 仅覆盖 2672 热门物品；扩大 UID 采样区间可提覆盖与冷门物品质量。多路融合里协同稳定占 1–2/N 是健康占比（补充信号，非主导）。
