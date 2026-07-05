"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  EvidencePanels,
  MemoryBadge,
  SpoilerBadge,
  PANEL_LABELS,
  renderPanelByName,
  type PanelHandlers,
} from "./evidence-panels";

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
  | { kind: "progress"; tool: string; summary: string; current?: number; total?: number; note?: string }
  | { kind: "note"; text: string };
type ImageAttachment = {
  uri: string;
  filename?: string;
  mime_type?: string;
  size?: number;
  preview_url?: string;
};
type PendingImage = { id: string; file: File; preview: string };
type Msg = { role: "user" | "assistant"; content: string; attachments?: ImageAttachment[]; evidence?: EvidenceMap };
type EvidenceMap = Record<string, Record<string, any>[]>;

// [[panel:tool_name]]：LLM 在正文中锚定证据面板的位置（豆包/Gemini 式 inline 卡片）
const PANEL_MARK = /\[\[panel:([a-z_]+)\]\]/g;

function inlinePanelNames(content: string, evidence?: EvidenceMap): string[] {
  const names: string[] = [];
  for (const m of content.matchAll(PANEL_MARK)) {
    const name = m[1];
    if (!names.includes(name) && (evidence?.[name]?.length ?? 0) > 0 && PANEL_LABELS[name]) names.push(name);
  }
  return names;
}

