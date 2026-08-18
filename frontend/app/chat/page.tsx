"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ImagePlus, PanelRightOpen, Plus, Send, Square } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PageHeader } from "../../components/page-header";
import { OtomoAvatar, UserAvatar } from "../../components/identity-avatar";
import { useExperience } from "../../lib/experience";
import { TasteQuiz } from "../taste-quiz";
import {
  EvidencePanels,
  MemoryBadge,
  SpoilerBadge,
  PANEL_LABELS,
  renderPanelByName,
  type PanelHandlers,
} from "../evidence-panels";

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
type Msg = { role: "user" | "assistant"; content: string; attachments?: ImageAttachment[]; evidence?: EvidenceMap; sources?: Source[]; turnId?: string; feedback?: "up" | "down"; steps?: string[]; trace?: TraceItem[]; elapsedMs?: number };
type EvidenceMap = Record<string, Record<string, any>[]>;

// [[panel:tool_name]]：LLM 在正文中锚定证据面板的位置。
// [[panel:tool_name:anchor]]：带 anchor 的单项面板，season_guide_brief 用 subject_id 锚定单个新番卡。
const PANEL_MARK = /\[\[panel:([a-z_]+)(?::[^\]]*)?\]\]/g;

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
  const marker = /\[\[panel:([a-z_]+)(?::([^\]]+))?\]\]/g;
  const used = new Set<string>();
  const nodes: ReactNode[] = [];
  let last = 0;
  let idx = 0;
  for (const m of content.matchAll(marker)) {
    const start = m.index ?? 0;
    const before = content.slice(last, start);
    if (before.trim()) nodes.push(<Markdown text={before} key={`md-${idx++}`} />);
    const name = m[1];
    const anchor = (m[2] || "").trim() || undefined;
    const rows = evidence?.[name] ?? [];
    const key = anchor ? `${name}:${anchor}` : name;
    if (!used.has(key) && rows.length && PANEL_LABELS[name]) {
      used.add(key);
      nodes.push(
        <div className="inline-panel" key={`panel-${key}-${idx++}`}>
          {renderPanelByName(name, rows, handlers, anchor)}
        </div>,
      );
    }
    last = start + m[0].length;
  }
  const tail = content.slice(last);
  if (tail.trim()) nodes.push(<Markdown text={tail} key={`md-${idx++}`} />);
  return <>{nodes}</>;
}

function TraceEntries({ steps, trace }: { steps?: string[]; trace?: TraceItem[] }) {
  if (trace?.length) return <>
    {trace.map((item, i) => {
      if (item.kind === "call") return (
        <details className="message-trace-call" key={i}>
          <summary>准备：{friendlyToolName(item.name)}</summary>
          <pre>{JSON.stringify(item.args, null, 2)}</pre>
        </details>
      );
      if (item.kind === "obs") return <div className={item.ok ? "ok" : "bad"} key={i}>{item.ok ? "✓" : "✗"} {item.summary || friendlyToolName(item.name)}</div>;
      if (item.kind === "progress") return <div key={i}>↳ {item.summary}{item.note ? ` · ${item.note}` : ""}</div>;
      return <div key={i}>{item.text}</div>;
    })}
  </>;
  return <ol>{steps?.map((step, i) => <li key={i}>{step}</li>)}</ol>;
}

/** 生成中的状态也是可展开控件；只展示执行事件，不暴露模型的隐藏思维。 */
function AgentLiveStatus({ steps, trace, startedAt }: { steps: string[]; trace?: TraceItem[]; startedAt: number }) {
  const latest = steps[steps.length - 1] || "正在理解你的问题…";
  const secs = startedAt ? Math.max(0, Math.round((Date.now() - startedAt) / 1000)) : 0;
  return (
    <details className="live-status">
      <summary>
        <span className="live-dot" aria-hidden />
        <span className="live-text">{latest}</span>
        <span className="live-clock">{secs}s{steps.length > 1 ? ` · 第 ${steps.length} 步` : ""}</span>
        <span className="live-toggle">查看过程</span>
      </summary>
      <div className="message-trace live-trace">
        <TraceEntries steps={steps} trace={trace} />
      </div>
    </details>
  );
}

/** 完成后收敛成一行小字，点开回看它当时做了什么。 */
function AgentStepsFold({ steps, trace, elapsedMs }: { steps?: string[]; trace?: TraceItem[]; elapsedMs?: number }) {
  if (!steps?.length && !trace?.length) return null;
  const secs = elapsedMs ? Math.round(elapsedMs / 1000) : null;
  const count = trace?.length || steps?.length || 0;
  return (
    <details className="agent-steps">
      <summary>执行过程 · {secs ? `${secs} 秒` : "已完成"} · {count} 步</summary>
      <div className="message-trace">
        <TraceEntries steps={steps} trace={trace} />
      </div>
    </details>
  );
}

