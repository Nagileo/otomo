# 05 · 评测方案

> 评测是本项目的算法骨架，也是作者的强项。三个相对独立的评测域：**Agent 多跳任务**、**RAG 检索/问答**、**推荐排序**。每个都要有基线、有指标、有去偏意识。

## 1. 可验证多跳 Agent Benchmark（脊柱 · RL 真值来源）

### 自动造题（关键创新点）
利用 Bangumi 图谱真值边**程序化生成「多跳问题 + 标准答案」**，无需人工标注：
- **模板化遍历**：从随机种子实体出发，按边模板组合 1–3 跳，回填约束（年份/评分/标签），得到问题与**可校验答案集合**。
  - 例：`角色→CV→作品（filter: air_date, rating, tag）`、`番→监督→其他番`、`番A↔番B 最短 CV 路径`。
- **答案真值**：直接是图谱遍历结果（集合/有序列表/路径），可算 exact-match、set-F1、路径正确性。
- **难度分级**：按跳数、约束数、候选规模分级，支持课程式评测。

### 指标
- **任务成功率**（答案集合/顺序正确）。
- **Tool-call 准确率**（调用路径是否最优/正确）。
- **平均步数 / token 成本**。
- **Verifier 通过率**（检索层 + 答案层）。
- 设定/解释类用 **LLM-as-judge + 来源核对**（防幻觉）。

### 基线
- prompt-only（无工具）/ 固定流水线 / 纯 ReAct → 后续 SFT → RL 各版本对比成功率。

> 这套 benchmark 本身可独立开源，是"二次元 Agent 可验证评测集"。RL 训练数据只来自此（Bangumi 真值），**不含萌娘文本**（许可证红线）。

## 2. RAG 评测

- **检索**：Recall@K、命中率（自建测试集：问题→应命中的萌娘/维基页/chunk）。
- **混合检索消融**：BM25 vs dense vs hybrid vs +rerank。
- **回答质量**：引用正确率（答案陈述是否被来源 chunk 支撑）、幻觉率、来源链接完整性（许可证要求，必测）。

## 3. 推荐评测（见 [06-recsys](06-recsys.md) 细化）

- **指标**：Recall@K、NDCG@K（头牌，LambdaMART 直接优化）、HitRate@K、MAP、MRR。
- **划分**：implicit feedback 用**时间分割**（train 过去 / test 未来）优先于随机/leave-one-out（避免未来泄漏）；负采样固定（1 正 + 99 采样负）跨模型一致。
- **必报基线**：流行度基线（按 members/rank 推 Top-N）——**打不过它等于没做**。
- **去偏与陷阱（README 显式写出，体现成熟度）**：流行度偏置（对比并报告流行度去偏指标）、数据泄漏、offline≠online、覆盖率/多样性（intra-list distance），不要只报准确率。

## 4. Agent 整体评测（Agentic Eval）

每次任务记录：intent、tool calls、retrieval 命中、推荐候选、最终答案、token、latency、失败原因。
构建 ≥100 条测试 case（覆盖：多跳问答、声优查询、补番规划、跨源证据问答、推荐），支持 **case replay（回放）**。
报告：tool-call accuracy、Recall@K、NDCG@10、引用正确率、P50/P95 latency、失败归因分布。

## 5. 评测工程

- `eval/` 目录：benchmark 生成器、case 集、指标库、回放器、报告生成。
- 每个里程碑产出一张"指标 vs 基线"表，并入对应版本的博客/简历句。
- Eval 也封装为 **Eval Tool**，让 agent/CI 可调用做回归。
