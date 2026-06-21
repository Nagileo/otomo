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
- 切本地 Qwen：`LLM_BASE_URL` 指向本地 vLLM 的 OpenAI 兼容端点即可，其余不变。

## 4. 成本与限流意识

- Bangumi / 萌娘调用走 Redis 缓存（见 [01](01-architecture.md)）降外呼。
- 评测 / benchmark 跑量大：开发期优先用便宜 API，固定随机种子与缓存以可复现。
