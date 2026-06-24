# recsys-offline · 离线推荐（Track B）

工业推荐学习线（见 [../docs/06-recsys.md](../docs/06-recsys.md)）。**评测前置**：先有指标 + 流行度基线，再上模型。

## 两条数据线（关键）
1. **MAL 公开集（学方法 / 刷指标）**：Kaggle Anime Rec DB（MyAnimeList `anime_id`）。规模大，适合练 CF/MF/LTR 全套刷 NDCG。
   **但主键是 MAL ID，与在线（Bangumi `subject_id`）不通 → 产出无法反哺在线**，定位是方法论 / 简历资产。
2. **Bangumi 原生集（闭环 / 上线）**：自采 Bangumi 用户公开收藏（`subject_id`）。规模小些，但
   **主键与在线一致 → 训出的 i2i 相似度表直接作在线 `recommend` 的「协同召回 provider」**（看过 X 的人也看 Y），
   补在线天生缺失的协同信号、治重度用户饱和。这条才是**离线真正反哺在线的闭环**。

> 公开集是历史快照（无最新番），但离线学的是**用户-物品偏好结构**（协同模式相对稳定），刷指标与新番无关；
> 新番时效由在线轨（live Bangumi + 图谱召回）负责。

## 阶段（对应 docs/06 §7）
- **S0 ✅**：评测套件（Recall@K / NDCG@K / HitRate@K / MRR）+ leave-one-out + **流行度基线**。`run_baseline`。
- **S1 ✅**：ItemCF(BM25) + ALS/BPR-MF（implicit），同台对比流行度。`run_s1`。
  - 真实结果（Anime Rec DB，5.2M 正反馈/69k 用户，评测 5000）：
    流行度 NDCG@10=0.064 → **ItemCF 0.110(+72%) · BPR 0.133(+108%) · ALS 0.174(+171%)**。**ALS 完胜**。
- **S2 ✅（核心）**：ALS 召回 top-100 → 交叉特征 → **LightGBM LambdaMART** 重排。`run_s2`（防泄漏：LTR 训练/评测用户不相交）。
  - 真实结果（评测 4000）：ALS NDCG@10=0.176 → **重排 0.181(+3%)、MRR 0.142→0.149(+5%)**。特征重要度 **genre_overlap≈als_score > members > rating**。
  - 提升温和（ALS 已强 + 仅 6 基础特征）；漏斗/LTR/特征重要度/防泄漏评测全跑通，更大增益靠 S3 LLM 特征 + 更多交叉/序列特征。
- **S3+**：LLM 派生特征 / 冷启动 → 双塔+Faiss → DPO/GRPO（推后）。

## Bangumi 原生 CF（闭环，对应 docs/06 §9）✅
自采 Bangumi 公开收藏 → 训 ALS/ItemCF → 导出 i2i 表 → 在线协同召回。**离线↔在线真闭环**（≠ MAL 线只能刷指标）。
- **采集** `bangumi_collect`：按数字 UID 区间拉 `/v0/users/{uid}/collections`（公开免 token），礼貌限流 + 并发 + 断点续传。
  实测命中率 ~18–26%、~11 uid/s。原始 user-item 数据**本地训练用、不提交**（隐私），只发布聚合 i2i 表。
- **训练评测** `run_bangumi_cf`：leave-one-out 对比流行度基线。
  真实结果（自采 ~1.5k 用户 / 9 万交互，过滤后 815 用户 / 2672 物品）：
  流行度 NDCG@10=0.062 → **ItemCF 0.127(+105%) · ALS 0.186(+199%)**。数据够大 **ALS 完胜**（小数据时 ItemCF 更稳，故导出模型可选）。
- **导出**：`--export-model als` 用 `similar_items` 导每物品 top-K 邻居 → `i2i_<stype>.json`（key=subject_id）。
- **在线接入**：拷到 `backend/otomo/data/`，`recommend` 加载（缺失则该路静默跳过）。协同召回与标签 / 图谱融合——
  rank 衰减累加、封顶 1.5，**协同 > 图谱**。端到端验证：协同贡献了图谱 / 标签召回不到的独特候选。

## 数据集（S0 决定）
- 学习/指标：公开 anime 评分数据集（user×item 矩阵）。Kaggle Anime Rec DB 需 auth；优先用免 auth 可直接下载的镜像（GitHub/HuggingFace），或后期用 Bangumi API 抓用户公开收藏建域内集。
- 划分：**时间分割优先**（避免未来泄漏）；小数据用 leave-one-out。固定负采样跨模型一致。

## 结构
```
recsys_offline/
  metrics.py          # Recall@K / NDCG@K / HitRate@K / MRR / Precision@K
  split.py            # leave-one-out（item_col 可泛化：anime_id / subject_id）
  baseline.py         # 流行度基线（必报，打不过=白做）
  data.py             # MAL 数据集加载（CSV → 交互）
  run_baseline/s1/s2.py  # MAL 线：S0 基线 / S1 CF·MF / S2 ALS召回+LambdaMART
  bangumi_collect.py  # 【Bangumi 原生】采集公开收藏（UID 区间，限流/并发/续传）
  bangumi_data.py     # 【Bangumi 原生】收藏加载（正反馈=看过/在看）+ 稀疏过滤
  run_bangumi_cf.py   # 【Bangumi 原生】CF：评测 + 全量重训 + 导出 i2i（闭环产物）
tests/                # 合成数据单测（不依赖下载）
```

## 跑
```bash
conda activate otomo
cd recsys-offline && pip install -e .   # numpy/pandas/implicit/lightgbm/httpx（按阶段装）
pytest                                   # 合成单测
python -m recsys_offline.run_baseline --data <path>   # S0 流行度基线指标

# Bangumi 原生 CF 闭环（离线真正反哺在线）
python -m recsys_offline.bangumi_collect --start 1 --end 50000   # 采集公开收藏（可断点续传）
python -m recsys_offline.run_bangumi_cf --export-model als        # 训练+评测+导出 i2i 表
cp data/bangumi/i2i_anime.json ../backend/otomo/data/            # 发布给在线 recommend
```
