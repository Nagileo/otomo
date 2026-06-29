"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { EvidencePanels, MemoryBadge, SpoilerBadge } from "./evidence-panels";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

type Source = { title: string; url: string; source: string; image?: string };

function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
type TraceItem =
  | { kind: "call"; name: string; args: Record<string, unknown> }
  | { kind: "obs"; name: string; ok: boolean; summary: string }
  | { kind: "note"; text: string };
type ImageAttachment = {
  uri: string;
  filename?: string;
  mime_type?: string;
  size?: number;
  preview_url?: string;
};
type PendingImage = { file: File; preview: string };
type Msg = { role: "user" | "assistant"; content: string; attachments?: ImageAttachment[] };
type EvidenceMap = Record<string, Record<string, any>[]>;
type SpoilerState = {
  mode?: string;
  progress_episode?: number;
  pending_followup?: boolean;
  followup_question?: string;
};
type MemoryState = {
  username?: string;
  likes?: Record<string, any>[];
  dislikes?: Record<string, any>[];
  spoiler_default?: string;
  progress?: Record<string, any>;
  recent_feedback?: Record<string, any>[];
  profile_snapshot?: Record<string, any>;
  aspect_profiles?: Record<string, any>;
  pending_write_actions?: Record<string, any>[];
  recent_decisions?: Record<string, any>[];
  watch_plan?: Record<string, any>[];
  recommendation_lists?: Record<string, any>[];
  weekly_digest_subscription?: Record<string, any>;
  inbox?: Record<string, any>[];
  updated_at?: string;
};
type AuthState = {
  auth_session_id?: string;
  authenticated?: boolean;
  username?: string;
  user_id?: number;
};

