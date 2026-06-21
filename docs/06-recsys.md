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
