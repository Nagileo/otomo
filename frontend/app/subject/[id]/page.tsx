"use client";

import { ArrowLeft, ExternalLink, MessageCircle, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { PageHeader } from "../../../components/page-header";
import { SubjectActions } from "../../../components/subject-actions";
import { createShareSnapshot, productFetch } from "../../../lib/api";
import { useExperience } from "../../../lib/experience";
import { AnimeWatchHubPanel } from "../../panels/media";
import { SubjectDossierPanel } from "../../panels/product";

type HubStage = "overview" | "core" | "videos" | "releases" | "music";
type StageState = { loading: boolean; error: string };

const STAGES: HubStage[] = ["overview", "core", "videos", "releases", "music"];
const STAGE_LABEL: Record<HubStage, string> = {
  overview: "个性化概览",
  core: "正版与系列",
  videos: "B站视频",
  releases: "RSS / 离线",
  music: "音乐",
};

function hasData(value: unknown) {
  return Boolean(value && typeof value === "object" && Object.keys(value as Record<string, unknown>).length);
}

function mergeHub(current: any, next: any) {
  if (!current) return next;
  const choose = (key: string) => hasData(next?.[key]) ? next[key] : current[key];
  return {
    ...current,
    ...next,
    subject: choose("subject"),
    identity: next?.identity || current.identity,
    resolution: next?.resolution || current.resolution,
    lifecycle: next?.lifecycle || current.lifecycle,
    viewer_state: choose("viewer_state"),
    preferences: choose("preferences"),
    overview: choose("overview"),
    reputation: choose("reputation"),
    relations: next?.relations?.length ? next.relations : current.relations,
    episode_radar: choose("episode_radar"),
    trend: choose("trend"),
    music: choose("music"),
    online: choose("online"),
    releases: choose("releases"),
    bilibili: next?.bilibili || current.bilibili,
    series_progress: next?.series_progress || current.series_progress,
    modules: { ...(current.modules || {}), ...(next?.modules || {}) },
    staff_signals: Array.from(new Set([...(current.staff_signals || []), ...(next?.staff_signals || [])])),
    status_summary: Array.from(new Set([...(current.status_summary || []), ...(next?.status_summary || [])])),
    caveats: Array.from(new Set([...(current.caveats || []), ...(next?.caveats || [])])),
  };
}

export default function SubjectPage({ params }: { params: { id: string } }) {
  const { csrf, authenticated } = useExperience();
  const requestKey = useRef(0);
  const [watchHub, setWatchHub] = useState<any>(null);
  const [dossier, setDossier] = useState<any>(null);
  const [sources, setSources] = useState<any[]>([]);
  const [isAnime, setIsAnime] = useState<boolean | null>(null);
  const [identityLoading, setIdentityLoading] = useState(true);
  const [stages, setStages] = useState<Record<HubStage, StageState>>(() => Object.fromEntries(STAGES.map((stage) => [stage, { loading: false, error: "" }])) as Record<HubStage, StageState>);
  const [follow, setFollow] = useState<any>(null);
  const [pendingDownload, setPendingDownload] = useState<any>(null);
  const [pendingWrite, setPendingWrite] = useState<any>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [share, setShare] = useState("");

  const mergeSources = (incoming: any[]) => setSources((rows) => {
    const merged = [...rows, ...incoming];
    return merged.filter((item, index) => merged.findIndex((candidate) => candidate.url === item.url) === index);
  });

  async function loadStage(stage: HubStage, key = requestKey.current) {
    setStages((current) => ({ ...current, [stage]: { loading: true, error: "" } }));
    try {
      const payload = await productFetch(`/product/subjects/${encodeURIComponent(params.id)}/watch-hub?stage=${stage}&include_release=true&include_videos=true&include_viewer_state=false&video_limit=5&spoiler_level=none`);
      if (key !== requestKey.current) return;
      setWatchHub((current: any) => mergeHub(current, payload.data));
      mergeSources(payload.sources || []);
    } catch (stageError) {
      if (key !== requestKey.current) return;
      setStages((current) => ({ ...current, [stage]: { loading: false, error: String(stageError) } }));
      return;
    }
    if (key === requestKey.current) setStages((current) => ({ ...current, [stage]: { loading: false, error: "" } }));
  }

  useEffect(() => {
    const key = requestKey.current + 1;
    requestKey.current = key;
    setWatchHub(null);
    setDossier(null);
    setSources([]);
    setIsAnime(null);
    setIdentityLoading(true);
    setError("");
    setNotice("");
    setPendingDownload(null);
    setPendingWrite(null);
    setStages(Object.fromEntries(STAGES.map((stage) => [stage, { loading: false, error: "" }])) as Record<HubStage, StageState>);
    try {
      const cached = sessionStorage.getItem(`otomo:subject:${params.id}`);
      if (cached) {
        const artifact = JSON.parse(cached);
        if (artifact?.subject?.id) {
          setWatchHub(artifact);
          setIsAnime(artifact.subject.type_name === "anime");
        }
      }
    } catch {
      sessionStorage.removeItem(`otomo:subject:${params.id}`);
    }

    productFetch(`/product/subjects/${encodeURIComponent(params.id)}/watch-hub?stage=identity&include_release=false&include_videos=false`)
      .then((payload) => {
        if (key !== requestKey.current) return;
        const hub = payload.data;
        const anime = hub?.subject?.type_name === "anime";
        setIsAnime(anime);
        setWatchHub(hub);
        mergeSources(payload.sources || []);
        setIdentityLoading(false);
        if (anime) {
          STAGES.forEach((stage) => { void loadStage(stage, key); });
          if (authenticated) {
            void productFetch(`/product/subjects/${encodeURIComponent(params.id)}/follow`)
              .then((followPayload) => { if (key === requestKey.current) setFollow(followPayload.data || null); })
              .catch(() => undefined);
          }
          return;
        }
        return productFetch(`/product/subjects/${encodeURIComponent(params.id)}?spoiler_level=none`)
          .then((dossierPayload) => {
            if (key !== requestKey.current) return;
            setDossier(dossierPayload.data);
            mergeSources(dossierPayload.sources || []);
          });
      })
      .catch((loadError) => {
        if (key === requestKey.current) {
          setIdentityLoading(false);
          setError(String(loadError));
        }
      });
  // Account state is included so progress and follow status refresh immediately after login.
  }, [params.id, authenticated]);

  async function prepareDownloaderPush(payload: Record<string, any>) {
    try {
      const result = await productFetch("/actions/prepare-downloader-push", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
        body: JSON.stringify({ ...payload, reason: "从动画作品中心选择具体发布项，准备推送到下载器" }),
      });
      setPendingDownload(result.data?.action || null);
    } catch (actionError) { setError(String(actionError)); }
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
      setNotice(ok ? "已推送到下载器。" : "已取消下载器操作。");
    } catch (actionError) { setError(String(actionError)); }
  }

  async function prepareBangumiWrite(subjectId: number, subjectName: string, collectionType = 3, upToEpisode?: number) {
    try {
      const operation = upToEpisode ? "mark_episodes_watched" : "set_collection";
      const result = await productFetch("/actions/prepare-write", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
        body: JSON.stringify({
          operation,
          subject_id: subjectId,
          subject_name: subjectName,
          collection_type: collectionType,
          up_to_episode: upToEpisode,
          reason: upToEpisode ? `从动画作品中心标记看到第 ${upToEpisode} 集` : "从动画作品中心更新收藏状态",
        }),
      });
      setPendingWrite(result.data?.action || null);
    } catch (actionError) { setError(String(actionError)); }
  }

  async function confirmBangumiWrite(ok: boolean) {
    if (!pendingWrite?.id) return;
    try {
      await productFetch(`/actions/${ok ? "confirm" : "cancel"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
        body: JSON.stringify({ action_id: pendingWrite.id, reason: ok ? "" : "用户在作品中心取消" }),
      });
      setPendingWrite(null);
      setNotice(ok ? "Bangumi 已按你的确认更新。" : "已取消 Bangumi 写回。");
      if (ok) await Promise.all([loadStage("core"), loadStage("overview")]);
    } catch (actionError) { setError(String(actionError)); }
  }

  async function createRssFollow(payload: Record<string, any>) {
    const existing = await productFetch("/subscriptions/rules");
    const duplicate = (existing.rules || []).find((rule: any) => rule.kind === "rss_release" && rule.filters?.rss_url === payload.rss_url);
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
    if (!duplicate) {
      await productFetch("/subscriptions/rules", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
        body: JSON.stringify({
          kind: "rss_release",
          title: `${payload.title} · ${payload.subgroup} 新资源`,
          enabled: true,
          filters: { ...payload, include_watch_plan: false },
          schedule: { timezone, hour: 9, minute: 0, interval_minutes: 60 },
          channels: ["inbox"],
          template: "normal",
        }),
      });
    }
    await productFetch(`/product/subjects/${encodeURIComponent(params.id)}/watch-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
      body: JSON.stringify({ name: payload.title, status: "watching", priority: 2, reason: "已在作品中心选择字幕组 RSS", rss_url: payload.rss_url, subgroup: payload.subgroup }),
    });
    setNotice(duplicate ? "这个 RSS 已在追更，并已同步到本地计划。" : "RSS 追更和本地计划已一起建立。");
  }

  async function updatePreferences(payload: Record<string, any>) {
    const result = await productFetch(`/product/subjects/${encodeURIComponent(params.id)}/preferences`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
      body: JSON.stringify(payload),
    });
    setWatchHub((current: any) => ({ ...current, preferences: result.data || current.preferences }));
    if (payload.video_id || payload.uploader) await loadStage("videos");
    else await loadStage("releases");
  }

  async function addWatchPlan() {
    const subject = watchHub?.subject || {};
    await productFetch(`/product/subjects/${encodeURIComponent(params.id)}/watch-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
      body: JSON.stringify({ name: subject.name || `subject ${params.id}`, status: watchHub?.viewer_state?.collection_state === "watching" ? "watching" : "backlog", priority: 3, reason: "从动画作品中心手动加入" }),
    });
  }

  async function toggleFollow() {
    if (follow) {
      await productFetch(`/product/subjects/${encodeURIComponent(params.id)}/follow`, { method: "DELETE", headers: { "x-otomo-csrf": csrf } });
      setFollow(null);
      return;
    }
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
    const result = await productFetch(`/product/subjects/${encodeURIComponent(params.id)}/follow`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-otomo-csrf": csrf },
      body: JSON.stringify({ title: watchHub?.subject?.name || `subject ${params.id}`, events: ["official", "release", "sequel", "video", "progress"], interval_minutes: 60, timezone, channels: ["inbox"] }),
    });
    setFollow(result.data || null);
  }

  async function shareSnapshot(request: Record<string, any>) {
    try {
      const payload = await createShareSnapshot(request, csrf, sources);
      setShare(payload.url || payload.snapshot?.url || "");
    } catch (shareError) { setError(String(shareError)); }
  }

  const subject = watchHub?.subject || dossier?.subject || {};
  const resolution = watchHub?.resolution || {};
  return (
    <main className="page-frame subject-page">
      <PageHeader
        eyebrow={isAnime ? "动画作品中心" : "作品档案"}
        title={subject.name || (identityLoading ? "正在确认作品…" : "作品档案")}
        description={subject.name_jp && subject.name_jp !== subject.name ? subject.name_jp : subject.date || "以 Bangumi 条目为统一身份"}
        actions={<><Link className="button-secondary icon-label" href="/discover"><ArrowLeft size={16} />返回发现</Link><Link className="button-secondary icon-label" href={`/chat?q=${encodeURIComponent(`详细评价《${subject.name || params.id}》`)}`}><MessageCircle size={16} />继续问</Link>{subject.id ? <a className="button-secondary icon-label" href={`https://bgm.tv/subject/${subject.id}`} target="_blank" rel="noreferrer">Bangumi <ExternalLink size={15} /></a> : null}</>}
      />
      {error ? <div className="surface-error">{error}</div> : null}
      {notice ? <div className="inline-notice">{notice}</div> : null}
      {identityLoading ? <div className="surface-loading">正在确认具体 Bangumi 条目、季度和版本；先不加载慢资源…</div> : null}
      {resolution.status === "ambiguous" ? <div className="surface-error"><strong>作品版本存在歧义，未自动选择搜索第一项。</strong><div className="compact-list">{(resolution.candidates || []).map((candidate: any) => <Link href={`/subject/${candidate.subject_id}`} key={candidate.subject_id}>{candidate.title} · {candidate.date || candidate.platform}</Link>)}</div></div> : null}
      {isAnime ? <div className="hub-module-status">{STAGES.map((stage) => {
        const state = stages[stage];
        const backendState = watchHub?.modules?.[stage];
        const failed = Boolean(state.error || backendState?.status === "failed");
        return <button type="button" key={stage} className={failed ? "failed" : state.loading ? "loading" : "ready"} onClick={() => failed ? void loadStage(stage) : undefined}>
          {state.loading ? <RefreshCw size={13} className="spin" /> : null}<strong>{STAGE_LABEL[stage]}</strong><span>{failed ? "加载失败 · 点击重试" : state.loading ? "加载中" : backendState?.duration_ms ? `${backendState.duration_ms} ms` : "已就绪"}</span>
        </button>;
      })}</div> : null}
      {share ? <div className="inline-notice">分享页已生成：<a href={share} target="_blank" rel="noreferrer">打开公开快照</a></div> : null}
      {subject.id ? <SubjectActions subject={subject} /> : null}
      {pendingDownload ? <div className="inline-confirm"><strong>确认推送到你的 qBittorrent？</strong><span>{pendingDownload.summary}</span><div><button className="button-secondary" onClick={() => void confirmDownloader(false)}>取消</button><button className="button-primary" onClick={() => void confirmDownloader(true)}>确认推送</button></div></div> : null}
      {pendingWrite ? <div className="inline-confirm"><strong>确认写回 Bangumi？</strong><span>{pendingWrite.summary}</span><div><button className="button-secondary" onClick={() => void confirmBangumiWrite(false)}>取消</button><button className="button-primary" onClick={() => void confirmBangumiWrite(true)}>确认写回</button></div></div> : null}
      {isAnime && watchHub ? <AnimeWatchHubPanel
        data={watchHub}
        onPrepareDownloaderPush={authenticated ? (payload) => void prepareDownloaderPush(payload) : undefined}
        onPrepareWrite={authenticated ? (subjectId, subjectName, collectionType) => void prepareBangumiWrite(subjectId, subjectName, collectionType) : undefined}
        onPrepareProgress={authenticated ? (subjectId, subjectName, upToEpisode) => void prepareBangumiWrite(subjectId, subjectName, 3, upToEpisode) : undefined}
        onCreateRssFollow={authenticated ? createRssFollow : undefined}
        onUpdatePreferences={authenticated ? updatePreferences : undefined}
        onAddWatchPlan={authenticated ? addWatchPlan : undefined}
        onToggleFollow={authenticated ? toggleFollow : undefined}
        isFollowing={Boolean(follow)}
      /> : null}
      {isAnime === false && !dossier && !error ? <div className="surface-loading">正在汇总该媒介的评价、关系与购买入口…</div> : null}
      {dossier ? <SubjectDossierPanel data={dossier} productView onShareSnapshot={authenticated ? (request) => void shareSnapshot(request) : undefined} /> : null}
    </main>
  );
}
