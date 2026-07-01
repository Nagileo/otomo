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
      <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={safeMarkdownUrl}>{text}</ReactMarkdown>
    </div>
  );
}

function safeMarkdownUrl(url: string) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (raw.startsWith("#") || raw.startsWith("/")) return raw;
  try {
    const parsed = new URL(raw);
    return ["http:", "https:", "mailto:"].includes(parsed.protocol) ? raw : "";
  } catch {
    return "";
  }
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
type PendingImage = { id: string; file: File; preview: string };
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
  recent_visual_feedback?: Record<string, any>[];
  updated_at?: string;
};
type AuthState = {
  auth_session_id?: string;
  authenticated?: boolean;
  username?: string;
  user_id?: number;
  oauth_configured?: boolean;
  dev_token_available?: boolean;
  csrf_token?: string;
};
type AuthNotice = { tone: "good" | "warn" | "bad"; text: string };
type UploadNotice = { tone: "good" | "warn" | "bad"; text: string };

const MAX_IMAGES = 4;
const SUPPORTED_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/jpg", "image/webp"]);

function list(value: any): any[] {
  return Array.isArray(value) ? value : [];
}

function sourceHost(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function evidenceSummary(evidence: EvidenceMap) {
  const rows = [
    ["recommend_subjects", "推荐候选"],
    ["season_guide_brief", "季番导视"],
    ["review_subject", "评价矩阵"],
    ["identify_acgn_screenshot", "截图识别"],
    ["extract_visual_text", "OCR 结构化"],
    ["recommend_by_visual_style", "视觉推荐"],
    ["search_image_source", "图片溯源"],
    ["route_image_source", "图片来源路由"],
    ["analyze_video_frames", "视频帧分析"],
    ["summarize_bilibili_video_content", "B站视频分析"],
    ["compare_user_taste", "同步率"],
    ["build_aspect_profile", "Aspect 画像"],
    ["build_collection_dashboard", "收藏仪表盘"],
    ["episode_buzz_radar", "分集口碑"],
    ["explore_voice_network", "角色/声优网络"],
    ["claim_check", "事实校验"],
  ];
  return rows
    .map(([key, label]) => ({ key, label, count: list(evidence[key]).length }))
    .filter((item) => item.count > 0);
}

function AnswerSupport({ sources, evidence }: { sources: Source[]; evidence: EvidenceMap }) {
  const summary = evidenceSummary(evidence);
  if (!sources.length && !summary.length) return null;
  const compactSources = sources.slice(0, 6);
  const visualSources = sources.filter((s) => s.image);
  return (
    <section className="answer-support">
      <div className="support-head">
        <div>
          <div className="support-title">回答支撑</div>
          <div className="support-sub">证据优先展示在下方卡片；外链仅作追溯入口</div>
        </div>
      </div>
      {summary.length > 0 && (
        <div className="support-pills">
          {summary.map((item) => (
            <span className="support-pill" key={item.key}>{item.label} {item.count}</span>
          ))}
        </div>
      )}
      {compactSources.length > 0 && (
        <div className="source-links">
          {compactSources.map((s, i) => (
            <a key={`${s.url}-${i}`} href={s.url} target="_blank" rel="noreferrer" title={s.title}>
              <span>{s.source || sourceHost(s.url) || "source"}</span>
              {s.title}
            </a>
          ))}
        </div>
      )}
      {visualSources.length > 0 && (
        <details className="source-detail">
          <summary>查看相关图片卡片（{visualSources.length}）</summary>
          <div className="src-cards compact">
            {visualSources.map((s, i) => (
              <a key={`${s.url}-${i}`} className="src-card" href={s.url} target="_blank" rel="noreferrer" title={s.title}>
                <img src={s.image} alt="" loading="lazy" />
                <span className="src-title">{s.title}</span>
              </a>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

function friendlyToolName(name: string) {
  const map: Record<string, string> = {
    recommend_subjects: "生成推荐候选",
    season_guide_brief: "整理季番导视",
    review_subject: "融合评价证据",
    identify_acgn_screenshot: "识别截图",
    extract_visual_text: "读取图片文字",
    recommend_by_visual_style: "分析视觉风格",
    search_image_source: "搜索图片来源",
    route_image_source: "路由图片来源",
    analyze_video_frames: "分析视频帧",
    summarize_bilibili_video_content: "分析B站视频",
    compare_user_taste: "计算同步率",
    build_aspect_profile: "更新口味画像",
    build_collection_dashboard: "生成收藏仪表盘",
    claim_check: "核对事实声明",
    get_user_memory: "读取记忆",
    remember_user_preference: "写入偏好记忆",
  };
  return map[name] || name.replaceAll("_", " ");
}

function TracePanel({
  trace,
  busy,
  mode,
  onModeChange,
}: {
  trace: TraceItem[];
  busy: boolean;
  mode: "summary" | "dev";
  onModeChange: (mode: "summary" | "dev") => void;
}) {
  const calls = trace.filter((t) => t.kind === "call");
  const observations = trace.filter((t) => t.kind === "obs");
  const failures = observations.filter((t) => t.kind === "obs" && !t.ok);
  const visibleTrace = mode === "summary" ? trace.filter((t) => t.kind !== "call").slice(-10) : trace;
  return (
    <div className="panel trace-panel">
      <div className="panel-title-row">
        <h3>执行过程</h3>
        <div className="segmented">
          <button className={mode === "summary" ? "active" : ""} onClick={() => onModeChange("summary")}>简洁</button>
          <button className={mode === "dev" ? "active" : ""} onClick={() => onModeChange("dev")}>开发</button>
        </div>
      </div>
      {trace.length === 0 && !busy && (
        <div className="trace-empty">工具调用与证据状态会实时出现在这里</div>
      )}
      {trace.length > 0 && (
        <div className="trace-metrics">
          <span>工具 {calls.length}</span>
          <span>观察 {observations.length}</span>
          <span className={failures.length ? "bad" : "good"}>异常 {failures.length}</span>
        </div>
      )}
      {mode === "summary" && calls.length > 0 && (
        <details className="trace-detail folded">
          <summary>本轮调用了 {calls.length} 个工具</summary>
          <div className="tool-list">
            {calls.map((t, i) => <span key={`${t.name}-${i}`}>{friendlyToolName(t.name)}</span>)}
          </div>
        </details>
      )}
      {visibleTrace.map((t, i) =>
        t.kind === "call" ? (
          <div key={i} className="trace-item">
            <details className="trace-detail">
              <summary className="name">→ {friendlyToolName(t.name)}</summary>
              <span className="args">{JSON.stringify(t.args)}</span>
            </details>
          </div>
        ) : t.kind === "note" ? (
          <div key={i} className="trace-item muted">
            {t.text}
          </div>
        ) : (
          <div key={i} className="trace-item">
            <span className={t.ok ? "ok" : "fail"}>{t.ok ? "✓" : "✗"}</span> {t.summary}
          </div>
        )
      )}
      {busy && <div className="trace-item processing">● 处理中…（推荐类查询可能要十几秒）</div>}
    </div>
  );
}

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
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [traceMode, setTraceMode] = useState<"summary" | "dev">("summary");
  const [evidenceMode, setEvidenceMode] = useState<"user" | "dev">("user");
  const [authNotice, setAuthNotice] = useState<AuthNotice | null>(null);
  const [uploadNotice, setUploadNotice] = useState<UploadNotice | null>(null);
  const [busy, setBusy] = useState(false);
  const answerRef = useRef("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const authSessionId = useRef("");
  const csrfToken = useRef("");
  const sessionId = useRef("");  // 多轮会话 id（首次发送时生成；"新对话"会重置）
  const lastQ = useRef("");      // 最近一次用户问题（剧透 followup chips 重发用）

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const authStatus = params.get("bangumi_auth");
    if (authStatus === "ok") {
      setAuthNotice({ tone: "good", text: `Bangumi 登录成功${params.get("user") ? `：@${params.get("user")}` : ""}` });
    } else if (authStatus === "error") {
      setAuthNotice({ tone: "bad", text: `Bangumi 登录失败：${params.get("error") || "unknown"}` });
    }
    if (authStatus) {
      params.delete("bangumi_auth");
      params.delete("user");
      params.delete("error");
      const query = params.toString();
      const cleanUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
      window.history.replaceState(null, "", cleanUrl);
    }
    void refreshAuthSession();
  }, []);

  function csrfHeaders(extra?: Record<string, string>) {
    return {
      ...(extra ?? {}),
      ...(csrfToken.current ? { "x-otomo-csrf": csrfToken.current } : {}),
    };
  }

  async function refreshAuthSession() {
    try {
      const res = await fetch(`${BACKEND}/auth/session`, { credentials: "include" });
      if (res.ok) {
        const payload = await res.json();
        authSessionId.current = payload.auth_session_id || "";
        csrfToken.current = payload.csrf_token || "";
        setAuth(payload);
      }
    } catch {
      setAuth({ auth_session_id: authSessionId.current, authenticated: false });
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

  function imageId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function isSupportedImage(file: File) {
    if (SUPPORTED_IMAGE_TYPES.has(file.type)) return true;
    const name = file.name.toLowerCase();
    return [".png", ".jpg", ".jpeg", ".webp"].some((ext) => name.endsWith(ext));
  }

  function addPendingImages(files: FileList | null) {
    if (!files?.length) {
      setUploadNotice({ tone: "warn", text: "没有选择图片文件" });
      return;
    }
    const selected = Array.from(files);
    const valid = selected.filter(isSupportedImage);
    const invalid = selected.length - valid.length;
    setPendingImages((prev) => {
      const room = Math.max(MAX_IMAGES - prev.length, 0);
      const accepted = valid.slice(0, room);
      const next = accepted.map((file) => ({
        id: imageId(),
        file,
        preview: URL.createObjectURL(file),
      }));
      const skipped = valid.length - accepted.length;
      const parts = [];
      if (accepted.length) parts.push(`已选择 ${accepted.length} 张截图`);
      if (invalid) parts.push(`${invalid} 个文件格式不支持`);
      if (skipped) parts.push(`已达到最多 ${MAX_IMAGES} 张`);
      setUploadNotice({
        tone: accepted.length ? (invalid || skipped ? "warn" : "good") : "bad",
        text: parts.join("，") || "没有可用图片；仅支持 png/jpeg/webp",
      });
      return [...prev, ...next];
    });
  }

  function removePendingImage(id: string) {
    setPendingImages((prev) => {
      const target = prev.find((img) => img.id === id);
      if (target) URL.revokeObjectURL(target.preview);
      return prev.filter((img) => img.id !== id);
    });
  }

  function clearPendingImages() {
    setPendingImages((prev) => {
      prev.forEach((img) => URL.revokeObjectURL(img.preview));
      return [];
    });
    setUploadNotice(null);
  }

  async function uploadPendingImages(): Promise<ImageAttachment[]> {
    if (!pendingImages.length) return [];
    const uploaded: ImageAttachment[] = [];
    for (const image of pendingImages) {
      const dataUrl = await readAsDataUrl(image.file);
      const res = await fetch(`${BACKEND}/uploads/image`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ data_url: dataUrl, filename: image.file.name }),
      });
      if (!res.ok) throw new Error(await res.text());
      const payload = await res.json();
      uploaded.push({
        uri: payload.uri,
        filename: payload.filename,
        mime_type: payload.mime_type,
        size: payload.size,
        preview_url: payload.preview_url ? `${BACKEND}${payload.preview_url}` : undefined,
      });
    }
    return uploaded;
  }

  async function send(override?: string, spoilerMode?: string) {
    let q = (override ?? input).trim();
    const shouldUseImage = pendingImages.length > 0 && !override;
    if (!q && shouldUseImage) {
      q = pendingImages.length > 1 ? "请综合识别这些截图，并回锚 Bangumi 候选。" : "请识别这张截图，并回锚 Bangumi 候选。";
    }
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
      const attachments = shouldUseImage ? await uploadPendingImages() : [];
      if (shouldUseImage) clearPendingImages();
      setMessages((m) => [...m, { role: "user", content: q, attachments }]);
      const res = await fetch(`${BACKEND}/chat`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          message: q,
          session_id: sessionId.current,
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
      setUploadNotice({ tone: "bad", text: String(e) });
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
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ action_id: actionId }),
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

  async function postVisualFeedback(payload: Record<string, any>) {
    try {
      const res = await fetch(`${BACKEND}/feedback/visual`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        setTrace((t) => [...t, { kind: "obs", name: "visual_feedback", ok: false, summary: data.detail || data.error || `HTTP ${res.status}` }]);
        return;
      }
      if (data.memory) setMemory(data.memory);
      setTrace((t) => [
        ...t,
        {
          kind: "obs",
          name: "visual_feedback",
          ok: true,
          summary: payload.corrected_subject_id
            ? `已记录视觉纠错：正确条目 ${payload.corrected_subject_name || payload.corrected_subject_id}`
            : payload.signal === "correct"
              ? "已记录：截图识别正确"
              : payload.signal === "ambiguous"
                ? "已记录：截图识别不确定"
                : "已记录：截图识别不对",
        },
      ]);
    } catch (e) {
      setTrace((t) => [...t, { kind: "obs", name: "visual_feedback", ok: false, summary: String(e) }]);
    }
  }

  async function searchVisualCorrection(query: string, subjectType?: string): Promise<Record<string, any>[]> {
    try {
      const res = await fetch(`${BACKEND}/feedback/visual/search_subjects`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ keyword: query, subject_type: subjectType || "anime", limit: 8 }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setTrace((t) => [...t, { kind: "obs", name: "visual_search", ok: false, summary: payload.detail || payload.error || `HTTP ${res.status}` }]);
        return [];
      }
      return list(payload.subjects);
    } catch (e) {
      setTrace((t) => [...t, { kind: "obs", name: "visual_search", ok: false, summary: String(e) }]);
      return [];
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
      case "progress":
        setTrace((t) => [...t, { kind: "note", text: `⏱ ${ev.summary}` }]);
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
        const verifiableClaims = Number(ev.supported_count || 0) + Number(ev.unsupported_count || 0);
        setTrace((t) => [
          ...t,
          {
            kind: "note",
            text: verifiableClaims
              ? `证据校验：support ${(Number(ev.support_rate || 0) * 100).toFixed(0)}% · unsupported ${ev.unsupported_count ?? 0}`
              : "证据校验：无强 canonical 硬事实需要自动回退",
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
    clearPendingImages();
    setAnswer("");
    answerRef.current = "";
  }

  async function startBangumiLogin() {
    if (!authSessionId.current || !csrfToken.current) await refreshAuthSession();
    if (auth && !auth.oauth_configured) {
      setAuthNotice({
        tone: "warn",
        text: auth.dev_token_available
          ? "当前未配置 Bangumi OAuth 应用；本地开发可先使用 BANGUMI_TOKEN 绑定。"
          : "当前未配置 Bangumi OAuth 应用，也未检测到 BANGUMI_TOKEN。",
      });
      return;
    }
    const res = await fetch(`${BACKEND}/auth/bangumi/login`, { credentials: "include" });
    const payload = await res.json().catch(() => ({}));
    if (payload.authorization_url) window.location.href = payload.authorization_url;
    else {
      const msg = payload.detail || "OAuth 未配置";
      setAuthNotice({ tone: "bad", text: `无法发起 Bangumi 登录：${msg}` });
      setTrace((t) => [...t, { kind: "obs", name: "bangumi_login", ok: false, summary: msg }]);
    }
  }

  async function loginWithLocalToken() {
    if (!authSessionId.current || !csrfToken.current) await refreshAuthSession();
    try {
      const res = await fetch(`${BACKEND}/auth/dev-token-login`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({}),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setAuthNotice({ tone: "bad", text: `本地 Token 绑定失败：${payload.detail || payload.error || res.status}` });
        return;
      }
      setAuth(payload.identity);
      authSessionId.current = payload.identity?.auth_session_id || authSessionId.current;
      csrfToken.current = payload.identity?.csrf_token || csrfToken.current;
      setAuthNotice({ tone: "good", text: `已使用本地 BANGUMI_TOKEN 绑定：@${payload.identity?.username || "unknown"}` });
    } catch (e) {
      setAuthNotice({ tone: "bad", text: `本地 Token 绑定失败：${String(e)}` });
    }
  }

  async function logoutBangumi() {
    await fetch(`${BACKEND}/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({}),
    });
    authSessionId.current = "";
    csrfToken.current = "";
    setAuth({ auth_session_id: authSessionId.current, authenticated: false });
    setAuthNotice({ tone: "warn", text: "已退出当前浏览器会话的 Bangumi 绑定" });
    setMemory(null);
  }

  const hasEvidence = Object.values(evidence).some((rows) => list(rows).length > 0);

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
                <button className="inline-action" onClick={startBangumiLogin} disabled={busy}>
                  {auth?.oauth_configured ? "OAuth 绑定" : "绑定"}
                </button>
                {auth?.dev_token_available && (
                  <button className="inline-action" onClick={loginWithLocalToken} disabled={busy}>本地 Token</button>
                )}
              </>
            )}
          </div>
          {authNotice && <div className={`auth-notice ${authNotice.tone}`}>{authNotice.text}</div>}
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
          <AnswerSupport sources={sources} evidence={evidence} />
          {hasEvidence && (
            <div className="evidence-toolbar">
              <div>
                <div className="evidence-toolbar-title">证据视图</div>
                <div className="evidence-toolbar-sub">
                  {evidenceMode === "user" ? "默认只展示可读结论和可操作项" : "开发模式展示原始证据、校验和映射细节"}
                </div>
              </div>
              <div className="segmented" aria-label="证据视图">
                <button className={evidenceMode === "user" ? "active" : ""} onClick={() => setEvidenceMode("user")}>简洁</button>
                <button className={evidenceMode === "dev" ? "active" : ""} onClick={() => setEvidenceMode("dev")}>开发</button>
              </div>
            </div>
          )}
          <EvidencePanels
            evidence={evidence}
            mode={evidenceMode}
            onCritique={(q) => send(q)}
            onConfirmAction={(id) => postAction("confirm", id)}
            onCancelAction={(id) => postAction("cancel", id)}
            onUndoAction={(id) => postAction("undo", id)}
            onVisualFeedback={postVisualFeedback}
            onVisualCorrectionSearch={searchVisualCorrection}
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
              multiple
              accept="image/*"
              className="file-input"
              onChange={(e) => {
                addPendingImages(e.target.files);
                e.currentTarget.value = "";
              }}
              disabled={busy}
            />
            <button
              className="icon-button"
              title={`上传截图（最多 ${MAX_IMAGES} 张）`}
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || pendingImages.length >= MAX_IMAGES}
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
          {uploadNotice && (
            <div className={`upload-notice ${uploadNotice.tone}`}>{uploadNotice.text}</div>
          )}
          {pendingImages.length > 0 && (
            <div className="pending-images">
              <div className="pending-head">
                <span>待上传截图 {pendingImages.length}/{MAX_IMAGES}</span>
                <button className="inline-action" onClick={clearPendingImages} disabled={busy}>清空</button>
              </div>
              <div className="pending-grid">
                {pendingImages.map((img) => (
                  <div className="pending-card" key={img.id}>
                    <img src={img.preview} alt={img.file.name || "待上传截图"} />
                    <div className="pending-meta">
                      <div className="card-title">{img.file.name}</div>
                      <div className="card-meta">{Math.round(img.file.size / 1024)} KB</div>
                    </div>
                    <button className="remove-image" onClick={() => removePendingImage(img.id)} disabled={busy} aria-label="移除截图">×</button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <TracePanel trace={trace} busy={busy} mode={traceMode} onModeChange={setTraceMode} />
      </div>
    </div>
  );
}
