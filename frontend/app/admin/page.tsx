"use client";

import {
  Activity, BarChart3, Check, Database, Eye, EyeOff, Flag, Gauge,
  HardDrive, LoaderCircle, RefreshCw, RotateCcw, ShieldCheck, Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

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

export default function AdminPage() {
  const exp = useExperience();
  const [data, setData] = useState<AnyRow | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [days, setDays] = useState(30);

  async function load(nextDays = days) {
    setBusy(true); setError("");
    try {
      const payload = await productFetch(`/admin/overview?days=${nextDays}`);
      setData(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  }

  useEffect(() => { void exp.refreshAuthSession().then(() => load()); }, []);

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

  const evaluation = data?.recommendations?.evaluation?.current || {};
  const moderation = data?.community?.moderation || {};
  const allTasks = useMemo(() => [
    ...(data?.tasks?.chat || []).map((row: AnyRow) => ({ ...row, kind: "对话" })),
    ...(data?.tasks?.recommendation || []).map((row: AnyRow) => ({ ...row, kind: "推荐" })),
  ].sort((a, b) => Number(b.started_at || 0) - Number(a.started_at || 0)), [data]);

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

      <section className="admin-section">
        <header><div><span className="section-kicker">推荐评估</span><h2>准确率、解释与速度一起看</h2></div><span className="admin-version">{Object.entries(evaluation.strategy_versions || {}).map(([key, value]) => `${key} · ${value} 批`).join("；") || "暂无版本数据"}</span></header>
        <div className="admin-metric-grid">
          <div><strong>{pct(evaluation.acceptance_at_k?.["1"])}</strong><span>Acceptance@1</span></div>
          <div><strong>{pct(evaluation.acceptance_at_k?.["3"])}</strong><span>Acceptance@3</span></div>
          <div><strong>{pct(evaluation.explanations?.claim_support_coverage)}</strong><span>解释有证据支撑</span></div>
          <div><strong>{pct(evaluation.catalog?.repeat_rate)}</strong><span>跨批次重复率</span></div>
          <div><strong>{duration(evaluation.performance?.average_ms)}</strong><span>平均推荐耗时</span></div>
          <div><strong>{data.recommendations.artifact_cache.entries}</strong><span>持久证据缓存</span></div>
        </div>
        <div className="admin-table"><div className="admin-table-head"><span>媒介 / 场景</span><span>曝光批次</span><span>采纳批次</span><span>采纳率</span></div>{(evaluation.segments || []).map((row: AnyRow) => <div key={`${row.subject_type}-${row.scenario}`}><strong>{row.subject_type} / {row.scenario}</strong><span>{row.visible_sets}</span><span>{row.accepted_sets}</span><span>{pct(row.acceptance_rate)}</span></div>)}</div>
        <details className="admin-models"><summary>查看协同模型状态</summary>{(data.recommendations.models || []).map((model: AnyRow) => <p key={model.subject_type}><strong>{model.subject_type}</strong><span>{model.available ? (model.stale ? "可用但过期" : "可用") : "未发布"}</span><small>{model.version || model.warnings?.join("；") || "无版本"}</small></p>)}</details>
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
    </main>
  );
}
