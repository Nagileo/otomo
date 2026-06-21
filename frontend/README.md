# Otomo Frontend (A1 骨架)

最简 Next.js (App Router) chat，消费后端 `/chat` 的 SSE：左侧流式对话 + 右侧执行轨迹 (trace)。
后续按 docs/01 升级到 shadcn/ui + Vercel AI SDK。

## 跑起来

```bash
conda activate otomo   # node/npm 装在这个 conda 环境里（见根 README）
cd frontend
npm install
npm run dev            # http://localhost:3000
```

需先起后端（默认 `http://localhost:8000`，可用 `NEXT_PUBLIC_BACKEND` 覆盖）。

## 现状

- `app/page.tsx`：手写 SSE 解析（fetch + ReadableStream），渲染 `tool_call/observation/answer_delta/final` 事件。
- 仅验证链路，样式极简；trace 面板是后续「手搓 vs LangGraph」对比的展示位。