function MessageAvatar({ role, auth }: { role: "user" | "assistant"; auth?: AuthState | null }) {
  return role === "assistant"
    ? <OtomoAvatar className="message-avatar assistant" />
    : <UserAvatar className="message-avatar user" username={auth?.username} avatarUrl={auth?.avatar_url} />;
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
  inbox?: Record<string, any>[];
  recent_visual_feedback?: Record<string, any>[];
  updated_at?: string;
};
type AuthState = {
  authenticated?: boolean;
  username?: string;
  user_id?: number;
  avatar_url?: string;
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
  source?: string;
  source_label?: string;
  revision?: number;
  running?: boolean;
  activity_surface?: string;
  activity_run_id?: string;
  activity_started_at?: number;
  activity_is_current_device?: boolean;
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

function sessionActivityLabel(session: ChatSession) {
  const source = session.source_label || (session.source === "discord_import" ? "Discord 续聊" : "网页");
  const updated = String(session.updated_at || "").replace("T", " ").slice(5, 16);
  return updated ? `${source} · ${updated}` : source;
}

function evidenceSummary(evidence: EvidenceMap) {
  const rows = [
    ["season_guide_brief", "季番导视"],
    ["where_to_watch", "正版观看"],
    ["get_anime_release_feeds", "离线资源/RSS"],
    ["get_bangumi_index", "Bangumi目录"],
    ["review_subject", "评价矩阵"],
    ["get_broadcast_calendar", "放送日历"],
    ["get_airing_progress", "追番进度"],
    ["subject_dossier", "作品档案"],
    ["franchise_map", "IP图谱"],
    ["anime_music_themes", "OP/ED音乐"],
    ["search_anime_themes", "AnimeThemes"],
    ["search_image_source", "图片溯源"],
    ["summarize_bilibili_video_content", "B站视频分析"],
    ["episode_buzz_radar", "分集口碑"],
  ];
  return rows
    .map(([key, label]) => ({ key, label, count: list(evidence[key]).length }))
    .filter((item) => item.count > 0);
}

function AnswerSupport({ sources, evidence }: { sources: Source[]; evidence: EvidenceMap }) {
  const summary = evidenceSummary(evidence);
  const verifiableSources = sources.filter((source) => /^https?:\/\//i.test(String(source.url || "")));
  if (!verifiableSources.length) return null;
  const compactSources = verifiableSources.slice(0, 6);
  const visualSources = verifiableSources.filter((s) => s.image);
  const countLabel = `${compactSources.length} 个可打开来源`;
  return (
    <details className="answer-support">
      <summary className="support-summary">
        <span><strong>回答依据</strong><small>{countLabel}</small></span>
        <i aria-hidden>⌄</i>
      </summary>
      <div className="support-body">
        <p>这些链接是这条回答实际引用、可以直接打开核对的来源。</p>
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
                <span>{s.source || sourceHost(s.url) || "来源"}</span>
                {s.title}
              </a>
            ))}
          </div>
        )}
        {visualSources.length > 0 && (
          <details className="source-detail">
            <summary>相关图片（{visualSources.length}）</summary>
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
      </div>
    </details>
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
    route_subject_sources: "规划源路由",
    route_image_source: "路由图片来源",
    extract_visual_text: "读取图片文字",
    recommend_by_visual_style: "分析视觉风格",
    search_image_source: "搜索图片来源",
    analyze_video_frames: "分析视频帧",
    summarize_bilibili_video_content: "分析B站视频",
    get_broadcast_calendar: "查询放送日历",
    get_airing_progress: "计算追番进度",
    watch_cockpit: "汇总追番驾驶舱",
    subject_dossier: "生成作品档案",
    franchise_map: "构建IP图谱",
    monthly_watch_report: "生成月度报告",
    anime_music_themes: "融合OP/ED音乐",
    search_anime_themes: "查询AnimeThemes",
    plan_watch_order: "规划补番路线",
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
        <h3>幕后 · 它在做什么</h3>
        <div className="segmented">
          <button className={mode === "summary" ? "active" : ""} onClick={() => onModeChange("summary")}>简洁</button>
          <button className={mode === "dev" ? "active" : ""} onClick={() => onModeChange("dev")}>开发</button>
        </div>
      </div>
      {trace.length === 0 && !busy && (
        <div className="trace-empty">提问后，它检索和查证的每一步会实时出现在这里</div>
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
      {busy && <div className="trace-item processing">● 正在思考…（推荐会读取画像、筛候选并核对口碑，复杂条件可能需要几十秒）</div>}
    </div>
  );
}

export default function Home() {
  const experience = useExperience();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const runTraceRef = useRef<TraceItem[]>([]);
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const sourcesRef = useRef<Source[]>([]);
  const [evidence, setEvidence] = useState<EvidenceMap>({});
  const [spoiler, setSpoiler] = useState<SpoilerState | null>(null);
  const [memory, setMemory] = useState<MemoryState | null>(null);
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [followups, setFollowups] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [traceMode, setTraceMode] = useState<"summary" | "dev">("summary");
  const [evidenceMode, setEvidenceMode] = useState<"user" | "dev">("user");
  const [contextOpen, setContextOpen] = useState(false);
  const [authNotice, setAuthNotice] = useState<AuthNotice | null>(null);
  const [uploadNotice, setUploadNotice] = useState<UploadNotice | null>(null);
  const [shareNotice, setShareNotice] = useState<AuthNotice | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [resumeCandidate, setResumeCandidate] = useState<ChatSession | null>(null);
  const [busy, setBusy] = useState(false);
  const answerRef = useRef("");
  const evidenceRef = useRef<EvidenceMap>({});  // finally 定型消息时读（state 闭包会是旧值）
  // 等待体验：本轮 agent 步骤实时滚动（豆包/Gemini 式"看它思考"），完成后收敛进消息
  const [liveSteps, setLiveSteps] = useState<string[]>([]);
  const liveStepsRef = useRef<string[]>([]);
  const turnStartRef = useRef(0);
  const [, setClockTick] = useState(0);  // busy 时每秒 tick 一次驱动秒表重绘
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const csrfToken = useRef("");
  const sessionId = useRef("");  // 多轮会话 id（首次发送时生成；"新对话"会重置）
  const lastQ = useRef("");      // 最近一次用户问题（剧透 followup chips 重发用）
  const turnIdRef = useRef("");  // 本轮 turn_id（meta 事件下发，👍👎 反馈按它关联轨迹）
  const abortRef = useRef<AbortController | null>(null);
  const activeRunIdRef = useRef("");
  const streamingRunRef = useRef("");
  const receivedFinalRef = useRef(false);
  const busyRef = useRef(false);
  const deviceIdRef = useRef("");
  const realtimeRefreshRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const realtimeChangedSessionsRef = useRef<Set<string>>(new Set());

  // 新问题锚顶：发出消息后把该条用户消息滚到视口顶部，流式回答在其下方展开——
  // 否则长对话里视口停在旧位置，正在生成的内容整个在屏幕外（用户实测痛点）。
  useEffect(() => {
    const last = messages.length - 1;
    if (last >= 0 && messages[last]?.role === "user") {
      requestAnimationFrame(() => {
        document.getElementById(`msg-${last}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }, [messages.length]);

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => setClockTick((x) => x + 1), 1000);  // 驱动等待秒表
    return () => clearInterval(t);
  }, [busy]);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialQuestion = params.get("q");
    if (initialQuestion) setInput(initialQuestion);
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
    // Per-page writer id: another tab in the same browser is still a distinct
    // writer and must not silently submit into a running conversation.
    const storedDeviceId = window.sessionStorage.getItem("otomo.chatDeviceId") || crypto.randomUUID();
    deviceIdRef.current = storedDeviceId;
    window.sessionStorage.setItem("otomo.chatDeviceId", storedDeviceId);
    void (async () => {
      const identity = await refreshAuthSession();
      const rows = await loadSessions();
      const handoff = params.get("handoff") || window.sessionStorage.getItem("otomo.pendingHandoff") || "";
      if (handoff) {
        window.sessionStorage.setItem("otomo.pendingHandoff", handoff);
        const clean = new URLSearchParams(window.location.search);
        clean.delete("handoff");
        window.history.replaceState(null, "", `${window.location.pathname}${clean.size ? `?${clean}` : ""}`);
        if (identity?.authenticated) await consumeHandoff(handoff);
        else setAuthNotice({ tone: "warn", text: "请先绑定同一个 Bangumi 账号，再接续这段 Discord 对话。" });
        return;
      }
      await restoreLastSession(rows);
    })();
  }, []);

  useEffect(() => {
    if (auth === null || !deviceIdRef.current) return;
    const source = new EventSource(
      `${BACKEND}/sessions/events?device_id=${encodeURIComponent(deviceIdRef.current)}`,
      { withCredentials: true },
    );
    const refresh = (raw: Event) => {
      const message = raw as MessageEvent;
      let event: Record<string, any> = {};
      try { event = JSON.parse(String(message.data || "{}")); } catch { return; }
      if (event.type === "ping") return;
      if (event.type === "session_changed" && event.session_id) {
        realtimeChangedSessionsRef.current.add(String(event.session_id));
      }
      if (realtimeRefreshRef.current) clearTimeout(realtimeRefreshRef.current);
      realtimeRefreshRef.current = setTimeout(async () => {
        const changed = new Set(realtimeChangedSessionsRef.current);
        realtimeChangedSessionsRef.current.clear();
        await loadSessions();
        if (
          changed.has(sessionId.current) &&
          !busyRef.current
        ) await loadSession(sessionId.current, true);
      }, 90);
    };
    source.addEventListener("session", refresh);
    return () => {
      source.close();
      if (realtimeRefreshRef.current) clearTimeout(realtimeRefreshRef.current);
    };
  }, [auth?.authenticated, auth?.username]);

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
    if (detail) {
      const message = typeof detail === "string" ? detail : detail.message || JSON.stringify(detail);
      return `${message}${suffix}`;
    }
    const text = await res.text().catch(() => "");
    return `${res.status} ${res.statusText || "request failed"}${text ? `: ${text.slice(0, 160)}` : ""}${suffix}`;
  }

  async function refreshAuthSession(): Promise<AuthState | null> {
    try {
      const payload = await experience.refreshAuthSession();
      csrfToken.current = payload.csrf_token || "";
      setAuth(payload);
      return payload;
    } catch {
      setAuth({ authenticated: false });
    }
    return null;
  }

  async function loadSessions(): Promise<ChatSession[]> {
    try {
      const query = deviceIdRef.current
        ? `?device_id=${encodeURIComponent(deviceIdRef.current)}`
        : "";
      const res = await fetch(`${BACKEND}/sessions${query}`, { credentials: "include" });
      const payload = await res.json().catch(() => ({}));
      if (res.ok && payload.ok) {
        const rows = list(payload.sessions) as ChatSession[];
        setSessions(rows);
        return rows;
      }
    } catch {
      /* 历史会话不是主流程，失败静默降级 */
    }
    return [];
  }

  async function restoreLastSession(rows: ChatSession[] = []) {
    const saved = window.localStorage.getItem("otomo.activeSessionId") || "";
    if (saved) {
      if (await loadSession(saved)) return;
    }
    setResumeCandidate(rows.find((row) => Number(row.message_count || 0) > 0) || null);
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
      sources: list(row.sources) as Source[],
      trace: list(row.trace) as TraceItem[],
      steps: list(row.steps).map((step) => String(step)),
      turnId: String(row.turn_id || "") || undefined,
      elapsedMs: row.elapsed_ms == null ? undefined : Number(row.elapsed_ms),
    }));
  }

  async function loadSession(id: string, force = false): Promise<boolean> {
    if (!id || (busyRef.current && !force)) return false;
    try {
      const deviceQuery = deviceIdRef.current
        ? `?device_id=${encodeURIComponent(deviceIdRef.current)}`
        : "";
      const res = await fetch(`${BACKEND}/sessions/${encodeURIComponent(id)}/messages${deviceQuery}`, { credentials: "include" });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        if ([403, 404].includes(res.status) && window.localStorage.getItem("otomo.activeSessionId") === id) {
          sessionId.current = "";
          setActiveSessionId("");
          window.localStorage.removeItem("otomo.activeSessionId");
        }
        return false;
      }
      sessionId.current = id;
      setActiveSessionId(id);
      window.localStorage.setItem("otomo.activeSessionId", id);
      const restoredMessages = normalizeRestoredMessages(payload.messages);
      setMessages(restoredMessages);
      setEvidence(payload.evidence || {});
      sourcesRef.current = list(payload.sources) as Source[];
      setSources(sourcesRef.current);
      const shortTerm = payload.state?.short_term || {};
      setSpoiler(shortTerm.spoiler || null);
      setMemory(shortTerm.memory || null);
      const lastAssistant = [...restoredMessages].reverse().find((message) => message.role === "assistant");
      runTraceRef.current = lastAssistant?.trace || [];
      setTrace(runTraceRef.current);
      setFollowups([]);
      setAnswer("");
      answerRef.current = "";
      setResumeCandidate(null);
      const activeRunId = String(payload.session?.activity_run_id || "");
      if (
        payload.session?.running
        && activeRunId
        && streamingRunRef.current !== activeRunId
        && !busyRef.current
      ) {
        setTimeout(() => void resumeRun(activeRunId), 0);
      } else if (!payload.session?.running) {
        activeRunIdRef.current = "";
      }
      return true;
    } catch {
      /* ignore */
      return false;
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

  async function uploadPendingImages(signal?: AbortSignal): Promise<ImageAttachment[]> {
    if (!pendingImages.length) return [];
    const uploaded: ImageAttachment[] = [];
    for (const image of pendingImages) {
      const dataUrl = await readAsDataUrl(image.file);
      const res = await fetch(`${BACKEND}/uploads/image`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ data_url: dataUrl, filename: image.file.name }),
        signal,
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

  async function sendAnswerFeedback(idx: number, rating: "up" | "down") {
    const msg = messages[idx];
    if (!msg?.turnId) return;
    const next = msg.feedback === rating ? undefined : rating; // 再点一次取消
    setMessages((m) => m.map((x, i) => (i === idx ? { ...x, feedback: next } : x)));
    try {
      await fetch(`${BACKEND}/feedback/answer`, {
        method: "POST",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        credentials: "include",
        body: JSON.stringify({
          session_id: sessionId.current,
          turn_id: msg.turnId,
          rating: next ?? "clear",
        }),
      });
    } catch {
      /* 反馈失败静默：不打断阅读 */
    }
  }

  async function send(override?: string, spoilerMode?: string) {
    let q = (override ?? input).trim();
    const shouldUseImage = pendingImages.length > 0 && !override;
    if (!q && shouldUseImage) {
      q = pendingImages.length > 1 ? "请综合识别这些截图，并回锚 Bangumi 候选。" : "请识别这张截图，并回锚 Bangumi 候选。";
    }
    const active = sessions.find((row) => row.id === sessionId.current);
    if (!q || busy || active?.running) return;
    lastQ.current = q;
    setInput("");
    setTrace([]);
    runTraceRef.current = [];
    setSources([]);
    sourcesRef.current = [];
    setEvidence({});
    evidenceRef.current = {};
    setFollowups([]);
    setAnswer("");
    answerRef.current = "";
    receivedFinalRef.current = false;
    liveStepsRef.current = [];
    setLiveSteps([]);
    turnStartRef.current = Date.now();
    busyRef.current = true;
    setBusy(true);
    const controller = new AbortController();
    abortRef.current = controller;
    let userMessageAdded = false;
    if (!sessionId.current) {
      sessionId.current = crypto.randomUUID();  // 客户端 lazy 生成，避免 SSR mismatch
      setActiveSessionId(sessionId.current);
      window.localStorage.setItem("otomo.activeSessionId", sessionId.current);
    }
    const backgroundTaskId = crypto.randomUUID();
    let backgroundTaskError = "";
    window.dispatchEvent(new CustomEvent("otomo:task-start", {
      detail: {
        id: backgroundTaskId,
        path: "/chat",
        label: "Otomo 正在回答",
        href: "/chat",
      },
    }));

    try {
      const attachments = shouldUseImage ? await uploadPendingImages(controller.signal) : [];
      if (shouldUseImage) clearPendingImages();
      setMessages((m) => [...m, { role: "user", content: q, attachments }]);
      userMessageAdded = true;
      const res = await fetch(`${BACKEND}/chat`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          message: q,
          session_id: sessionId.current,
          device_id: deviceIdRef.current,
          attachments,
          ...(spoilerMode ? { spoiler_mode: spoilerMode } : {}),
        }),
        signal: controller.signal,
      });
      if (!res.ok) {
        if (res.status === 409 && userMessageAdded) {
          setMessages((rows) => rows.slice(0, -1));
          userMessageAdded = false;
          await loadSessions();
          await loadSession(sessionId.current, true);
        }
        throw new Error(await httpErrorMessage(res));
      }
      if (!res.body) throw new Error("no response body");
      const responseRunId = res.headers.get("x-otomo-run-id") || "";
      if (responseRunId) {
        activeRunIdRef.current = responseRunId;
        streamingRunRef.current = responseRunId;
      }
      await consumeEventStream(res);
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === "AbortError";
      if (aborted && !receivedFinalRef.current) {
        if (!userMessageAdded) {
          setMessages((m) => [...m, { role: "user", content: q }]);
        }
        answerRef.current = "本轮生成已由用户停止。";
        setAnswer(answerRef.current);
        evidenceRef.current = {};
        setEvidence({});
        pushRunTrace({ kind: "note", text: "已停止本轮生成" });
        backgroundTaskError = "本轮生成已停止";
      } else if (!aborted) {
        const message = e instanceof Error ? e.message : String(e);
        if (activeRunIdRef.current && !receivedFinalRef.current) {
          pushRunTrace({ kind: "note", text: "与实时进度的连接已断开，任务仍在服务器后台继续" });
          setUploadNotice({ tone: "warn", text: "实时连接已断开，Otomo 仍在后台回答；稍后回到这段对话即可恢复。" });
        } else {
          pushRunTrace({ kind: "obs", name: "error", ok: false, summary: message });
          setUploadNotice({ tone: "bad", text: message });
          backgroundTaskError = message;
        }
      }
    } finally {
      const final = answerRef.current;
      if (final && receivedFinalRef.current) {
        const turnEvidence = evidenceRef.current;
        setMessages((m) => [...m, {
          role: "assistant", content: final, evidence: turnEvidence, turnId: turnIdRef.current || undefined,
          sources: sourcesRef.current.length ? sourcesRef.current : undefined,
          steps: liveStepsRef.current.length ? liveStepsRef.current : undefined,
          trace: runTraceRef.current.length ? runTraceRef.current : undefined,
          elapsedMs: turnStartRef.current ? Date.now() - turnStartRef.current : undefined,
        }]);
      }
      liveStepsRef.current = [];
      setLiveSteps([]);
      setAnswer("");
      busyRef.current = false;
      setBusy(false);
      if (abortRef.current === controller) abortRef.current = null;
      streamingRunRef.current = "";
      if (receivedFinalRef.current) activeRunIdRef.current = "";
      window.dispatchEvent(new CustomEvent("otomo:task-finish", {
        detail: { id: backgroundTaskId, ...(backgroundTaskError ? { error: backgroundTaskError } : {}) },
      }));
      void loadSessions();
    }
  }

  async function consumeEventStream(res: Response) {
    if (!res.body) throw new Error("no response body");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split(/\r?\n\r?\n/);
      buf = blocks.pop() ?? "";
      for (const block of blocks) {
        const dataLine = block.split(/\r?\n/).find((line) => line.startsWith("data:"));
        if (!dataLine) continue;
        try {
          handleEvent(JSON.parse(dataLine.slice(5).trim()));
        } catch {
          /* 忽略半包/ping */
        }
      }
    }
  }

  async function resumeRun(runId: string) {
    if (!runId || streamingRunRef.current === runId || busyRef.current) return;
    streamingRunRef.current = runId;
    activeRunIdRef.current = runId;
    receivedFinalRef.current = false;
    answerRef.current = "";
    setAnswer("");
    evidenceRef.current = {};
    setEvidence({});
    runTraceRef.current = [];
    setTrace([]);
    liveStepsRef.current = [];
    setLiveSteps([]);
    turnStartRef.current = Date.now();
    busyRef.current = true;
    setBusy(true);
    const taskId = `chat-run:${runId}`;
    window.dispatchEvent(new CustomEvent("otomo:task-start", {
      detail: { id: taskId, path: "/chat", label: "重新连接 Otomo 的回答", href: "/chat" },
    }));
    let taskError = "";
    try {
      const res = await fetch(`${BACKEND}/chat/runs/${encodeURIComponent(runId)}/events`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await httpErrorMessage(res));
      await consumeEventStream(res);
    } catch (cause) {
      taskError = cause instanceof Error ? cause.message : String(cause);
      setUploadNotice({ tone: "warn", text: `暂时无法恢复实时过程：${taskError}` });
    } finally {
      busyRef.current = false;
      setBusy(false);
      setAnswer("");
      answerRef.current = "";
      liveStepsRef.current = [];
      setLiveSteps([]);
      await loadSession(sessionId.current, true);
      streamingRunRef.current = "";
      void loadSessions();
      window.dispatchEvent(new CustomEvent("otomo:task-finish", {
        detail: { id: taskId, ...(taskError ? { error: taskError } : {}) },
      }));
    }
  }

  async function consumeHandoff(code: string) {
    try {
      const res = await fetch(`${BACKEND}/sessions/handoff/consume`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ code }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        const detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
        if (res.status === 404) window.sessionStorage.removeItem("otomo.pendingHandoff");
        setAuthNotice({ tone: "bad", text: detail || "Discord 续聊链接无效或已过期。" });
        return;
      }
      const id = String(payload.session?.id || "");
      window.sessionStorage.removeItem("otomo.pendingHandoff");
      await loadSessions();
      await loadSession(id);
      setAuthNotice({ tone: "good", text: "Discord 对话已安全复制到网页，可以从这里继续。" });
    } catch (e) {
      setAuthNotice({ tone: "bad", text: `接续 Discord 对话失败：${String(e)}` });
    }
  }

  async function stopGeneration() {
    const runId = activeRunIdRef.current
      || sessions.find((row) => row.id === sessionId.current)?.activity_run_id
      || "";
    if (!runId) {
      abortRef.current?.abort();
      return;
    }
    try {
      const res = await fetch(`${BACKEND}/chat/runs/${encodeURIComponent(runId)}/cancel`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: "{}",
      });
      if (!res.ok) throw new Error(await httpErrorMessage(res));
      pushStep("正在停止本轮生成…");
    } catch (cause) {
      setUploadNotice({ tone: "bad", text: cause instanceof Error ? cause.message : String(cause) });
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

  async function postPrepareWrite(
    subjectId: number,
    subjectName: string,
    collectionType = 1,
    options?: {
      operation?: "set_collection" | "mark_episodes_watched";
      upToEpisode?: number;
      recommendationSetId?: string;
    },
  ) {
    try {
      const res = await fetch(`${BACKEND}/actions/prepare-write`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          subject_id: subjectId,
          subject_name: subjectName,
          collection_type: collectionType,
          operation: options?.operation || "set_collection",
          up_to_episode: options?.upToEpisode,
          recommendation_set_id: options?.recommendationSetId,
          reason: options?.operation === "mark_episodes_watched"
            ? `从今日追番卡片标记看到第 ${options.upToEpisode} 集`
            : "从前端推荐/日历卡片一键加入想看",
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

  async function postRecommendationFeedback(payloadIn: Record<string, any>) {
    try {
      const res = await fetch(`${BACKEND}/feedback/recommendation`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payloadIn),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setTrace((rows) => [...rows, { kind: "obs", name: "recommendation_feedback", ok: false, summary: payload.detail || `HTTP ${res.status}` }]);
        return false;
      }
      return true;
    } catch (error) {
      setTrace((rows) => [...rows, { kind: "obs", name: "recommendation_feedback", ok: false, summary: String(error) }]);
      return false;
    }
  }

  async function nextRecommendationBatch(setId: string) {
    try {
      const res = await fetch(`${BACKEND}/recommendations/next`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ recommendation_set_id: setId }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok || !payload.data) {
        setTrace((rows) => [...rows, { kind: "obs", name: "recommendations_next", ok: false, summary: payload.detail || payload.error || `HTTP ${res.status}` }]);
        return null;
      }
      setTrace((rows) => [...rows, { kind: "obs", name: "recommendations_next", ok: true, summary: `已换一批：${payload.data.items?.length ?? 0} 个候选` }]);
      return payload.data;
    } catch (error) {
      setTrace((rows) => [...rows, { kind: "obs", name: "recommendations_next", ok: false, summary: String(error) }]);
      return null;
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

  function pushStep(text: string) {
    const clean = String(text || "").trim().slice(0, 80);
    if (!clean) return;
    const steps = liveStepsRef.current;
    if (steps[steps.length - 1] === clean) return;  // 连续重复去重
    liveStepsRef.current = [...steps.slice(-119), clean];
    setLiveSteps(liveStepsRef.current);
  }

  function pushRunTrace(item: TraceItem) {
    runTraceRef.current = [...runTraceRef.current.slice(-199), item];
    setTrace(runTraceRef.current);
  }

  function handleEvent(ev: any) {
    switch (ev.type) {
      case "meta":
        turnIdRef.current = ev.turn_id || "";
        activeRunIdRef.current = ev.run_id || activeRunIdRef.current;
        break;
      case "plan":
        pushRunTrace({ kind: "note", text: `📋 ${ev.summary}` });
        pushStep(`规划：${ev.summary}`);
        break;
      case "reflect":
        pushRunTrace({ kind: "note", text: ev.complete ? "↺ 反思：完整" : `↺ 反思：${ev.note}` });
        break;
      case "tool_call":
        pushRunTrace({ kind: "call", name: ev.name, args: ev.args });
        break;
      case "progress":
        pushRunTrace({
          kind: "progress",
          tool: ev.tool || "",
          summary: ev.summary,
          current: ev.current ?? undefined,
          total: ev.total ?? undefined,
          note: ev.note || "",
        });
        pushStep(ev.summary);
        break;
      case "observation":
        pushRunTrace({ kind: "obs", name: ev.name, ok: ev.ok, summary: ev.summary });
        pushStep(`${ev.ok ? "✓" : "✗"} ${ev.summary}`);
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
        pushRunTrace({
          kind: "note",
          text: verifiableClaims
            ? `证据校验：support ${(Number(ev.support_rate || 0) * 100).toFixed(0)}% · unsupported ${ev.unsupported_count ?? 0}`
            : "证据校验：无强 canonical 硬事实需要自动回退",
        });
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
        sourcesRef.current = list(ev.sources) as Source[];
        setSources(sourcesRef.current);
        if (ev.answer) {
          receivedFinalRef.current = true;
          answerRef.current = ev.answer; // 以最终完整答案为准，覆盖流式残留（如泄漏被截断的片段）
          setAnswer(ev.answer);
        }
        break;
      case "followup":
        setFollowups(ev.questions ?? []);
        break;
      case "error":
        pushRunTrace({ kind: "obs", name: "error", ok: false, summary: ev.message });
        break;
    }
  }

  function newChat() {
    sessionId.current = "";  // 重置 → 下次发送会生成新会话 id（清空多轮上下文）
    setActiveSessionId("");
    window.localStorage.removeItem("otomo.activeSessionId");
    activeRunIdRef.current = "";
    setMessages([]);
    setTrace([]);
    runTraceRef.current = [];
    setSources([]);
    sourcesRef.current = [];
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
    if (!csrfToken.current) await refreshAuthSession();
    if (auth && !auth.oauth_configured) {
      setAuthNotice({
        tone: "warn",
        text: auth.dev_token_available
          ? "当前未配置 Bangumi OAuth 应用；本地开发可先使用 BANGUMI_TOKEN 绑定。"
          : "当前未配置 Bangumi OAuth 应用，也未检测到 BANGUMI_TOKEN。",
      });
      return;
    }
    const returnTo = "/chat";
    const res = await fetch(
      `${BACKEND}/auth/bangumi/login?return_to=${encodeURIComponent(returnTo)}`,
      { credentials: "include" },
    );
    const payload = await res.json().catch(() => ({}));
    if (payload.authorization_url) window.location.href = payload.authorization_url;
    else {
      const msg = payload.detail || "OAuth 未配置";
      setAuthNotice({ tone: "bad", text: `无法发起 Bangumi 登录：${msg}` });
      setTrace((t) => [...t, { kind: "obs", name: "bangumi_login", ok: false, summary: msg }]);
    }
  }

  async function loginWithLocalToken() {
    if (!csrfToken.current) await refreshAuthSession();
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
      csrfToken.current = payload.identity?.csrf_token || csrfToken.current;
      await experience.refreshAuthSession();
      setAuthNotice({ tone: "good", text: `已使用本地 BANGUMI_TOKEN 绑定：@${payload.identity?.username || "unknown"}` });
      const rows = await loadSessions();
      await restoreLastSession(rows);
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
    csrfToken.current = "";
    setAuth({ authenticated: false });
    await experience.refreshAuthSession();
    setAuthNotice({ tone: "warn", text: "已退出当前浏览器会话的 Bangumi 绑定" });
    setMemory(null);
    newChat();
    setSessions([]);
    setResumeCandidate(null);
  }

  async function createShareSnapshot(req: Record<string, any>) {
    setShareNotice(null);
    try {
      if (!csrfToken.current) await refreshAuthSession();
      const res = await fetch(`${BACKEND}/share/snapshots`, {
        method: "POST",
        credentials: "include",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          type: req.type,
          title: req.title,
          summary: req.summary || req.title || "",
          payload: req.payload || {},
          sources,
          spoiler_level: req.spoiler_level || "none",
          personalization_mode: req.personalization_mode || "public_generic",
          include_personalized_reason: req.personalization_mode === "public_personalized",
        }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok || !payload.ok) {
        setShareNotice({ tone: "bad", text: payload.detail || payload.error || `生成分享页失败：HTTP ${res.status}` });
        return;
      }
      const url = payload.url || payload.snapshot?.url;
      if (url) {
        await navigator.clipboard?.writeText(url).catch(() => undefined);
        setShareNotice({ tone: "good", text: `分享页已生成，链接已复制：${url}` });
        window.open(url, "_blank", "noopener,noreferrer");
      } else {
        setShareNotice({ tone: "good", text: "分享页已生成。" });
      }
    } catch (e) {
      setShareNotice({ tone: "bad", text: `生成分享页失败：${String(e)}` });
    }
  }

  const hasEvidence = Object.values(evidence).some((rows) => list(rows).length > 0);

  const panelHandlerProps = {
    onShareSnapshot: createShareSnapshot,
    onCritique: (q: string) => send(q),
    onConfirmAction: (id: string) => postAction("confirm", id),
    onCancelAction: (id: string) => postAction("cancel", id),
    onUndoAction: (id: string) => postAction("undo", id),
    onPrepareWrite: postPrepareWrite,
    onRecommendationFeedback: postRecommendationFeedback,
    onNextRecommendationBatch: nextRecommendationBatch,
    onPrepareDownloaderPush: postPrepareDownloaderPush,
    onVisualFeedback: postVisualFeedback,
    onVisualCorrectionSearch: searchVisualCorrection,
  };
  const panelHandlers: PanelHandlers = { ...panelHandlerProps, devMode: evidenceMode === "dev" };
  const activeSession = sessions.find((row) => row.id === activeSessionId);
  const backgroundBusy = Boolean(activeSession?.running && !busy);
  const surfaceName = activeSession?.activity_surface === "discord"
    ? "Discord"
    : activeSession?.activity_is_current_device ? "这个标签页的后台任务" : "另一个页面或设备";

  return (
    <main className={`page-frame chat-page mode-${evidenceMode}`}>
      <PageHeader
        eyebrow="智能助手"
        title="与 Otomo 对话"
        description="可以直接问作品、推荐、考据或发截图；执行过程和可核对来源会跟在回答旁边。"
        actions={(
          <>
            <button className="button-secondary icon-label" onClick={() => setContextOpen((x) => !x)} title="执行与证据上下文"><PanelRightOpen size={17} />上下文</button>
            <button className="button-primary icon-label" onClick={newChat} disabled={busy}><Plus size={17} />新对话</button>
          </>
        )}
      />
      <div className="chat-status-row">
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
          {shareNotice && <div className={`auth-notice ${shareNotice.tone}`}>{shareNotice.text}</div>}
      </div>
      <div className="session-strip" aria-label="最近会话">
            <button className="icon-plain" title="新建会话" onClick={newChat} disabled={busy}><Plus size={16} /></button>
            {sessions.slice(0, 6).map((s) => (
              <button
                key={s.id}
                className={`session-chip ${activeSessionId === s.id ? "active" : ""}`}
                onClick={() => loadSession(s.id)}
                disabled={busy}
                title={`${sessionActivityLabel(s)}${s.running ? " · 正在生成" : ""}`}
              >
                <i className={`session-source source-${s.source || "web"}`} aria-hidden="true">
                  {s.source === "discord_import" ? "D" : "W"}
                </i>
                <span>{s.title || "新对话"}</span>
                <small>{s.message_count ?? 0}</small>
                {s.running && <em className="session-running" title="正在生成" />}
                <b
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (s.running) return;
                    void deleteSession(s.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !s.running) {
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

      {resumeCandidate && !activeSessionId && (
        <div className="resume-session-banner">
          <div>
            <strong>继续上次对话</strong>
            <span>{resumeCandidate.title || "新对话"} · {sessionActivityLabel(resumeCandidate)} · {resumeCandidate.message_count || 0} 条消息</span>
          </div>
          <div>
            <button className="button-primary" onClick={() => loadSession(resumeCandidate.id)}>继续</button>
            <button className="button-secondary" onClick={() => setResumeCandidate(null)}>暂不</button>
          </div>
        </div>
      )}

      <div className={`chat-layout ${contextOpen ? "with-context" : ""}`}>
        <section className="chat-surface">
          {backgroundBusy && (
            <div className="session-activity-banner" role="status">
              <span className="activity-pulse" />
              <div><strong>{surfaceName}正在生成这段对话</strong><span>可以去浏览其他页面；返回后过程与回答会自动恢复。</span></div>
            </div>
          )}
          {messages.length === 0 && !answer && (
            <div className="welcome">
              <div className="welcome-title">你的 ACGN 生活助手</div>
              <div className="welcome-sub">推荐 · 评价 · 追番 · 资源 · 识图，都可以直接问。试试：</div>
              {!auth?.authenticated && <TasteQuiz onDone={(q) => send(q)} disabled={busy} />}
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
            <div key={i} id={`msg-${i}`} className={`msg ${m.role}`}>
              <MessageAvatar role={m.role} auth={auth} />
              <div className="msg-main">
                <div className="role">{m.role === "user" ? (auth?.username ? `@${auth.username}` : "你") : "Otomo"}</div>
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
                    <AgentStepsFold steps={m.steps} trace={m.trace} elapsedMs={m.elapsedMs} />
                    <AssistantContent content={m.content} evidence={m.evidence} handlers={panelHandlers} />
                    <AnswerSupport sources={m.sources || []} evidence={m.evidence || {}} />
                    {m.turnId && (
                      <div className="answer-feedback">
                        <button className={`fb-btn ${m.feedback === "up" ? "on" : ""}`} title="这条回答不错"
                          onClick={() => sendAnswerFeedback(i, "up")}>👍</button>
                        <button className={`fb-btn ${m.feedback === "down" ? "on" : ""}`} title="这条回答不行"
                          onClick={() => sendAnswerFeedback(i, "down")}>👎</button>
                      </div>
                    )}
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
            </div>
          ))}
          {(busy || answer) && (
            <div className="msg assistant">
              <MessageAvatar role="assistant" />
              <div className="msg-main">
                <div className="role">Otomo</div>
                <div className="bubble">
                  {busy && <AgentLiveStatus steps={liveSteps} trace={trace} startedAt={turnStartRef.current} />}
                  {/* 流式中面板标记逐字到达后即时嵌入（inline 锚定对打字中的回答同样生效） */}
                  {answer ? <AssistantContent content={answer + "▍"} evidence={evidence} handlers={panelHandlers} /> : null}
                  {answer ? <AnswerSupport sources={sources} evidence={evidence} /> : null}
                </div>
              </div>
            </div>
          )}
          {backgroundBusy && !answer && (
            <div className="msg assistant background-generation">
              <MessageAvatar role="assistant" />
              <div className="msg-main">
                <div className="role">Otomo · 后台生成中</div>
                <div className="bubble">
                  <AgentLiveStatus
                    steps={["正在后台继续处理，完成后自动同步到这里"]}
                    startedAt={Number(activeSession?.activity_started_at || 0) * 1000}
                  />
                </div>
              </div>
            </div>
          )}
          {hasEvidence && evidenceMode === "dev" && (
            <div className="evidence-toolbar">
              <div>
                <div className="evidence-toolbar-title">资料卡片</div>
                <div className="evidence-toolbar-sub">开发者模式：本轮全部原始证据都平铺在底部</div>
              </div>
            </div>
          )}
          {/* user 模式：底部保留未被正文锚定的面板；dev 模式：本轮全家桶便于调试 */}
          <EvidencePanels
            evidence={evidence}
            mode={evidenceMode}
            collapsible={evidenceMode === "user"}
            excludeNames={evidenceMode === "user" ? inlinePanelNames(answer, evidence) : []}
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
          <div className="composer">
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
              className="composer-icon"
              title={`上传截图（最多 ${MAX_IMAGES} 张）`}
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || backgroundBusy || pendingImages.length >= MAX_IMAGES}
            >
              <ImagePlus size={19} />
            </button>
            <input
              type="text"
              value={input}
              placeholder="例：白色相簿2 里 冬马和纱 的声优还配过哪些番？"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              disabled={busy || backgroundBusy}
            />
            {busy ? (
              <button className="composer-send stop-button" onClick={stopGeneration} title="停止本轮生成"><Square size={17} /></button>
            ) : (
              <button className="composer-send" onClick={() => send()} title="发送" disabled={backgroundBusy}><Send size={18} /></button>
            )}
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
        </section>

        {contextOpen ? (
          <aside className="context-rail">
            <div className="context-heading">
              <div><strong>本轮上下文</strong><span>普通模式只显示运行摘要</span></div>
              <div className="segmented" aria-label="上下文模式">
                <button className={evidenceMode === "user" ? "active" : ""} onClick={() => { setEvidenceMode("user"); setTraceMode("summary"); }}>简洁</button>
                <button className={evidenceMode === "dev" ? "active" : ""} onClick={() => { setEvidenceMode("dev"); setTraceMode("dev"); }}>开发</button>
              </div>
            </div>
            <TracePanel trace={trace} busy={busy} mode={traceMode} onModeChange={setTraceMode} />
          </aside>
        ) : null}
      </div>
    </main>
  );
}