/** assistant 正文：按 [[panel:xxx]] 把 markdown 切段，把对应面板嵌进文字流的相应位置。 */
function AssistantContent({
  content,
  evidence,
  handlers,
}: {
  content: string;
  evidence?: EvidenceMap;
  handlers: PanelHandlers;
}) {
  const parts = content.split(/\[\[panel:([a-z_]+)\]\]/);
  const used = new Set<string>();
  const nodes: ReactNode[] = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      if (parts[i].trim()) nodes.push(<Markdown text={parts[i]} key={`md-${i}`} />);
      continue;
    }
    const name = parts[i];
    const rows = evidence?.[name] ?? [];
    if (!used.has(name) && rows.length && PANEL_LABELS[name]) {
      used.add(name);
      nodes.push(
        <div className="inline-panel" key={`panel-${name}-${i}`}>
          {renderPanelByName(name, rows, handlers)}
        </div>,
      );
    }
    // 无数据/重复的标记直接吞掉，不渲染
  }
  return <>{nodes}</>;
}
type SpoilerState = {
  mode?: string;
  memory_default?: string;
  soft_warning?: boolean;
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
type ChatSession = {
  id: string;
  title: string;
  updated_at?: string;
  created_at?: string;
  message_count?: number;
};

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
    ["where_to_watch", "正版观看"],
    ["get_anime_release_feeds", "离线资源/RSS"],
    ["get_bangumi_index", "Bangumi目录"],
    ["review_subject", "评价矩阵"],
    ["get_broadcast_calendar", "放送日历"],
    ["get_airing_progress", "追番进度"],
    ["route_image_source", "图片来源路由"],
    ["extract_visual_text", "OCR 结构化"],
    ["recommend_by_visual_style", "视觉推荐"],
    ["search_image_source", "图片溯源"],
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
    where_to_watch: "查询正版入口",
    get_anime_release_feeds: "聚合离线RSS",
    get_bangumi_index: "读取Bangumi目录",
    review_subject: "融合评价证据",
    route_image_source: "路由图片来源",
    extract_visual_text: "读取图片文字",
    recommend_by_visual_style: "分析视觉风格",
    search_image_source: "搜索图片来源",
    analyze_video_frames: "分析视频帧",
    summarize_bilibili_video_content: "分析B站视频",
    get_broadcast_calendar: "查询放送日历",
    get_airing_progress: "计算追番进度",
    compare_user_taste: "计算同步率",
    build_aspect_profile: "更新口味画像",
    build_collection_dashboard: "生成收藏仪表盘",
    claim_check: "核对事实声明",
    get_user_memory: "读取记忆",
    remember_user_preference: "写入偏好记忆",
    prepare_downloader_push: "准备下载器推送",
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
        ) : t.kind === "progress" ? (
          <div key={i} className="trace-item progress-trace">
            <div className="trace-progress-head">
              <span>{friendlyToolName(t.tool)}</span>
              <small>{t.current != null && t.total ? `${t.current}/${t.total}` : ""}</small>
            </div>
            <div className="trace-progress-text">{t.summary}</div>
            {t.total ? (
              <div className="trace-progress-bar">
                <span style={{ width: `${Math.min(100, Math.round(((t.current ?? 0) / t.total) * 100))}%` }} />
              </div>
            ) : null}
            {t.note ? <div className="trace-progress-note">{t.note}</div> : null}
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
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [busy, setBusy] = useState(false);
  const answerRef = useRef("");
  const evidenceRef = useRef<EvidenceMap>({});  // finally 定型消息时读（state 闭包会是旧值）
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
    void refreshAuthSession().then(() => restoreLastSession());
  }, []);

  function csrfHeaders(extra?: Record<string, string>) {
    return {
      ...(extra ?? {}),
      ...(csrfToken.current ? { "x-otomo-csrf": csrfToken.current } : {}),
    };
  }

  async function httpErrorMessage(res: Response) {
    const retryAfter = res.headers.get("retry-after");
    const suffix = retryAfter ? `（${retryAfter} 秒后可重试）` : "";
    const payload = await res.clone().json().catch(() => null);
    const detail = payload?.detail || payload?.error;
    if (detail) return `${detail}${suffix}`;
    const text = await res.text().catch(() => "");
    return `${res.status} ${res.statusText || "request failed"}${text ? `: ${text.slice(0, 160)}` : ""}${suffix}`;
  }

  async function refreshAuthSession() {
    try {
      const res = await fetch(`${BACKEND}/auth/session`, { credentials: "include" });
      if (res.ok) {
        const payload = await res.json();
        authSessionId.current = payload.auth_session_id || "";
        csrfToken.current = payload.csrf_token || "";
        setAuth(payload);
        await loadSessions();
      }
    } catch {
      setAuth({ auth_session_id: authSessionId.current, authenticated: false });
    }
  }

  async function loadSessions() {
    try {
      const res = await fetch(`${BACKEND}/sessions`, { credentials: "include" });
      const payload = await res.json().catch(() => ({}));
      if (res.ok && payload.ok) setSessions(list(payload.sessions));
    } catch {
      /* 历史会话不是主流程，失败静默降级 */
    }
  }

  async function restoreLastSession() {
    const saved = window.localStorage.getItem("otomo.activeSessionId") || "";
    if (saved) await loadSession(saved);
  }

  function normalizeRestoredMessages(rows: any[]): Msg[] {
    return list(rows).map((row) => ({
      role: row.role === "assistant" ? "assistant" : "user",
      content: String(row.content || ""),
      attachments: list(row.attachments).map((img) => ({
        ...img,
        preview_url: img.preview_url?.startsWith("/") ? `${BACKEND}${img.preview_url}` : img.preview_url,
      })),
      // per-message evidence：恢复历史会话时 inline 面板照常锚定
      evidence: row.evidence && typeof row.evidence === "object" ? row.evidence : undefined,
    }));
  }

  async function loadSession(id: string) {
    if (!id || busy) return;
    try {
      const res = await fetch(`${BACKEND}/sessions/${encodeURIComponent(id)}/messages`, { credentials: "include" });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        if (res.status === 404 && window.localStorage.getItem("otomo.activeSessionId") === id) {
          sessionId.current = "";
          setActiveSessionId("");
          window.localStorage.removeItem("otomo.activeSessionId");
        }
        return;
      }
      sessionId.current = id;
      setActiveSessionId(id);
      window.localStorage.setItem("otomo.activeSessionId", id);
      setMessages(normalizeRestoredMessages(payload.messages));
      setEvidence(payload.evidence || {});
      setSources(list(payload.sources));
      const shortTerm = payload.state?.short_term || {};
      setSpoiler(shortTerm.spoiler || null);
      setMemory(shortTerm.memory || null);
      setTrace([]);
      setFollowups([]);
      setAnswer("");
      answerRef.current = "";
    } catch {
      /* ignore */
    }
  }

  async function deleteSession(id: string) {
    if (!id || busy) return;
    try {
      const res = await fetch(`${BACKEND}/sessions/${encodeURIComponent(id)}`, {
        method: "DELETE",
        credentials: "include",
        headers: csrfHeaders(),
      });
      if (!res.ok) return;
      if (sessionId.current === id) newChat();
      await loadSessions();
    } catch {
      /* ignore */
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
    evidenceRef.current = {};
    setFollowups([]);
    setAnswer("");
    answerRef.current = "";
    setBusy(true);
    if (!sessionId.current) {
      sessionId.current = crypto.randomUUID();  // 客户端 lazy 生成，避免 SSR mismatch
      setActiveSessionId(sessionId.current);
      window.localStorage.setItem("otomo.activeSessionId", sessionId.current);
    }

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
      if (!res.ok) throw new Error(await httpErrorMessage(res));
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
      const message = e instanceof Error ? e.message : String(e);
      setTrace((t) => [...t, { kind: "obs", name: "error", ok: false, summary: message }]);
      setUploadNotice({ tone: "bad", text: message });
    } finally {
      const final = answerRef.current;
      if (final) {
        const turnEvidence = evidenceRef.current;
        setMessages((m) => [...m, { role: "assistant", content: final, evidence: turnEvidence }]);
      }
      setAnswer("");
      setBusy(false);
      void loadSessions();
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

  async function postPrepareWrite(subjectId: number, subjectName: string, collectionType = 1) {
    try {
      const res = await fetch(`${BACKEND}/actions/prepare-write`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          subject_id: subjectId,
          subject_name: subjectName,
          collection_type: collectionType,
          reason: "从前端推荐/日历卡片一键加入想看",
        }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setTrace((t) => [...t, { kind: "obs", name: "prepare_write", ok: false, summary: payload.detail || payload.error || `HTTP ${res.status}` }]);
        return;
      }
      const data = payload.data;
      if (data?.memory) {
        setMemory(data.memory);
        setEvidence((prev) => ({
          ...prev,
          prepare_bangumi_write_action: [...(prev.prepare_bangumi_write_action ?? []), data],
        }));
      }
      setTrace((t) => [
        ...t,
        {
          kind: "obs",
          name: "prepare_write",
          ok: true,
          summary: data?.action?.summary || `已准备写回：${subjectName}`,
        },
      ]);
    } catch (e) {
      setTrace((t) => [...t, { kind: "obs", name: "prepare_write", ok: false, summary: String(e) }]);
    }
  }

  async function postPrepareDownloaderPush(payloadIn: Record<string, any>) {
    try {
      const res = await fetch(`${BACKEND}/actions/prepare-downloader-push`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          ...payloadIn,
          reason: "从前端 release/RSS 面板准备推送到下载器",
        }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setTrace((t) => [...t, { kind: "obs", name: "prepare_downloader_push", ok: false, summary: payload.detail || payload.error || `HTTP ${res.status}` }]);
        return;
      }
      const data = payload.data;
      if (data?.memory) {
        setMemory(data.memory);
        setEvidence((prev) => ({
          ...prev,
          prepare_downloader_push: [...(prev.prepare_downloader_push ?? []), data],
        }));
      }
      setTrace((t) => [
        ...t,
        {
          kind: "obs",
          name: "prepare_downloader_push",
          ok: true,
          summary: data?.action?.summary || "已准备下载器推送",
        },
      ]);
    } catch (e) {
      setTrace((t) => [...t, { kind: "obs", name: "prepare_downloader_push", ok: false, summary: String(e) }]);
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
        setTrace((t) => [
          ...t,
          {
            kind: "progress",
            tool: ev.tool || "",
            summary: ev.summary,
            current: ev.current ?? undefined,
            total: ev.total ?? undefined,
            note: ev.note || "",
          },
        ]);
        break;
      case "observation":
        setTrace((t) => [...t, { kind: "obs", name: ev.name, ok: ev.ok, summary: ev.summary }]);
        if (ev.data) {
          evidenceRef.current = {
            ...evidenceRef.current,
            [ev.name]: [...(evidenceRef.current[ev.name] ?? []), ev.data],
          };
          setEvidence(evidenceRef.current);
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
        evidenceRef.current = {
          ...evidenceRef.current,
          claim_check: [...(evidenceRef.current.claim_check ?? []), ev],
        };
        setEvidence(evidenceRef.current);
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
    setActiveSessionId("");
    window.localStorage.removeItem("otomo.activeSessionId");
    setMessages([]);
    setTrace([]);
    setSources([]);
    setEvidence({});
    evidenceRef.current = {};
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

  const panelHandlerProps = {
    onCritique: (q: string) => send(q),
    onConfirmAction: (id: string) => postAction("confirm", id),
    onCancelAction: (id: string) => postAction("cancel", id),
    onUndoAction: (id: string) => postAction("undo", id),
    onPrepareWrite: postPrepareWrite,
    onPrepareDownloaderPush: postPrepareDownloaderPush,
    onVisualFeedback: postVisualFeedback,
    onVisualCorrectionSearch: searchVisualCorrection,
  };
  const panelHandlers: PanelHandlers = { ...panelHandlerProps, devMode: evidenceMode === "dev" };

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
          <div className="session-strip">
            <button className="inline-action" onClick={newChat} disabled={busy}>新建</button>
            {sessions.slice(0, 6).map((s) => (
              <button
                key={s.id}
                className={`session-chip ${activeSessionId === s.id ? "active" : ""}`}
                onClick={() => loadSession(s.id)}
                disabled={busy}
                title={`${s.title || "新对话"} · ${s.updated_at || ""}`}
              >
                <span>{s.title || "新对话"}</span>
                <small>{s.message_count ?? 0}</small>
                <b
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    void deleteSession(s.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.stopPropagation();
                      void deleteSession(s.id);
                    }
                  }}
                  aria-label="删除会话"
                >
                  ×
                </b>
              </button>
            ))}
          </div>
        </div>
        <button className="ghost" onClick={newChat} disabled={busy}>+ 新对话</button>
      </div>

      <div className="grid">
        <div className="panel">
          <h3>对话</h3>
          {messages.length === 0 && !answer && (
            <div className="welcome">
              <div className="welcome-title">你的 ACGN 生活助手</div>
              <div className="welcome-sub">推荐 · 评价 · 追番 · 资源 · 识图，都可以直接问。试试：</div>
              <div className="welcome-chips">
                {[
                  "今天有什么番更新？",
                  "今天谁过生日？",
                  "孤独摇滚和轻音少女哪个好看？",
                  "本周放送时间表",
                  "推荐几部治愈系 galgame",
                  "最近全站什么番最火？",
                ].map((q) => (
                  <button key={q} className="chip" onClick={() => send(q)} disabled={busy}>{q}</button>
                ))}
              </div>
            </div>
          )}
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
                  <AssistantContent content={m.content} evidence={m.evidence} handlers={panelHandlers} />
                  {m.evidence && (
                    <EvidencePanels
                      evidence={m.evidence}
                      mode={evidenceMode}
                      collapsible
                      excludeNames={inlinePanelNames(m.content, m.evidence)}
                      {...panelHandlerProps}
                    />
                  )}
                </div>
              )}
            </div>
          ))}
          {answer && (
            <div className="msg assistant">
              <div className="role">Otomo</div>
              <div className="bubble">
                {/* 流式中面板标记逐字到达后即时嵌入（inline 锚定对打字中的回答同样生效） */}
                <AssistantContent content={answer + "▍"} evidence={evidence} handlers={panelHandlers} />
              </div>
            </div>
          )}
          <AnswerSupport sources={sources} evidence={evidence} />
          {hasEvidence && (
            <div className="evidence-toolbar">
              <div>
                <div className="evidence-toolbar-title">证据视图</div>
                <div className="evidence-toolbar-sub">
                  {evidenceMode === "user" ? "面板锚定在回答对应位置，未锚定的收进各消息的折叠区" : "开发模式在底部展示本轮全部原始证据"}
                </div>
              </div>
              <div className="segmented" aria-label="证据视图">
                <button className={evidenceMode === "user" ? "active" : ""} onClick={() => setEvidenceMode("user")}>简洁</button>
                <button className={evidenceMode === "dev" ? "active" : ""} onClick={() => setEvidenceMode("dev")}>开发</button>
              </div>
            </div>
          )}
          {/* user 模式：底部只保留 memory 确认流（展示型面板已进消息内）；dev 模式：本轮全家桶便于调试 */}
          <EvidencePanels
            evidence={evidence}
            mode={evidenceMode}
            excludeNames={evidenceMode === "user" ? Object.keys(PANEL_LABELS) : []}
            {...panelHandlerProps}
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
