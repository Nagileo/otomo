# recsys-offline · 离线推荐（Track B）

工业推荐学习线（见 [../docs/06-recsys.md](../docs/06-recsys.md)）。**评测前置**：先有指标 + 流行度基线，再上模型。
与在线推荐互补：**离线学协同模式（CF/MF/LTR），在线（live Bangumi + 图谱）保新鲜**。

## 关于"公开数据集没最新番"
公开集是历史快照（无最新番），但离线轨学的是**用户-物品偏好结构**（协同模式相对稳定），
跑指标（NDCG/Recall vs 流行度基线、MF>CF、LambdaMART 提升）与新番无关、照样有效。
新番时效由在线轨负责（LightFM hybrid 还能用元数据推训练未见的新番）。

## 阶段（对应 docs/06 §7）
- **S0（当前）**：评测套件（Recall@K / NDCG@K / HitRate@K / MRR）+ 划分（leave-one-out / 时间分割）+ **流行度基线**。
- S1：内容过滤 + ItemCF + ALS/BPR-MF，报各法 vs 流行度。
- S2（核心）：交叉特征 + **LightGBM LambdaMART** 重排，报 NDCG@10 提升。
- S3+：LLM 特征/冷启动 → 双塔+Faiss → 导出接在线 → DPO/GRPO（推后）。

## 数据集（S0 决定）
- 学习/指标：公开 anime 评分数据集（user×item 矩阵）。Kaggle Anime Rec DB 需 auth；优先用免 auth 可直接下载的镜像（GitHub/HuggingFace），或后期用 Bangumi API 抓用户公开收藏建域内集。
- 划分：**时间分割优先**（避免未来泄漏）；小数据用 leave-one-out。固定负采样跨模型一致。

## 结构
```
recsys_offline/
  metrics.py    # Recall@K / NDCG@K / HitRate@K / MRR / Precision@K
  split.py      # leave-one-out / 时间分割
  baseline.py   # 流行度基线（必报，打不过=白做）
  eval.py       # evaluate(recommender, train, test) → 指标表
  data.py       # 数据集加载（CSV → 交互三元组）
tests/          # 合成数据单测（不依赖下载）
```

## 跑
```bash
conda activate otomo
cd recsys-offline && pip install -e .   # numpy/pandas/implicit/lightgbm（按阶段装）
pytest                                   # 合成单测
python -m recsys_offline.run_baseline --data <path>   # S0 流行度基线指标
```
