# 08 · LLM 选型与配置规范

## 1. 两层 LLM 策略

抽象优先：所有 LLM 调用走 **OpenAI 兼容接口**（LiteLLM 或 OpenAI SDK 指向不同 `base_url`），模型可一键切换，由配置 `LLM_*` 控制。

| 角色 | 选型 | 理由 |
|---|---|---|
| 开发期 agent 大脑（v0–v1） | **DeepSeek-V3.x/V4 API**（默认），备选 Qwen-Max | 便宜、中文原生、function-calling 强；评测要海量 rollout，API 迭代最快 |
| SFT 冷启动 teacher | 强 API 模型（DeepSeek/Qwen-Max） | 用强模型造高质量轨迹，蒸馏给小模型 |
| RL policy（v2） | **本地开源 Qwen 2.5-7B/14B-Instruct，vLLM 起服务** | **API 模型无法 RL**；开源权重 + 已有 ms-swift/VerL 栈，接 SAR 经验 |
| 多模态彩头（可选） | 包 trace.moe 等外部服务 | 不自训 VLM，避开 CV 正面战场 |

要求：所选模型须原生支持 **function-calling / tool-use（OpenAI 风格）** 与稳定 JSON 输出。DeepSeek、Qwen 均满足。

## 2. 为什么这么分

- v0–v1 目标是把 agent **跑通、跑稳、可评测**——此时要"聪明 + 便宜 + 会调工具"，API 最优。
- v2 才进 RL；RL 必须能改权重，只能本地开源模型。提前用接口抽象隔开，到时切 `base_url` 即可，不返工。
- teacher→student 蒸馏（强 API 造轨迹 → 小 Qwen SFT → RL）是标准且省成本的路径，正好复用作者已有训练栈。

## 3. 配置与密钥规范

- 所有密钥进 `backend/.env`（已 `.gitignore`），**绝不提交、绝不写进任何 doc / 代码常量**。
- 提交 `.env.example`（仅占位键名、无真值），至少包含：
  - `BANGUMI_TOKEN=`（个人令牌；只读开发可空，写操作必填；可在 bgm.tv 个人令牌页随时吊销/重签）
  - `BANGUMI_USER_AGENT=otomo-dev/otomo/0.1 (+repo-url)`（**强制**，通用 UA 会被拒）
  - `MOEGIRL_USER_AGENT=otomo-rag/0.1 (+repo-url; contact)`（礼貌 + 可联系）
  - `LLM_BASE_URL=` / `LLM_API_KEY=` / `LLM_MODEL=`（默认指向 DeepSeek）
  - Web search（全网兜底）：`WEBSEARCH_PROVIDER=`（tavily/serper/exa/bocha，默认 tavily）+ 各引擎专属 key `WEBSEARCH_TAVILY_KEY/WEBSEARCH_SERPER_KEY/WEBSEARCH_EXA_KEY/WEBSEARCH_BOCHA_KEY`（全配好，切引擎只改 PROVIDER 一行）；或用通用 `WEBSEARCH_API_KEY` 兜底。不填则 web_search 优雅报"未配置"。
    - 定价/质量：tavily/exa 每月1000免费(个人首选)；**serper 一次性2500后 $1/千最便宜+中文质量好(付费首选)**；bocha 试用1000/3月、之后 ¥36/千($5)质量最好但最贵。
- 切本地 Qwen：`LLM_BASE_URL` 指向本地 vLLM 的 OpenAI 兼容端点即可，其余不变。

## 4. 成本与限流意识

- Bangumi / 萌娘调用走 Redis 缓存（见 [01](01-architecture.md)）降外呼。
- 评测 / benchmark 跑量大：开发期优先用便宜 API，固定随机种子与缓存以可复现。

## 5. 多用户 Token 管理（刚需）

凡"以用户为主体"的能力（口味画像、个性化推荐、追番进度、收藏分析、会话式建库写操作）都强依赖用户的 Bangumi token，所以**从一开始就按多用户设计**：

- 数据模型 `user_id → BangumiToken` 映射，token 存加密配置/DB；`.env` 只放**默认开发 token**（作者自己的号当首个测试用户）。
- 作者后续开第二个号测多用户，只需新增一条映射，不改代码。
- **只读 vs 写分权**：搜番/查图谱/RAG 只读（匿名即可，R18 除外）；改收藏/进度需 token + **人工确认**。
- token 可在 bgm.tv 个人令牌页随时吊销/重签，规范同 §3（绝不入库提交）。

## 6. 开源模型 vs API：会"打折"，但这正是卖点

- **诚实**：Qwen 7–8B 零样本做 agentic（多步规划、tool-call 稳定性、何时停止）**明显弱于 DeepSeek-V3**。
- **但不会拿零样本当产品**：在**窄垂域**（固定工具集/任务分布）上，**SFT 冷启动 + RL** 能把小模型在**该任务上**拉到接近大模型。
- **"零样本-8B ≪ SFT-8B < RL-8B ≈ DeepSeek" 这条对比曲线本身就是简历/论文最硬的结果**。
- **尺寸**：8B 能讲"小模型"故事但更难调通，**14B 是更稳的甜点**。
- **本地部署与代理**：**主路径全程免代理**——DeepSeek API 国内直连；Qwen 权重从 **ModelScope（魔搭）** 拉、vLLM 起服务，均无需 clash。**仅当要 A/B Gemini/GPT/Claude 时才需代理**。切本地模型只改 `LLM_BASE_URL` 指向本地 vLLM 端点。
