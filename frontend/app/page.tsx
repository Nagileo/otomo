"use client";

import { useRef, useState } from "react";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

type Source = { title: string; url: string; source: string };
type TraceItem =
  | { kind: "call"; name: string; args: Record<string, unknown> }
  | { kind: "obs"; name: string; ok: boolean; summary: string }
  | { kind: "note"; text: string };
type Msg = { role: "user" | "assistant"; content: string };

export default function Home() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [followups, setFollowups] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const answerRef = useRef("");

  async function send(override?: string) {
    const q = (override ?? input).trim();
    if (!q || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    setTrace([]);
    setSources([]);
    setFollowups([]);
    setAnswer("");
    answerRef.current = "";
    setBusy(true);

    try {
      const res = await fetch(`${BACKEND}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: q }),
      });
      if (!res.body) throw new Error("no response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE 事件以空行分隔；后端用 \r\n\r\n，必须兼容 \r
        const blocks = buf.split(/\r?\n\r?\n/);
        buf = blocks.pop() ?? "";
        for (const block of blocks) {
          const dataLine = block.split(/\r?\n/).find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          try {
            handleEvent(JSON.parse(dataLine.slice(5).trim()));
          } catch {
            /* 忽略半包/ping */
          }
        }
      }
    } catch (e) {
      setTrace((t) => [...t, { kind: "obs", name: "error", ok: false, summary: String(e) }]);
    } finally {
      const final = answerRef.current;
      if (final) setMessages((m) => [...m, { role: "assistant", content: final }]);
      setAnswer("");
      setBusy(false);
    }
  }

  function handleEvent(ev: any) {
    switch (ev.type) {
      case "plan":
        setTrace((t) => [...t, { kind: "note", text: `📋 ${ev.summary}` }]);
        break;
      case "reflect":
        setTrace((t) => [...t, { kind: "note", text: ev.complete ? "↺ 反思：完整" : `↺ 反思：${ev.note}` }]);
        break;
      case "tool_call":
        setTrace((t) => [...t, { kind: "call", name: ev.name, args: ev.args }]);
        break;
      case "observation":
        setTrace((t) => [...t, { kind: "obs", name: ev.name, ok: ev.ok, summary: ev.summary }]);
        break;
      case "answer_delta":
        answerRef.current += ev.text;
        setAnswer(answerRef.current);
        break;
      case "final":
        setSources(ev.sources ?? []);
        if (ev.answer) {
          answerRef.current = ev.answer; // 以最终完整答案为准，覆盖流式残留（如泄漏被截断的片段）
          setAnswer(ev.answer);
        }
        break;
      case "followup":
        setFollowups(ev.questions ?? []);
        break;
      case "error":
        setTrace((t) => [...t, { kind: "obs", name: "error", ok: false, summary: ev.message }]);
        break;
    }
  }

  return (
    <div className="wrap">
      <div className="title">Otomo · 番组搭子</div>
      <div className="sub">ACGN Knowledge-Graph Agent — A1 骨架（Bangumi 多跳问答）</div>

      <div className="grid">
        <div className="panel">
          <h3>对话</h3>
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="role">{m.role === "user" ? "你" : "Otomo"}</div>
              <div className="bubble">{m.content}</div>
            </div>
          ))}
          {answer && (
            <div className="msg assistant">
              <div className="role">Otomo</div>
              <div className="bubble">{answer}▍</div>
            </div>
          )}
          {sources.length > 0 && (
            <div className="sources">
              来源：
              {sources.map((s, i) => (
                <span key={i}>
                  {" "}
                  <a href={s.url} target="_blank" rel="noreferrer">
                    {s.title}
                  </a>
                </span>
              ))}
            </div>
          )}
          {followups.length > 0 && (
            <div className="followups">
              {followups.map((q, i) => (
                <button key={i} className="chip" onClick={() => send(q)} disabled={busy}>
                  {q}
                </button>
              ))}
            </div>
          )}
          <div className="row">
            <input
              type="text"
              value={input}
              placeholder="例：白色相簿2 里 冬马和纱 的声优还配过哪些番？"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              disabled={busy}
            />
            <button onClick={() => send()} disabled={busy}>
              {busy ? "…" : "发送"}
            </button>
          </div>
        </div>

        <div className="panel">
          <h3>执行轨迹 (trace)</h3>
          {trace.length === 0 && !busy && (
            <div style={{ color: "var(--dim)", fontSize: 12 }}>工具调用与观察会实时出现在这里</div>
          )}
          {trace.map((t, i) =>
            t.kind === "call" ? (
              <div key={i} className="trace-item">
                <span className="name">→ {t.name}</span>{" "}
                <span className="args">{JSON.stringify(t.args)}</span>
              </div>
            ) : t.kind === "note" ? (
              <div key={i} className="trace-item" style={{ color: "var(--dim)" }}>
                {t.text}
              </div>
            ) : (
              <div key={i} className="trace-item">
                <span className={t.ok ? "ok" : "fail"}>{t.ok ? "✓" : "✗"}</span> {t.summary}
              </div>
            )
          )}
          {busy && <div className="trace-item" style={{ color: "var(--accent)" }}>● 处理中…（推荐类查询可能要十几秒）</div>}
        </div>
      </div>
    </div>
  );
}
