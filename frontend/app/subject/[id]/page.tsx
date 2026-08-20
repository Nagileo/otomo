"use client";

import { ArrowLeft, ExternalLink, MessageCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "../../../components/page-header";
import { createShareSnapshot, productFetch } from "../../../lib/api";
import { useExperience } from "../../../lib/experience";
import { SubjectDossierPanel } from "../../panels/product";
import { AnimeWatchHubPanel } from "../../panels/media";
import { SubjectActions } from "../../../components/subject-actions";

export default function SubjectPage({ params }: { params: { id: string } }) {
  const { csrf, authenticated } = useExperience();
  const [data, setData] = useState<any>(null);
  const [sources, setSources] = useState<any[]>([]);
  const [watchHub, setWatchHub] = useState<any>(null);
  const [watchHubError, setWatchHubError] = useState("");
  const [pendingDownload, setPendingDownload] = useState<any>(null);
  const [error, setError] = useState("");
  const [share, setShare] = useState("");

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setWatchHub(null);
    setSources([]);
    setError("");
    setWatchHubError("");
    setPendingDownload(null);
    productFetch(`/product/subjects/${encodeURIComponent(params.id)}?spoiler_level=none&include_watch=false&include_release=false`)
      .then((payload) => {
        if (cancelled) return;
        setData(payload.data);
        setSources((rows) => [...rows, ...(payload.sources || [])]);
      })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    productFetch(
      `/product/subjects/${encodeURIComponent(params.id)}/watch-hub?include_release=true&include_videos=true&video_limit=5`,
      undefined,
      { track: true, label: "补齐在线观看、RSS与B站内容", href: `/subject/${params.id}` },
    )
      .then((payload) => {
        if (cancelled) return;
        setWatchHub(payload.data);
        setSources((rows) => [...rows, ...(payload.sources || [])]);
      })
      .catch((e) => { if (!cancelled) setWatchHubError(String(e)); });
    return () => { cancelled = true; };
  }, [params.id]);

  async function prepareDownloaderPush(payload: Record<string, any>) {
    try {
      const result = await productFetch("/actions/prepare-downloader-push", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
        body: JSON.stringify({ ...payload, reason: "从动画作品中心选择具体发布项，准备推送到下载器" }),
      });
      setPendingDownload(result.data?.action || null);
    } catch (e) { setWatchHubError(String(e)); }
  }

  async function confirmDownloader(ok: boolean) {
    if (!pendingDownload?.id) return;
    try {
      await productFetch(`/actions/${ok ? "confirm" : "cancel"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
        body: JSON.stringify({ action_id: pendingDownload.id, reason: ok ? "" : "用户在作品中心取消" }),
      });
      setPendingDownload(null);
    } catch (e) { setWatchHubError(String(e)); }
  }

  async function shareSnapshot(request: Record<string, any>) {
    try {
      const payload = await createShareSnapshot(request, csrf, sources);
      setShare(payload.url || payload.snapshot?.url || "");
    } catch (e) { setError(String(e)); }
  }

  const subject = data?.subject || {};
  return (
    <main className="page-frame subject-page">
      <PageHeader
        eyebrow="Subject dossier"
        title={subject.name || "作品档案"}
        description={subject.name && subject.name !== subject.name_cn ? subject.name_cn : "多源信息正在汇总"}
        actions={<><Link className="button-secondary icon-label" href="/discover"><ArrowLeft size={16} />返回发现</Link><Link className="button-secondary icon-label" href={`/chat?q=${encodeURIComponent(`详细评价《${subject.name || params.id}》`)}`}><MessageCircle size={16} />继续问</Link>{subject.id ? <a className="button-secondary icon-label" href={`https://bgm.tv/subject/${subject.id}`} target="_blank" rel="noreferrer">Bangumi <ExternalLink size={15} /></a> : null}</>}
      />
      {error ? <div className="surface-error">{error}</div> : null}
      {watchHubError ? <div className="surface-error">观看中心部分加载失败：{watchHubError}</div> : null}
      {!data && !error ? <div className="surface-loading">正在汇总无剧透评价、系列关系、音乐与观看入口…</div> : null}
      {!watchHub && !watchHubError ? <div className="surface-loading">正在核验正版页面、Staff直传、B站内容与离线RSS；作品档案可先继续浏览…</div> : null}
      {share ? <div className="inline-notice">分享页已生成：<a href={share} target="_blank" rel="noreferrer">打开公开快照</a></div> : null}
      {data ? <SubjectActions subject={subject} /> : null}
      {pendingDownload ? <div className="inline-confirm"><strong>确认推送到你的 qBittorrent？</strong><span>{pendingDownload.summary}</span><div><button className="button-secondary" onClick={() => void confirmDownloader(false)}>取消</button><button className="button-primary" onClick={() => void confirmDownloader(true)}>确认推送</button></div></div> : null}
      {watchHub ? <AnimeWatchHubPanel data={watchHub} onPrepareDownloaderPush={authenticated ? (payload) => void prepareDownloaderPush(payload) : undefined} /> : null}
      {data ? <SubjectDossierPanel data={data} productView onShareSnapshot={authenticated ? (request) => void shareSnapshot(request) : undefined} /> : null}
    </main>
  );
}
