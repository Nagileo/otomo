"use client";

import { BarChart3, FileText, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { PageHeader } from "../../components/page-header";
import { authSession, BACKEND, createShareSnapshot, productFetch } from "../../lib/api";
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

  useEffect(() => {
    authSession().then((auth) => setCsrf(auth.csrf_token || "")).catch(() => undefined);
    void load("anime");
  }, []);

  async function load(type = subjectType) {
    setBusy(true); setError(""); setReport(null);
    try {
      const payload = await productFetch(`/product/library?subject_types=${type}&enrich_people=true`);
      setDashboard(payload.data);
    } catch (e) { setError(String(e)); setDashboard(null); }
    finally { setBusy(false); }
  }

  async function loadReport() {
    setBusy(true); setError("");
    try {
      const payload = await productFetch(`/product/monthly-report?subject_type=${subjectType}&period=month`);
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
      <PageHeader eyebrow="Library" title="我的收藏" description="从收藏状态、评分分布和长期口味里看清已经看过什么，以及下一步该补什么。" actions={<button className="button-secondary icon-label" onClick={() => void load()} disabled={busy}><RefreshCw size={16} />刷新</button>} />
      <nav className="media-switch library-switch" aria-label="收藏媒介">
        {media.map(([value, label]) => <button key={value} className={subjectType === value ? "active" : ""} onClick={() => { setSubjectType(value); void load(value); }}>{label}</button>)}
      </nav>
      {error ? <div className="surface-error">{error.includes("401") || error.includes("绑定") ? <><span>收藏分析需要连接 Bangumi。</span><a className="button-primary" href={`${BACKEND}/auth/bangumi/start`}>立即连接</a></> : error}</div> : null}
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