export default function Home() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [evidence, setEvidence] = useState<EvidenceMap>({});
  const [spoiler, setSpoiler] = useState<SpoilerState | null>(null);
  const [memory, setMemory] = useState<MemoryState | null>(null);
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [followups, setFollowups] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [pendingImage, setPendingImage] = useState<PendingImage | null>(null);
  const [busy, setBusy] = useState(false);
  const answerRef = useRef("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const authSessionId = useRef("");
  const sessionId = useRef("");  // 多轮会话 id（首次发送时生成；"新对话"会重置）
  const lastQ = useRef("");      // 最近一次用户问题（剧透 followup chips 重发用）

  useEffect(() => {
    let sid = localStorage.getItem("otomo_auth_session_id") || "";
    if (!sid) {
      sid = crypto.randomUUID();
      localStorage.setItem("otomo_auth_session_id", sid);
    }
    authSessionId.current = sid;
    void refreshAuthSession(sid);
  }, []);

  async function refreshAuthSession(sid = authSessionId.current) {
    if (!sid) return;
    try {
      const res = await fetch(`${BACKEND}/auth/session?auth_session_id=${encodeURIComponent(sid)}`);
      if (res.ok) setAuth(await res.json());
    } catch {
      setAuth({ auth_session_id: sid, authenticated: false });
    }
  }

  function readAsDataUrl(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("read image failed"));
      reader.readAsDataURL(file);
    });
  }

  async function uploadPendingImage(): Promise<ImageAttachment[]> {
    if (!pendingImage) return [];
    const dataUrl = await readAsDataUrl(pendingImage.file);
    const res = await fetch(`${BACKEND}/uploads/image`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data_url: dataUrl, filename: pendingImage.file.name }),
    });
    if (!res.ok) throw new Error(await res.text());
    const payload = await res.json();
    return [{
      uri: payload.uri,
      filename: payload.filename,
      mime_type: payload.mime_type,
      size: payload.size,
      preview_url: payload.preview_url ? `${BACKEND}${payload.preview_url}` : undefined,
    }];
  }

  async function send(override?: string, spoilerMode?: string) {
    let q = (override ?? input).trim();
    const shouldUseImage = Boolean(pendingImage) && !override;
    if (!q && shouldUseImage) q = "请识别这张截图，并回锚 Bangumi 候选。";
    if (!q || busy) return;
    lastQ.current = q;
    setInput("");
    setTrace([]);
    setSources([]);
    setEvidence({});
    setFollowups([]);
    setAnswer("");
    answerRef.current = "";
    setBusy(true);
    if (!sessionId.current) sessionId.current = crypto.randomUUID();  // 客户端 lazy 生成，避免 SSR mismatch

    try {
      const attachments = shouldUseImage ? await uploadPendingImage() : [];
      if (shouldUseImage) setPendingImage(null);
      setMessages((m) => [...m, { role: "user", content: q, attachments }]);
      const res = await fetch(`${BACKEND}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: q,
          session_id: sessionId.current,
          auth_session_id: authSessionId.current,
          attachments,
          ...(spoilerMode ? { spoiler_mode: spoilerMode } : {}),
        }),
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

  async function postAction(kind: "confirm" | "cancel" | "undo", actionId: string) {
    try {
      const res = await fetch(`${BACKEND}/actions/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_id: actionId, auth_session_id: authSessionId.current }),
      });
      const payload = await res.json();
      if (!payload.ok) {
        setTrace((t) => [...t, { kind: "obs", name: `action_${kind}`, ok: false, summary: payload.error || "action failed" }]);
        return;
      }
      const mem = payload.data?.memory;
      if (mem) {
        setMemory(mem);
        setEvidence((prev) => ({ ...prev, get_user_memory: [mem] }));
      }
      setTrace((t) => [
        ...t,
        { kind: "obs", name: `action_${kind}`, ok: true, summary: payload.data?.message || "ok" },
      ]);
    } catch (e) {
      setTrace((t) => [...t, { kind: "obs", name: `action_${kind}`, ok: false, summary: String(e) }]);
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
        if (ev.data) {
          setEvidence((prev) => ({
            ...prev,
            [ev.name]: [...(prev[ev.name] ?? []), ev.data],
          }));
        }
        break;
      case "claim_check":
        setTrace((t) => [
          ...t,
          {
            kind: "note",
            text: `证据校验：support ${(Number(ev.support_rate || 0) * 100).toFixed(0)}% · unsupported ${ev.unsupported_count ?? 0}`,
          },
        ]);
        setEvidence((prev) => ({
          ...prev,
          claim_check: [...(prev.claim_check ?? []), ev],
        }));
        break;
      case "state":
        if (ev.scope === "spoiler") setSpoiler(ev.snapshot ?? null);
        if (ev.scope === "memory") setMemory(ev.snapshot ?? null);
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

  function newChat() {
    sessionId.current = "";  // 重置 → 下次发送会生成新会话 id（清空多轮上下文）
    setMessages([]);
    setTrace([]);
    setSources([]);
    setEvidence({});
    setSpoiler(null);
    setMemory(null);
    setFollowups([]);
    setPendingImage(null);
    setAnswer("");
    answerRef.current = "";
  }

  async function startBangumiLogin() {
    if (!authSessionId.current) {
      authSessionId.current = crypto.randomUUID();
      localStorage.setItem("otomo_auth_session_id", authSessionId.current);
    }
    const res = await fetch(`${BACKEND}/auth/bangumi/login?auth_session_id=${encodeURIComponent(authSessionId.current)}`);
    const payload = await res.json();
    if (payload.authorization_url) window.location.href = payload.authorization_url;
    else setTrace((t) => [...t, { kind: "obs", name: "bangumi_login", ok: false, summary: payload.detail || "OAuth 未配置" }]);
  }

  async function logoutBangumi() {
    await fetch(`${BACKEND}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auth_session_id: authSessionId.current }),
    });
    setAuth({ auth_session_id: authSessionId.current, authenticated: false });
    setMemory(null);
  }

  return (
    <div className="wrap">
      <div className="topbar">
        <div>
          <div className="title">Otomo · 番组搭子</div>
          <div className="sub">ACGN 知识图谱 Agent — 多跳问答 / 跨媒体追溯 / 语义 RAG / 个性化推荐</div>
          <SpoilerBadge spoiler={spoiler} />
          <MemoryBadge memory={memory} />
          <div className="auth-state">
            {auth?.authenticated ? (
              <>
                <span className="badge good">Bangumi @{auth.username}</span>
                <button className="inline-action" onClick={logoutBangumi} disabled={busy}>退出</button>
              </>
            ) : (
              <>
                <span className="badge dim">Bangumi 未绑定</span>
                <button className="inline-action" onClick={startBangumiLogin} disabled={busy}>绑定</button>
              </>
            )}
          </div>
        </div>
        <button className="ghost" onClick={newChat} disabled={busy}>+ 新对话</button>
      </div>

      <div className="grid">
        <div className="panel">
          <h3>对话</h3>
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="role">{m.role === "user" ? "你" : "Otomo"}</div>
              {m.role === "user" ? (
                <div className="bubble">
                  {m.attachments?.length ? (
                    <div className="msg-images">
                      {m.attachments.map((img, j) => (
                        <img key={`${img.uri}-${j}`} src={img.preview_url} alt={img.filename || "uploaded image"} />
                      ))}
                    </div>
                  ) : null}
                  {m.content}
                </div>
              ) : (
                <div className="bubble">
                  <Markdown text={m.content} />
                </div>
              )}
            </div>
          ))}
          {answer && (
            <div className="msg assistant">
              <div className="role">Otomo</div>
              <div className="bubble">
                <Markdown text={answer + "▍"} />
              </div>
            </div>
          )}
          {sources.length > 0 && (
            <div className="sources">
              <div className="src-label">来源 / 相关</div>
              <div className="src-cards">
                {sources.map((s, i) => (
                  <a key={i} className="src-card" href={s.url} target="_blank" rel="noreferrer" title={s.title}>
                    {s.image ? <img src={s.image} alt="" loading="lazy" /> : <div className="noimg" />}
                    <span className="src-title">{s.title}</span>
                  </a>
                ))}
              </div>
            </div>
          )}
          <EvidencePanels
            evidence={evidence}
            onCritique={(q) => send(q)}
            onConfirmAction={(id) => postAction("confirm", id)}
            onCancelAction={(id) => postAction("cancel", id)}
            onUndoAction={(id) => postAction("undo", id)}
          />
          {spoiler?.progress_episode != null && (
            <div className="filter-note">🔒 已按第 {spoiler.progress_episode} 集进度过滤分集剧情内容</div>
          )}
          {spoiler?.pending_followup && (
            <div className="followups">
              <span className="followup-q">{spoiler.followup_question || "这个问题可能涉及后续剧情/结局，你希望："}</span>
              <button className="chip" onClick={() => send(lastQ.current, "none")} disabled={busy}>🚫 无剧透</button>
              <button className="chip" onClick={() => send(lastQ.current, "mild")} disabled={busy}>🌓 轻微剧透</button>
              <button className="chip" onClick={() => send(lastQ.current, "full")} disabled={busy}>💥 完整剧透</button>
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
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="file-input"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                setPendingImage({ file, preview: URL.createObjectURL(file) });
                e.currentTarget.value = "";
              }}
              disabled={busy}
            />
            <button
              className="icon-button"
              title="上传截图"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
            >
              图
            </button>
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
          {pendingImage && (
            <div className="pending-image">
              <img src={pendingImage.preview} alt="待上传截图" />
              <div>
                <div className="card-title">{pendingImage.file.name}</div>
                <div className="card-meta">{Math.round(pendingImage.file.size / 1024)} KB · 发送时上传</div>
              </div>
              <button className="ghost" onClick={() => setPendingImage(null)} disabled={busy}>移除</button>
            </div>
          )}
        </div>

        <div className="panel">
          <h3>执行轨迹 (trace)</h3>
          {trace.length === 0 && !busy && (
            <div style={{ color: "var(--dim)", fontSize: 12 }}>工具调用与观察会实时出现在这里</div>
          )}
          {trace.map((t, i) =>
            t.kind === "call" ? (
              <div key={i} className="trace-item">
                <details className="trace-detail">
                  <summary className="name">→ {t.name}</summary>
                  <span className="args">{JSON.stringify(t.args)}</span>
                </details>
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
