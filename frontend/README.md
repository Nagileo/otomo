# Otomo Frontend

Next.js (App Router) chat，消费后端 `/chat` 的 SSE：左侧流式对话 + 右侧执行轨迹 (trace)。
面板按域拆在 app/panels/（shared 原语 + media/visual/memory/recommend/product/report）。

## 跑起来

```bash
conda activate otomo   # node/npm 装在这个 conda 环境里（见根 README）
cd frontend
npm install
npm run dev            # http://localhost:3000
```

需先起后端（默认 `http://localhost:8000`，可用 `NEXT_PUBLIC_BACKEND` 覆盖）。

## 现状

- `app/page.tsx`：手写 SSE 解析（fetch + ReadableStream），渲染 tool_call/observation/answer_delta/final/followup 事件。
- 回答 markdown 渲染（react-markdown）；来源挂 Bangumi 封面图卡片；追问 chips；右侧 trace 面板（后续「手搓 vs LangGraph」对比展示位）。
- 样式仍偏简，产品化（session_id 透传、卡片打磨）排在 Phase 2。
