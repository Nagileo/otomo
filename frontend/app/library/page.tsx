"use client";

import { BarChart3, FileText, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { PageHeader } from "../../components/page-header";
import { AuthGate } from "../../components/auth-gate";
import { createShareSnapshot, productFetch } from "../../lib/api";
import { useExperience } from "../../lib/experience";
import { MonthlyWatchReportPanel } from "../panels/product";
import { CollectionDashboardPanel } from "../panels/report";

const media = [["anime", "动画"], ["book", "书籍"], ["game", "游戏"], ["music", "音乐"], ["real", "三次元"]] as const;

export default function LibraryPage() {
  const [subjectType, setSubjectType] = useState("anime");
  const [dashboard, setDashboard] = useState<any>(null);
  const [report, setReport] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [csrf, setCsrf] = useState("");
  const [share, setShare] = useState("");
  const exp = useExperience();

  useEffect(() => {
    setCsrf(exp.csrf);
    if (exp.authReady && exp.authenticated) void load("anime", false);
  }, [exp.authReady, exp.authenticated, exp.csrf]);

  async function load(type = subjectType, track = true) {
    setBusy(true); setError(""); setReport(null);
    try {
      const payload = await productFetch(
        `/product/library?subject_types=${type}&enrich_people=true`,
        undefined,
        { track, label: "汇总收藏" },
      );
      setDashboard(payload.data);
    } catch (e) { setError(String(e)); setDashboard(null); }
    finally { setBusy(false); }
  }

  async function loadReport() {
    setBusy(true); setError("");
    try {
      const payload = await productFetch(
        `/product/monthly-report?subject_type=${subjectType}&period=month`,
        undefined,
        { track: true, label: "生成观看报告" },
      );
      setReport(payload.data);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function shareSnapshot(request: Record<string, any>) {
    try {
      const payload = await createShareSnapshot(request, csrf);
      setShare(payload.url || payload.snapshot?.url || "");
    } catch (e) { setError(String(e)); }
  }

  return (
    <main className="page-frame library-page">
      <PageHeader eyebrow="Library" title="我的收藏" description="从收藏状态、评分分布和长期口味里看清已经看过什么，以及下一步该补什么。" actions={exp.authenticated ? <button className="button-secondary icon-label" onClick={() => void load()} disabled={busy}><RefreshCw size={16} />刷新</button> : undefined} />
      {!exp.authReady ? <div className="surface-loading">正在确认账户状态…</div> : null}
      {exp.authReady && !exp.authenticated ? <AuthGate eyebrow="PERSONAL LIBRARY" title="把你的收藏变成可读的口味档案" description="连接后才会读取收藏、评分和追番状态。分析结果只归属于当前账户，不会进入公共页面。" features={["收藏与评分分布", "长期口味与 staff 偏好", "月报与可分享快照"]} /> : null}
      {exp.authenticated ? <nav className="media-switch library-switch" aria-label="收藏媒介">
        {media.map(([value, label]) => <button key={value} className={subjectType === value ? "active" : ""} onClick={() => { setSubjectType(value); void load(value); }}>{label}</button>)}
      </nav> : null}
      {exp.authenticated && error ? <div className="surface-error">{error}</div> : null}
      {share ? <div className="inline-notice">月报分享页已生成：<a href={share} target="_blank" rel="noreferrer">打开公开快照</a></div> : null}
      {busy && !dashboard ? <div className="surface-loading">正在汇总收藏、标签与代表性 staff…</div> : null}
      {dashboard ? (
        <>
          <div className="library-toolbar"><span><BarChart3 size={17} /> 数据生成于 {String(dashboard.generated_at || "").slice(0, 16).replace("T", " ")}</span><button className="button-secondary icon-label" onClick={() => void loadReport()} disabled={busy}><FileText size={16} />生成本月报告</button></div>
          <CollectionDashboardPanel data={dashboard} />
        </>
      ) : null}
      {report ? <section className="workspace-section report-section"><MonthlyWatchReportPanel data={report} onShareSnapshot={(request) => void shareSnapshot(request)} /></section> : null}
    </main>
  );
}
