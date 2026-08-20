"use client";

import {
  Activity, BarChart3, Check, Database, Eye, EyeOff, Flag, Gauge,
  HardDrive, LoaderCircle, RefreshCw, RotateCcw, ShieldCheck, Trash2,
  ChevronRight, X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import QRCode from "qrcode";

import { PageHeader } from "../../components/page-header";
import { productFetch } from "../../lib/api";
import { useExperience } from "../../lib/experience";

type AnyRow = Record<string, any>;

function pct(value: number) { return `${Math.round((value || 0) * 100)}%`; }
function duration(ms: number) { return ms ? `${(ms / 1000).toFixed(1)} 秒` : "暂无"; }
function bytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / (1024 ** index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function BadgeLike({ good, label }: { good: boolean; label: string }) {
  return <span className={`admin-integration-badge ${good ? "good" : "dim"}`}>{label}</span>;
}

const scoreLabels: Record<string, string> = {
  affinity: "口味贴合", graph: "主创关联", cf: "相似用户", external: "外部证据",
  explicit_request: "本轮要求", scenario: "场景", feedback: "近期反馈",
  memory_penalty: "长期避雷", temporary_penalty: "本轮避雷",
  feedback_penalty: "近期负反馈", profile_penalty: "画像雷区",
  aspect_profile: "好球区", media_subtype: "媒介分型", semantic: "语义相似",
  quality: "社区口碑", evidence_aspect: "评价好球区", evidence_quality: "多源评价",
};

const EMPTY_SERIES_RULE = JSON.stringify({
  id: "series-id",
  title: "系列名称",
  mainline: [
    { subject_id: 1, name: "第一部", necessity: "required", note: "" },
    { subject_id: 2, name: "总集篇", necessity: "skip", note: "不阻塞主线" },
    { subject_id: 3, name: "第二部", necessity: "required", note: "" },
  ],
  optional: [],
  alternates: [],
  notes: ["管理员核对后的观看顺序。"],
}, null, 2);

export default function AdminPage() {
  const exp = useExperience();
  const [data, setData] = useState<AnyRow | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [days, setDays] = useState(30);
  const [batchDetail, setBatchDetail] = useState<AnyRow | null>(null);
  const [batchBusy, setBatchBusy] = useState("");
  const [biliCookies, setBiliCookies] = useState("");
  const [biliBusy, setBiliBusy] = useState(false);
  const [biliQr, setBiliQr] = useState<AnyRow | null>(null);
  const [biliQrImage, setBiliQrImage] = useState("");
  const [adminAction, setAdminAction] = useState("");
  const [seriesDraft, setSeriesDraft] = useState(EMPTY_SERIES_RULE);

  async function load(nextDays = days) {
    setBusy(true); setError("");
    try {
      const [payload, batchPayload] = await Promise.all([
        productFetch(`/admin/overview?days=${nextDays}`),
        productFetch("/admin/recommendations/batches?limit=30"),
      ]);
      setData({
        ...payload,
        recommendations: { ...payload.recommendations, batches: batchPayload.batches || [] },
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  }

  useEffect(() => { void exp.refreshAuthSession().then(() => load()); }, []);
  useEffect(() => {
    if (!batchDetail) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setBatchDetail(null); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [batchDetail]);
  useEffect(() => {
    if (!biliQr?.login_id || !["waiting", "scanned"].includes(biliQr.status)) return;
    let stopped = false;
    const timer = window.setTimeout(async () => {
      try {
        const result = await productFetch("/admin/integrations/bilibili/qr/poll", {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
          body: JSON.stringify({ login_id: biliQr.login_id }),
        });
        if (stopped) return;
        setBiliQr((current) => current ? ({ ...current, ...result.login }) : result.login);
        if (result.integration) {
          setData((current) => current ? ({ ...current, integrations: { ...current.integrations, bilibili: result.integration } }) : current);
          setBiliQrImage("");
        }
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : String(cause));
      }
    }, 1800);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [biliQr?.login_id, biliQr?.status, exp.csrf]);

  async function openBatch(setId: string) {
    setBatchBusy(setId);
    try {
      const payload = await productFetch(`/admin/recommendations/batches/${encodeURIComponent(setId)}`);
      setBatchDetail(payload.batch);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBatchBusy(""); }
  }

  async function moderate(commentId: string, action: "hide" | "restore" | "delete") {
    const note = action === "delete" ? "管理员删除" : window.prompt("可选：填写治理备注", "") ?? "";
    if (action === "delete" && !window.confirm("永久删除这条留言及其举报记录？")) return;
    await productFetch(`/admin/comments/${encodeURIComponent(commentId)}/moderate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
      body: JSON.stringify({ action, note }),
    });
    await load();
  }

  async function resolve(reportId: string, status: "resolved" | "dismissed") {
    const note = window.prompt(status === "resolved" ? "处理说明（可选）" : "忽略原因（可选）", "") ?? "";
    await productFetch(`/admin/reports/${encodeURIComponent(reportId)}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
      body: JSON.stringify({ status, note }),
    });
    await load();
  }

  async function connectBilibili() {
    if (!biliCookies.trim()) return;
    setBiliBusy(true); setError("");
    try {
      const result = await productFetch("/admin/integrations/bilibili", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
        body: JSON.stringify({ cookies_text: biliCookies }),
      });
      setBiliCookies("");
      setData((current) => current ? ({ ...current, integrations: { ...current.integrations, bilibili: result.integration } }) : current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBiliBusy(false); }
  }

  async function startBilibiliQr() {
    setBiliBusy(true); setError("");
    try {
      const result = await productFetch("/admin/integrations/bilibili/qr/start", {
        method: "POST",
        headers: { "x-otomo-csrf": exp.csrf },
      });
      setBiliQrImage(await QRCode.toDataURL(result.login.qr_url, { width: 240, margin: 1 }));
      setBiliQr(result.login);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBiliBusy(false); }
  }

  async function disconnectBilibili() {
    if (!window.confirm("清除服务器保存的 B站登录态？公开视频搜索仍然可用。")) return;
    setBiliBusy(true); setError("");
    try {
      const result = await productFetch("/admin/integrations/bilibili", {
        method: "DELETE",
        headers: { "x-otomo-csrf": exp.csrf },
      });
      setData((current) => current ? ({ ...current, integrations: { ...current.integrations, bilibili: result.integration } }) : current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBiliBusy(false); }
  }

  async function testQbittorrent() {
    setAdminAction("qbittorrent"); setError("");
    try {
      const result = await productFetch("/admin/integrations/qbittorrent/test", {
        method: "POST",
        headers: { "x-otomo-csrf": exp.csrf },
      });
      setData((current) => current ? ({ ...current, integrations: { ...current.integrations, qbittorrent: result.integration } }) : current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setAdminAction(""); }
  }

  async function retrySubscription(ruleId: string) {
    setAdminAction(`retry-${ruleId}`); setError("");
    try {
      await productFetch(`/admin/subscriptions/${encodeURIComponent(ruleId)}/retry`, {
        method: "POST",
        headers: { "x-otomo-csrf": exp.csrf },
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setAdminAction(""); }
  }

  function editSeriesRule(rule: AnyRow) {
    setSeriesDraft(JSON.stringify(rule, null, 2));
    window.setTimeout(() => document.getElementById("series-rule-editor")?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }

  async function saveSeriesRule() {
    setAdminAction("series-save"); setError("");
    try {
      const parsed = JSON.parse(seriesDraft);
      if (!parsed?.id) throw new Error("规则必须填写 id。");
      await productFetch(`/admin/series-overrides/${encodeURIComponent(parsed.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": exp.csrf },
        body: JSON.stringify(parsed),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setAdminAction(""); }
  }

  async function deleteSeriesRule(ruleId: string) {
    if (!window.confirm(`删除人工系列规则 ${ruleId}？删除后会恢复 Bangumi 关系图。`)) return;
    setAdminAction(`series-delete-${ruleId}`); setError("");
    try {
      await productFetch(`/admin/series-overrides/${encodeURIComponent(ruleId)}`, {
        method: "DELETE",
        headers: { "x-otomo-csrf": exp.csrf },
      });
      setSeriesDraft(EMPTY_SERIES_RULE);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setAdminAction(""); }
  }

  const evaluation = data?.recommendations?.evaluation?.current || {};
  const hubMetrics = data?.anime_hub?.metrics || {};
  const moderation = data?.community?.moderation || {};
  const allTasks = useMemo(() => [
    ...(data?.tasks?.chat || []).map((row: AnyRow) => ({ ...row, kind: "对话" })),
    ...(data?.tasks?.recommendation || []).map((row: AnyRow) => ({ ...row, kind: "推荐" })),
  ].sort((a, b) => Number(b.started_at || 0) - Number(a.started_at || 0)), [data]);
  const diagnosticTrace = batchDetail?.request?._diagnostics;
  const hasDiagnosticTrace = Boolean(
    diagnosticTrace
    && (diagnosticTrace.candidate_count !== undefined || diagnosticTrace.finalist_pool?.length),
  );

  if (busy && !data) return <main className="page-frame"><div className="surface-loading"><LoaderCircle className="spin" size={17} /> 正在汇总运行状态…</div></main>;
  if (error && !data) return <main className="page-frame admin-page"><PageHeader eyebrow="管理后台" title="无法进入管理后台" description={error} /><div className="surface-error"><span>请确认当前 Bangumi 用户位于 COMMUNITY_ADMIN_USERNAMES 配置中。</span><button className="button-secondary" onClick={() => void load()}>重试</button></div></main>;
  if (!data) return null;

  return (
    <main className="page-frame admin-page">
      <PageHeader eyebrow="Operations" title="Otomo 管理后台" description={`当前管理员 @${data.admin} · 把推荐质量、社区治理、后台任务和存储状态放在同一处。`} actions={<div className="admin-period"><select value={days} onChange={(event) => { const value = Number(event.target.value); setDays(value); void load(value); }}><option value={7}>近 7 天</option><option value={30}>近 30 天</option><option value={90}>近 90 天</option></select><button className="button-secondary" disabled={busy} onClick={() => void load()}><RefreshCw className={busy ? "spin" : ""} size={15} />刷新</button></div>} />
      {error ? <div className="surface-error">{error}</div> : null}
      <section className="admin-kpis">
        <article><Activity size={18} /><span>今日访客</span><strong>{data.community.stats.visitors_today}</strong><small>页面浏览 {data.community.stats.views_today}</small></article>
        <article className={moderation.counts?.pending_reports ? "warn" : ""}><Flag size={18} /><span>待处理举报</span><strong>{moderation.counts?.pending_reports || 0}</strong><small>隐藏留言 {moderation.counts?.hidden || 0}</small></article>
        <article><Gauge size={18} /><span>Acceptance@3</span><strong>{pct(evaluation.acceptance_at_k?.["3"])}</strong><small>MRR {Number(evaluation.mrr || 0).toFixed(2)} · NDCG {Number(evaluation.ndcg || 0).toFixed(2)}</small></article>
        <article><BarChart3 size={18} /><span>推荐 P95</span><strong>{duration(evaluation.performance?.p95_ms)}</strong><small>证据缓存命中 {pct(evaluation.performance?.cache_hit_rate)}</small></article>
      </section>

      <section className="admin-section admin-integration">
        <header><div><span className="section-kicker">外部账号</span><h2>Bilibili 登录态</h2></div><span>{data.integrations?.bilibili?.authenticated ? `已连接 @${data.integrations.bilibili.username}` : data.integrations?.bilibili?.configured ? "Cookie 已导入但登录态失效" : "当前使用公开模式"}</span></header>
        <div className="integration-status-row">
          <BadgeLike good={Boolean(data.integrations?.bilibili?.authenticated)} label={data.integrations?.bilibili?.authenticated ? "登录态有效" : "未连接"} />
          <p>用于 B站搜索、视频详情、字幕读取和 ASR 音频下载。Cookie 只保存在服务器，不会进入聊天、模型上下文或普通用户 API。</p>
        </div>
        <div className="panel-actions"><button className="button-primary" disabled={biliBusy} onClick={() => void startBilibiliQr()}>{biliBusy ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />}使用B站App扫码连接</button>{data.integrations?.bilibili?.configured ? <button className="button-secondary" disabled={biliBusy} onClick={() => void disconnectBilibili()}><Trash2 size={15} />清除登录态</button> : null}</div>
        {biliQrImage ? <div className="admin-bili-qr"><img src={biliQrImage} alt="B站登录二维码" /><div><strong>{biliQr?.message || "等待扫码"}</strong><span>二维码仅在本机管理页显示，通常 3 分钟后过期。</span>{biliQr?.status === "expired" ? <button className="button-secondary" onClick={() => void startBilibiliQr()}>重新生成</button> : null}</div></div> : null}
        <details className="admin-cookie-fallback"><summary>扫码不可用？改用 cookies.txt</summary><label className="admin-cookie-import"><span>粘贴浏览器插件导出的 Netscape cookies.txt</span><textarea value={biliCookies} onChange={(event) => setBiliCookies(event.target.value)} placeholder="# Netscape HTTP Cookie File…" rows={5} spellCheck={false} /><small>建议使用专门账号；Cookie 只保存在服务器，不会发送给模型。</small></label><button className="button-secondary" disabled={biliBusy || !biliCookies.trim()} onClick={() => void connectBilibili()}>导入并验证</button></details>
      </section>

      <section className="admin-section">
        <header><div><span className="section-kicker">运行依赖</span><h2>ASR 与下载器诊断</h2></div><span>只做健康检查，不会创建下载任务。</span></header>
        <div className="admin-integration-grid">
          <article>
            <div><strong>视频语音核验</strong><BadgeLike good={Boolean(data.integrations?.asr?.healthy)} label={data.integrations?.asr?.healthy ? "可用" : data.integrations?.asr?.configured ? "异常" : "未配置"} /></div>
            <p>当前提供方：{data.integrations?.asr?.provider || "off"} · 最长核验 {data.integrations?.asr?.max_video_seconds || 0} 秒</p>
            {data.integrations?.asr?.error ? <small>{data.integrations.asr.error}</small> : <small>字幕优先；只有最终边界候选才会进入 ASR。</small>}
          </article>
          <article>
            <div><strong>qBittorrent</strong><BadgeLike good={Boolean(data.integrations?.qbittorrent?.authenticated)} label={data.integrations?.qbittorrent?.authenticated ? "已连通" : data.integrations?.qbittorrent?.configured ? "待检测" : "未配置"} /></div>
            <p>{data.integrations?.qbittorrent?.host ? `${data.integrations.qbittorrent.scheme}://${data.integrations.qbittorrent.host}` : "尚未填写 WebUI 地址"}{data.integrations?.qbittorrent?.version ? ` · v${data.integrations.qbittorrent.version}` : ""}</p>
            {data.integrations?.qbittorrent?.error ? <small>{data.integrations.qbittorrent.error}</small> : <small>检测只登录 Web API 并读取版本，不会推送种子。</small>}
            <button className="button-secondary" disabled={adminAction === "qbittorrent" || !data.integrations?.qbittorrent?.configured} onClick={() => void testQbittorrent()}>{adminAction === "qbittorrent" ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}检测连接</button>
          </article>
        </div>
      </section>

      <section className="admin-section">
        <header><div><span className="section-kicker">长期任务</span><h2>订阅调度健康</h2></div><span>{data.subscriptions?.enabled ? data.subscriptions?.healthy ? "调度器正常" : "调度器心跳异常" : "当前部署未启用调度器"}</span></header>
        <div className="admin-metric-grid">
          <div><strong>{(data.subscriptions?.workers || []).filter((row: AnyRow) => row.healthy).length}</strong><span>健康工作进程</span></div>
          <div><strong>{(data.subscriptions?.active_leases || []).length}</strong><span>正在执行</span></div>
          <div><strong>{(data.subscriptions?.failed_rules || []).length}</strong><span>退避中的规则</span></div>
          <div><strong>{data.subscriptions?.max_concurrency || 1}</strong><span>单进程并发上限</span></div>
        </div>
        {(data.subscriptions?.workers || []).length ? <div className="admin-task-list admin-scheduler-workers">{data.subscriptions.workers.map((worker: AnyRow) => <p key={worker.worker_id}><span><strong>{worker.worker_id}</strong><small>最近周期 {worker.last_cycle_at || "尚未执行"} · 累计处理 {worker.processed_count || 0}</small></span><b className={`task-status ${worker.healthy ? "done" : "failed"}`}>{worker.healthy ? `${worker.heartbeat_age_seconds || 0}s` : "心跳过期"}</b></p>)}</div> : <div className="inline-notice">若线上需要主动提醒，请启用订阅调度器；仅创建规则但没有常驻 worker 不会自动发送。</div>}
        {(data.subscriptions?.failed_rules || []).length ? <details className="admin-models admin-scheduler-failures" open><summary>失败与退避队列</summary>{data.subscriptions.failed_rules.map((rule: AnyRow) => <p key={rule.id}><strong>{rule.title}</strong><span>连续失败 {rule.consecutive_failures}</span><small>{rule.last_error || "未知错误"}<br />下次自动重试：{rule.retry_after || "待定"}</small><button className="button-secondary" disabled={adminAction === `retry-${rule.id}`} onClick={() => void retrySubscription(rule.id)}>{adminAction === `retry-${rule.id}` ? "重试中" : "立即重试"}</button></p>)}</details> : null}
      </section>

      <section className="admin-section">
        <header><div><span className="section-kicker">系列纠错</span><h2>复杂作品人工顺序</h2></div><span>{data.series_overrides?.status?.rules || 0} 条规则 · {data.series_overrides?.status?.subjects || 0} 个条目</span></header>
        <p className="card-note">仅为 Bangumi 关系图无法可靠表达的复杂系列维护覆盖规则。required 会阻塞后续；optional 和 skip 不阻塞；alternates 是替代演绎。</p>
        {(data.series_overrides?.rules || []).length ? <div className="admin-series-list">{data.series_overrides.rules.map((rule: AnyRow) => <article key={rule.id}><span><strong>{rule.title}</strong><small>{rule.id} · 主线 {rule.mainline?.length || 0} · 旁支 {rule.optional?.length || 0} · 替代 {rule.alternates?.length || 0}</small></span><button className="button-secondary" onClick={() => editSeriesRule(rule)}>编辑</button><button className="button-quiet danger" disabled={adminAction === `series-delete-${rule.id}`} onClick={() => void deleteSeriesRule(rule.id)}>删除</button></article>)}</div> : <div className="inline-notice">尚未配置人工规则；所有系列继续使用 Bangumi 关系图。</div>}
        <details className="admin-series-editor" open id="series-rule-editor"><summary>新增或编辑规则</summary><textarea value={seriesDraft} onChange={(event) => setSeriesDraft(event.target.value)} rows={16} spellCheck={false} /><div className="panel-actions"><button className="button-primary" disabled={adminAction === "series-save"} onClick={() => void saveSeriesRule()}>{adminAction === "series-save" ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}保存并立即生效</button><button className="button-secondary" onClick={() => setSeriesDraft(EMPTY_SERIES_RULE)}>新建模板</button></div></details>
      </section>

      <section className="admin-section">
        <header><div><span className="section-kicker">动画作品中心</span><h2>模块速度与可靠性</h2></div><span>{hubMetrics.runs || 0} 次模块请求 · 持久缓存 {data.anime_hub?.artifact_cache?.entries || 0} 条</span></header>
        <div className="admin-metric-grid">
          <div><strong>{duration(hubMetrics.p50_ms)}</strong><span>整体 P50</span></div>
          <div><strong>{duration(hubMetrics.p95_ms)}</strong><span>整体 P95</span></div>
          <div><strong>{data.anime_hub?.artifact_cache?.hits || 0}</strong><span>跨请求缓存命中</span></div>
          <div><strong>{Object.keys(hubMetrics.modules || {}).length}</strong><span>有观测的模块</span></div>
        </div>
        <div className="admin-table"><div className="admin-table-head"><span>模块</span><span>P50</span><span>P95</span><span>失败 / 缓存</span></div>{Object.entries(hubMetrics.modules || {}).map(([name, module]: [string, any]) => <div key={name}><strong>{name}</strong><span>{duration(module.p50_ms)}</span><span>{duration(module.p95_ms)}</span><span>{pct(module.failure_rate)} / {module.cache_hit_rate == null ? "—" : pct(module.cache_hit_rate)}</span></div>)}</div>
      </section>

      <section className="admin-section">
        <header><div><span className="section-kicker">推荐评估</span><h2>准确率、解释与速度一起看</h2></div><span className="admin-version">{Object.entries(evaluation.strategy_versions || {}).map(([key, value]) => `${key} · ${value} 批`).join("；") || "暂无版本数据"}</span></header>
        <div className="admin-metric-grid">
          <div><strong>{pct(evaluation.acceptance_at_k?.["1"])}</strong><span>Acceptance@1</span></div>
          <div><strong>{pct(evaluation.acceptance_at_k?.["3"])}</strong><span>Acceptance@3</span></div>
          <div><strong>{pct(evaluation.explanations?.claim_support_coverage)}</strong><span>解释有证据支撑</span></div>
          <div><strong>{pct(evaluation.explanations?.integrity_rate)}</strong><span>理由与证据一致</span></div>
          <div><strong>{pct(evaluation.catalog?.repeat_rate)}</strong><span>跨批次重复率</span></div>
          <div><strong>{duration(evaluation.performance?.average_ms)}</strong><span>平均推荐耗时</span></div>
          <div><strong>{data.recommendations.artifact_cache.entries}</strong><span>持久证据缓存</span></div>
        </div>
        <div className="admin-table"><div className="admin-table-head"><span>媒介 / 场景</span><span>曝光批次</span><span>采纳批次</span><span>采纳率</span></div>{(evaluation.segments || []).map((row: AnyRow) => <div key={`${row.subject_type}-${row.scenario}`}><strong>{row.subject_type} / {row.scenario}</strong><span>{row.visible_sets}</span><span>{row.accepted_sets}</span><span>{pct(row.acceptance_rate)}</span></div>)}</div>
        {(evaluation.experiments || []).length ? <div className="admin-experiments"><strong>稳定策略实验</strong><div>{evaluation.experiments.map((row: AnyRow) => <article key={`${row.id}-${row.variant}`}><span>{row.variant === "control" ? "基准组" : "个性化证据组"}</span><b>{pct(row.acceptance_at_3)}</b><small>Acceptance@3 · {row.visible_sets} 个可见批次</small><small>MRR {Number(row.mrr || 0).toFixed(2)} · P95 {duration(row.p95_ms)}</small></article>)}</div><p>同一用户与媒介会稳定停留在同一组；样本不足时只展示数据，不自动宣称实验组更好。</p></div> : null}
        <details className="admin-models"><summary>查看协同模型状态</summary>{(data.recommendations.models || []).map((model: AnyRow) => <p key={model.subject_type}><strong>{model.subject_type}</strong><span>{model.available ? (model.stale ? "可用但过期" : "可用") : "未发布"}</span><small>{model.version || model.warnings?.join("；") || "无版本"}</small></p>)}</details>
      </section>

      <section className="admin-section">
        <header><div><span className="section-kicker">推荐批次诊断</span><h2>从召回到证据重排逐层查看</h2></div><span>仅管理员可查看；不包含私人聊天内容。</span></header>
        <div className="admin-batch-list">{(data.recommendations.batches || []).map((batch: AnyRow) => <button key={batch.id} onClick={() => void openBatch(batch.id)} disabled={batchBusy === batch.id}><span className="admin-batch-main"><strong>@{batch.username} · {batch.subject_type} / {batch.scenario}</strong><small>{new Date(batch.created_at).toLocaleString("zh-CN")} · {batch.candidate_count || "?"} 个候选 → {batch.item_count} 张卡片</small><em>{(batch.preview || []).map((item: AnyRow) => item.name).join("、") || "暂无卡片"}</em></span><span className="admin-batch-result"><b>{batch.accepted_items ? `采纳 ${batch.accepted_items}` : batch.dismissed_items ? `减少 ${batch.dismissed_items}` : "暂无决策"}</b><small>{batch.experiment?.variant === "personalized-evidence-v1" ? "个性化证据组" : "基准组"} · {duration(batch.duration_ms)}</small></span>{batchBusy === batch.id ? <LoaderCircle className="spin" size={16} /> : <ChevronRight size={16} />}</button>)}</div>
        {!(data.recommendations.batches || []).length ? <div className="feature-empty"><BarChart3 size={24} /><strong>还没有推荐批次</strong><span>线上产生推荐后，这里会显示每一层的选择依据。</span></div> : null}
      </section>

      <section className="admin-section moderation-section">
        <header><div><span className="section-kicker">社区治理</span><h2>举报队列</h2></div><span>隐藏可恢复；永久删除只保留在明确需要时使用。</span></header>
        <div className="moderation-list">{(moderation.reports || []).map((report: AnyRow) => <article className={report.status !== "pending" ? "resolved" : ""} key={report.id}><div><strong>@{report.display_name || "已删除作者"}</strong><span>{report.reason}</span><small>{report.content || "原留言已删除"}</small></div><div className="moderation-meta"><span>{report.status === "pending" ? "待处理" : report.status === "resolved" ? "已处理" : "已忽略"}</span>{report.status === "pending" ? <><button className="button-secondary" onClick={() => void moderate(report.comment_id, report.moderation_status === "hidden" ? "restore" : "hide")}>{report.moderation_status === "hidden" ? <Eye size={14} /> : <EyeOff size={14} />}{report.moderation_status === "hidden" ? "恢复留言" : "先隐藏"}</button><button className="button-secondary" onClick={() => void resolve(report.id, "resolved")}><Check size={14} />标记已处理</button><button className="button-quiet" onClick={() => void resolve(report.id, "dismissed")}>忽略举报</button></> : <small>{report.resolved_by ? `@${report.resolved_by}` : ""} {report.resolution_note}</small>}</div></article>)}{!(moderation.reports || []).length ? <div className="feature-empty"><ShieldCheck size={25} /><strong>暂无举报</strong><span>社区目前很安静。</span></div> : null}</div>
        {(data.community.comments || []).some((comment: AnyRow) => comment.moderation_status === "hidden") ? <details className="admin-hidden"><summary>查看已隐藏留言</summary>{data.community.comments.filter((comment: AnyRow) => comment.moderation_status === "hidden").map((comment: AnyRow) => <p key={comment.id}><span><strong>@{comment.display_name}</strong>{comment.content}</span><button className="button-secondary" onClick={() => void moderate(comment.id, "restore")}><RotateCcw size={14} />恢复</button><button className="button-quiet danger" onClick={() => void moderate(comment.id, "delete")}><Trash2 size={14} />删除</button></p>)}</details> : null}
      </section>

      <div className="admin-two-column">
        <section className="admin-section"><header><div><span className="section-kicker">后台任务</span><h2>最近运行</h2></div></header><div className="admin-task-list">{allTasks.slice(0, 20).map((task: AnyRow) => <p key={`${task.namespace}-${task.id}`}><span><strong>{task.kind}</strong><small>{task.id.slice(0, 10)} · {new Date(task.started_at * 1000).toLocaleString("zh-CN")}</small></span><b className={`task-status ${task.status}`}>{task.status}</b></p>)}</div></section>
        <section className="admin-section"><header><div><span className="section-kicker">系统与存储</span><h2>轻量诊断</h2></div><span>v{data.system.version}{data.system.commit ? ` · ${data.system.commit.slice(0, 8)}` : ""}</span></header><div className="admin-system"><p><Activity size={15} /><span>运行时间</span><strong>{Math.floor(data.system.uptime_seconds / 3600)} 小时</strong></p><p><Database size={15} /><span>记忆用户</span><strong>{data.system.memory_users}</strong></p><p><HardDrive size={15} /><span>磁盘可用</span><strong>{bytes(data.system.disk.free)}</strong></p></div><div className="admin-storage">{Object.entries(data.system.storage || {}).map(([name, item]: [string, any]) => <p key={name}><span>{name}</span><strong>{bytes(item.bytes)}</strong></p>)}</div></section>
      </div>
      {batchDetail ? <div className="global-overlay admin-diagnostic-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) setBatchDetail(null); }}><section className="global-modal wide admin-diagnostic-modal" role="dialog" aria-modal="true" aria-labelledby="diagnostic-title"><header className="global-modal-head"><div><strong id="diagnostic-title">推荐诊断 · @{batchDetail.username}</strong><span>{batchDetail.subject_type} / {batchDetail.scenario} · {new Date(batchDetail.created_at).toLocaleString("zh-CN")}</span></div><button className="icon-button" aria-label="关闭诊断" onClick={() => setBatchDetail(null)}><X size={17} /></button></header><div className="admin-diagnostic-body"><section className="diagnostic-summary"><article><span>实验组</span><strong>{batchDetail.request?._experiment?.variant === "personalized-evidence-v1" ? "个性化证据组" : "基准组"}</strong></article><article><span>总耗时</span><strong>{duration(batchDetail.request?._performance?.total_ms)}</strong></article><article><span>召回候选</span><strong>{diagnosticTrace?.candidate_count ?? "未记录"}</strong></article><article><span>证据候选</span><strong>{batchDetail.request?._performance?.evidence_candidates ?? "未记录"}</strong></article></section>{!hasDiagnosticTrace ? <div className="inline-notice">这是升级前生成的推荐批次，只保留了最终卡片；新的批次会完整记录召回、淘汰与证据重排过程。</div> : null}{hasDiagnosticTrace ? <><details open><summary>阶段耗时与淘汰原因</summary><div className="diagnostic-pairs">{Object.entries(batchDetail.request?._performance?.phases_ms || {}).map(([key, value]) => <p key={key}><span>{key.replaceAll("_ms", "")}</span><b>{duration(Number(value))}</b></p>)}{Object.entries(diagnosticTrace?.elimination_counts || {}).map(([key, value]) => <p key={key}><span>淘汰 · {key}</span><b>{String(value)}</b></p>)}</div></details><details open><summary>证据重排前后的候选</summary><div className="diagnostic-finalists">{(diagnosticTrace?.finalist_pool || []).map((item: AnyRow) => <article className={item.selected ? "selected" : ""} key={item.id}><header><span><strong>{item.name}</strong><small>#{item.id} · {item.selected ? "进入最终结果" : "未进入最终结果"}</small></span><b>{Number(item.before_evidence_score || 0).toFixed(2)} → {Number(item.final_score || 0).toFixed(2)}</b></header><div className="score-breakdown">{Object.entries(item.score_breakdown || {}).sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1]))).map(([key, value]) => <span className={Number(value) < 0 ? "negative" : ""} key={key}>{scoreLabels[key] || key} {Number(value) > 0 ? "+" : ""}{Number(value).toFixed(2)}</span>)}</div><p>{(item.recall_signals || []).join("；") || "仅由综合排序进入候选池"}</p><small>解释支撑 {item.claim_support?.supported || 0}/{item.claim_support?.checked || 0}</small></article>)}</div></details></> : null}<details><summary>最终卡片与用户动作</summary><div className="diagnostic-items">{(batchDetail.items || []).map((item: AnyRow) => <article key={item.id}><img src={item.image || "/icon.svg"} alt="" /><span><strong>{item.position}. {item.name}</strong><small>{(item.why_recalled || []).join("；")}</small><em>{item.latest_decision?.event ? `用户动作：${item.latest_decision.event}` : "尚无明确动作"}</em></span><b>{Number(item.score || 0).toFixed(2)}</b></article>)}</div></details></div></section></div> : null}
    </main>
  );
}
