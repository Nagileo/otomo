"use client";

import { BarChart3, BookmarkPlus, Compass, History, RefreshCw, SlidersHorizontal, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { PageHeader } from "../../components/page-header";
import { BACKEND, createShareSnapshot, productFetch } from "../../lib/api";
import { useExperience } from "../../lib/experience";
import { SeasonGuidePanel } from "../panels/media";
import { RecommendPanel } from "../panels/recommend";

type MediaType = "anime" | "book" | "game" | "music" | "real";
type Scenario = "general" | "tonight" | "season" | "backlog" | "gal_intro" | "cross_media";
type BookSubtype = "auto" | "comic" | "light_novel" | "novel";
type MusicSubtype = "auto" | "ost" | "theme_song" | "character_song" | "artist";
type GameFocus = "all" | "game" | "visual_novel" | "galgame";

const mediaOptions: [MediaType, string][] = [
  ["anime", "动画"], ["book", "漫画 / 轻小说 / 小说"], ["game", "游戏 / Galgame"],
  ["music", "音乐"], ["real", "三次元"],
];

const scenarioOptions: Record<MediaType, [Scenario, string][]> = {
  anime: [["general", "随便看看"], ["tonight", "今晚看"], ["season", "当季追番"], ["backlog", "清理想看"], ["cross_media", "跨媒体延伸"]],
  book: [["general", "随便看看"], ["tonight", "今晚读"], ["backlog", "清理想读"], ["cross_media", "跨媒体延伸"]],
  game: [["general", "随便看看"], ["tonight", "今晚开玩"], ["backlog", "清理想玩"], ["gal_intro", "Galgame 入门"], ["cross_media", "跨媒体延伸"]],
  music: [["general", "随便听听"], ["tonight", "今晚听"], ["backlog", "清理想听"], ["cross_media", "跨媒体延伸"]],
  real: [["general", "随便看看"], ["tonight", "今晚看"], ["backlog", "清理想看"]],
};

const mediaLabel: Record<string, string> = { anime: "动画", book: "书籍", game: "游戏", music: "音乐", real: "三次元" };
const scenarioLabel: Record<string, string> = { general: "随便看看", tonight: "今晚", season: "当季", backlog: "清理收藏", gal_intro: "Galgame 入门", cross_media: "跨媒体" };
const ACTIVE_RECOMMENDATION_RUN = "otomo:discover:active-run";

function currentSeason() {
  const now = new Date();
  const month = now.getMonth() + 1;
  return { year: now.getFullYear(), month: month >= 10 ? 10 : month >= 7 ? 7 : month >= 4 ? 4 : 1 };
}

export default function DiscoverPage() {
  const initial = useMemo(currentSeason, []);
  const { csrf, authenticated } = useExperience();
  const [year, setYear] = useState(initial.year);
  const [month, setMonth] = useState<1 | 4 | 7 | 10>(initial.month as 1 | 4 | 7 | 10);
  const [seasonMode, setSeasonMode] = useState<"guide" | "hot">("hot");
  const [season, setSeason] = useState<any>(null);
  const [media, setMedia] = useState<MediaType>("anime");
  const [scenario, setScenario] = useState<Scenario>("general");
  const [bookSubtype, setBookSubtype] = useState<BookSubtype>("auto");
  const [musicSubtype, setMusicSubtype] = useState<MusicSubtype>("auto");
  const [gameFocus, setGameFocus] = useState<GameFocus>("all");
  const [tags, setTags] = useState("");
  const [avoidTags, setAvoidTags] = useState("");
  const [maxEpisodes, setMaxEpisodes] = useState(0);
  const [niche, setNiche] = useState(false);
  const [explore, setExplore] = useState(false);
  const [recommendation, setRecommendation] = useState<any>(null);
  const [seasonBusy, setSeasonBusy] = useState(false);
  const [recommendBusy, setRecommendBusy] = useState(false);
  const [recommendElapsed, setRecommendElapsed] = useState(0);
  const [recommendRunId, setRecommendRunId] = useState("");
  const [recommendProgress, setRecommendProgress] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [share, setShare] = useState("");
  const [metrics, setMetrics] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);
  const recommendationSource = useRef<EventSource | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const savedMedia = params.get("media") as MediaType | null;
    const savedScenario = params.get("scenario") as Scenario | null;
    if (mediaOptions.some(([value]) => value === savedMedia)) setMedia(savedMedia as MediaType);
    if (["general", "tonight", "season", "backlog", "gal_intro", "cross_media"].includes(savedScenario || "")) setScenario(savedScenario as Scenario);
    setTags(params.get("tags") || "");
    setAvoidTags(params.get("avoid") || "");
    setMaxEpisodes(Number(params.get("max_episodes") || 0));
    setNiche(params.get("niche") === "true");
    setExplore(params.get("explore") === "true");
    const savedBookSubtype = params.get("book_subtype") || "auto";
    const savedMusicSubtype = params.get("music_subtype") || "auto";
    const savedGameFocus = params.get("game_focus") || "all";
    setBookSubtype((["auto", "comic", "light_novel", "novel"].includes(savedBookSubtype) ? savedBookSubtype : "auto") as BookSubtype);
    setMusicSubtype((["auto", "ost", "theme_song", "character_song", "artist"].includes(savedMusicSubtype) ? savedMusicSubtype : "auto") as MusicSubtype);
    setGameFocus((["all", "game", "visual_novel", "galgame"].includes(savedGameFocus) ? savedGameFocus : "all") as GameFocus);
    void loadSeason(initial.year, initial.month as 1 | 4 | 7 | 10, "hot");
  }, []);

  useEffect(() => {
    if (!scenarioOptions[media].some(([value]) => value === scenario)) setScenario("general");
    if (media !== "anime") setMaxEpisodes(0);
  }, [media, scenario]);

  useEffect(() => {
    if (authenticated) void loadInsights();
  }, [authenticated]);

  useEffect(() => {
    const saved = window.sessionStorage.getItem(ACTIVE_RECOMMENDATION_RUN);
    if (saved) {
      try {
        const { id } = JSON.parse(saved);
        if (id) void resumeRecommendationRun(String(id));
      } catch {
        window.sessionStorage.removeItem(ACTIVE_RECOMMENDATION_RUN);
      }
    }
    return () => recommendationSource.current?.close();
  }, []);

  useEffect(() => {
    if (!recommendBusy) return;
    const started = Date.now();
    setRecommendElapsed(0);
    const timer = window.setInterval(() => {
      setRecommendElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [recommendBusy]);

  async function loadSeason(y = year, m = month, mode = seasonMode) {
    setSeasonBusy(true); setError("");
    try {
      const payload = await productFetch(`/product/season-guide?year=${y}&month=${m}&mode=${mode}&limit=12`);
      setSeason(payload.data);
    } catch (e) { setError(String(e)); }
    finally { setSeasonBusy(false); }
  }

  async function recommend() {
    setRecommendBusy(true); setError("");
    setRecommendProgress([]);
    try {
      const focusTags = gameFocus === "visual_novel" ? ["视觉小说"] : gameFocus === "galgame" ? ["galgame"] : [];
      const focusAvoidTags = gameFocus === "game" ? ["galgame", "视觉小说"] : [];
      const payload = await productFetch("/recommendations/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
        body: JSON.stringify({
          subject_type: media, scenario, limit: 8, niche, explore,
          tags: [...tags.split(/[，,]/).map((x) => x.trim()).filter(Boolean), ...focusTags],
          avoid_tags: [...avoidTags.split(/[，,]/).map((x) => x.trim()).filter(Boolean), ...focusAvoidTags],
          max_episodes: maxEpisodes > 0 ? maxEpisodes : undefined,
          book_subtype: media === "book" ? bookSubtype : undefined,
          music_subtype: media === "music" ? musicSubtype : undefined,
        }),
      });
      const runId = String(payload.run?.id || "");
      if (!runId) throw new Error("推荐任务没有返回运行编号");
      setRecommendRunId(runId);
      window.sessionStorage.setItem(ACTIVE_RECOMMENDATION_RUN, JSON.stringify({ id: runId, sequence: 0 }));
      connectRecommendationRun(runId, 0);
    } catch (e) {
      setError(String(e));
      setRecommendBusy(false);
      setRecommendRunId("");
      window.sessionStorage.removeItem(ACTIVE_RECOMMENDATION_RUN);
    }
  }

  async function resumeRecommendationRun(runId: string) {
    try {
      await productFetch(`/recommendations/runs/${encodeURIComponent(runId)}`);
      setRecommendRunId(runId);
      setRecommendBusy(true);
      setRecommendProgress([]);
      connectRecommendationRun(runId, 0);
    } catch {
      window.sessionStorage.removeItem(ACTIVE_RECOMMENDATION_RUN);
      setRecommendBusy(false);
      setError("上次推荐因服务重启或超过保留时间而中断，请重新生成。之前已完成的推荐仍在历史记录里。");
    }
  }

  function connectRecommendationRun(runId: string, after: number) {
    recommendationSource.current?.close();
    const source = new EventSource(
      `${BACKEND}/recommendations/runs/${encodeURIComponent(runId)}/events?after=${after}`,
      { withCredentials: true },
    );
    recommendationSource.current = source;
    const rememberSequence = (event: MessageEvent) => {
      const sequence = Number(event.lastEventId || 0);
      window.sessionStorage.setItem(ACTIVE_RECOMMENDATION_RUN, JSON.stringify({ id: runId, sequence }));
    };
    source.addEventListener("progress", (raw) => {
      const event = raw as MessageEvent;
      rememberSequence(event);
      try {
        const progress = JSON.parse(event.data);
        setRecommendProgress((items) => {
          const next = items.filter((item) => item.current !== progress.current || item.summary !== progress.summary);
          return [...next, progress].slice(-6);
        });
      } catch { /* Ignore malformed progress without losing the run. */ }
    });
    source.addEventListener("final", (raw) => {
      const event = raw as MessageEvent;
      rememberSequence(event);
      try {
        const payload = JSON.parse(event.data);
        setRecommendation(payload.data);
        setRecommendBusy(false);
        setRecommendRunId("");
        window.sessionStorage.removeItem(ACTIVE_RECOMMENDATION_RUN);
        if (authenticated) void loadInsights();
      } catch {
        setError("推荐已完成，但结果解析失败，请刷新后从推荐历史中查看。");
        setRecommendBusy(false);
      }
      source.close();
    });
    source.addEventListener("cancelled", (raw) => {
      const event = raw as MessageEvent;
      rememberSequence(event);
      setRecommendBusy(false);
      setRecommendRunId("");
      setRecommendProgress([]);
      window.sessionStorage.removeItem(ACTIVE_RECOMMENDATION_RUN);
      source.close();
    });
    source.addEventListener("error", (raw) => {
      if (!(raw instanceof MessageEvent)) return;
      rememberSequence(raw);
      try { setError(JSON.parse(raw.data).message || "推荐任务失败"); }
      catch { setError("推荐任务失败"); }
      setRecommendBusy(false);
      setRecommendRunId("");
      window.sessionStorage.removeItem(ACTIVE_RECOMMENDATION_RUN);
      source.close();
    });
  }

  async function cancelRecommendation() {
    if (!recommendRunId) return;
    try {
      await productFetch(`/recommendations/runs/${encodeURIComponent(recommendRunId)}/cancel`, {
        method: "POST",
        headers: csrf ? { "x-otomo-csrf": csrf } : {},
      });
    } catch (e) {
      setError(String(e));
    }
  }

  async function feedback(payload: Record<string, any>) {
    try {
      await productFetch("/feedback/recommendation", {
        method: "POST", headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
        body: JSON.stringify(payload),
      });
      if (authenticated && !["impression", "open"].includes(String(payload.event))) void loadInsights();
      return true;
    } catch { return false; }
  }

  async function nextBatch(setId: string) {
    try {
      const payload = await productFetch("/recommendations/next", {
        method: "POST", headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
        body: JSON.stringify({ recommendation_set_id: setId }),
      });
      setRecommendation(payload.data);
      return payload.data;
    } catch (e) { setError(String(e)); return null; }
  }

  async function shareSnapshot(request: Record<string, any>) {
    try {
      const payload = await createShareSnapshot(request, csrf);
      setShare(payload.url || payload.snapshot?.url || "");
    } catch (e) { setError(String(e)); }
  }

  async function loadInsights() {
    try {
      const [metricPayload, historyPayload] = await Promise.all([
        productFetch("/recommendations/metrics?days=30"),
        productFetch("/recommendations/history?limit=8"),
      ]);
      setMetrics(metricPayload.data);
      setHistory(Array.isArray(historyPayload.data) ? historyPayload.data : []);
    } catch {
      // Insights are supplementary; recommendation itself remains available.
    }
  }

  async function saveView() {
    const name = window.prompt("给这个发现视图起个名字", tags ? `${tags} · ${media}` : `${media} · ${scenario}`);
    if (!name?.trim()) return;
    try {
      await productFetch("/workspace/views", {
        method: "POST", headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
        body: JSON.stringify({ name: name.trim(), surface: "discover", params: { media, scenario, tags, avoidTags, maxEpisodes, niche, explore, bookSubtype, musicSubtype, gameFocus } }),
      });
      setShare("saved-view");
    } catch (e) { setError(String(e)); }
  }

  return (
    <main className="page-frame discover-page">
      <PageHeader eyebrow="作品发现" title="发现下一部作品" description="季番追更与跨媒介推荐共用你的口味画像，但把当下心境留给这一轮决定。" />
      {error ? <div className="surface-error">{error}</div> : null}
      {share ? <div className="inline-notice">{share === "saved-view" ? <>发现条件已保存到 <a href="/workspace">我的工作区</a>。</> : <>导视分享页已生成：<a href={share} target="_blank" rel="noreferrer">打开公开快照</a></>}</div> : null}

      <section className="workspace-section">
        <div className="section-heading"><div><span className="section-kicker">先从这里看</span><h2>最适合你的候选</h2></div><div className="page-actions">{authenticated ? <button className="button-secondary" onClick={() => void saveView()}><BookmarkPlus size={16} />保存条件</button> : null}<SlidersHorizontal size={19} /></div></div>
        <div className="recommend-controls">
          <div className="media-switch">
            {mediaOptions.map(([value, label]) => <button key={value} className={media === value ? "active" : ""} onClick={() => setMedia(value)}>{label}</button>)}
          </div>
          <div className="filter-grid">
            <label><span>场景</span><select value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)}>{scenarioOptions[media].map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            {media === "book" ? <label><span>书籍类型</span><select value={bookSubtype} onChange={(e) => setBookSubtype(e.target.value as BookSubtype)}><option value="auto">自动判断</option><option value="comic">漫画</option><option value="light_novel">轻小说</option><option value="novel">小说</option></select></label> : null}
            {media === "music" ? <label><span>音乐类型</span><select value={musicSubtype} onChange={(e) => setMusicSubtype(e.target.value as MusicSubtype)}><option value="auto">自动判断</option><option value="ost">OST / 原声</option><option value="theme_song">OP / ED / 主题歌</option><option value="character_song">角色歌</option><option value="artist">艺人 / 专辑</option></select></label> : null}
            {media === "game" ? <label><span>游戏方向</span><select value={gameFocus} onChange={(e) => { const value = e.target.value as GameFocus; setGameFocus(value); if (value === "galgame") setScenario("gal_intro"); }}><option value="all">不限</option><option value="game">普通游戏</option><option value="visual_novel">视觉小说</option><option value="galgame">Galgame</option></select></label> : null}
            <label className="wide"><span>这轮想看什么</span><input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="治愈，百合，短篇…" /></label>
            <label className="wide"><span>这轮明确避开什么</span><input value={avoidTags} onChange={(e) => setAvoidTags(e.target.value)} placeholder="后宫，致郁，党争…" /></label>
            {media === "anime" ? <label><span>最多多少集</span><select value={maxEpisodes} onChange={(e) => setMaxEpisodes(Number(e.target.value))}><option value={0}>不限篇幅</option><option value={6}>6 集以内</option><option value={12}>12 集以内</option><option value={24}>24 集以内</option></select></label> : null}
            <label className="toggle-line"><input type="checkbox" checked={niche} onChange={(e) => setNiche(e.target.checked)} /><span>冷门挖宝</span></label>
            <label className="toggle-line"><input type="checkbox" checked={explore} onChange={(e) => setExplore(e.target.checked)} /><span>拓展口味</span></label>
            <button className="button-primary icon-label" onClick={() => void recommend()} disabled={recommendBusy}><Sparkles size={17} />{recommendBusy ? `精筛中 ${recommendElapsed}s` : "生成推荐"}</button>
            {recommendBusy && recommendRunId ? <button className="button-secondary" onClick={() => void cancelRecommendation()}>停止本轮</button> : null}
          </div>
          {!authenticated ? <div className="inline-notice">未连接 Bangumi 时会按你这轮填写的偏好做匿名推荐；<a href={`${BACKEND}/auth/bangumi/start`}>连接 Bangumi</a> 后还会结合收藏和评分。</div> : null}
        </div>
        {recommendBusy ? (
          <div className="surface-loading recommend-loading" role="status">
            <strong>{recommendProgress.at(-1)?.summary || "正在认真筛选候选"} · {recommendElapsed} 秒</strong>
            {recommendProgress.at(-1)?.total ? <div className="recommend-progress-track"><span style={{ width: `${Math.max(4, Math.min(100, Number(recommendProgress.at(-1)?.current || 0) / Number(recommendProgress.at(-1)?.total || 1) * 100))}%` }} /></div> : null}
            <span>任务会在后台继续；离开页面再回来时会自动接上进度。核心口碑核验仍会完整执行。</span>
            {recommendProgress.length ? <div className="recommend-progress-steps">{recommendProgress.slice(-4).map((item, index) => <span key={`${item.current}-${item.summary}-${index}`}>{item.current && item.total ? `${item.current}/${item.total} · ` : ""}{item.summary}</span>)}</div> : null}
          </div>
        ) : null}
        {recommendation ? <RecommendPanel data={recommendation} onFeedback={feedback} onNextBatch={nextBatch} /> : (
          <div className="feature-empty"><Compass size={24} /><strong>告诉我现在想看什么</strong><span>会先给 3 部重点候选，并分别解释适合点、风险和口碑依据。</span></div>
        )}
        {authenticated && metrics ? (
          <div className="recommend-insights">
            <details open={metrics.personalization?.decision_samples > 0}>
              <summary><BarChart3 size={15} />Otomo 从你的反馈里学到了什么</summary>
              <div className="insight-summary-grid">
                <div><strong>{metrics.personalization?.decision_samples || 0}</strong><span>次明确选择</span></div>
                <div><strong>{Math.round((metrics.wishlist_rate || 0) * 100)}%</strong><span>加入想看</span></div>
                <div><strong>{Math.round((metrics.start_rate || 0) * 100)}%</strong><span>开始观看</span></div>
              </div>
              <p>{metrics.personalization?.note}</p>
              <div className="learned-preferences">
                <div><strong>倾向多来</strong><span>{(metrics.personalization?.positive_tags || []).join("、") || "还没有足够反馈"}</span></div>
                <div><strong>倾向少来</strong><span>{(metrics.personalization?.negative_tags || []).join("、") || "还没有足够反馈"}</span></div>
              </div>
              {(metrics.segments || []).length ? <div className="segment-metrics">{metrics.segments.slice(0, 5).map((segment: any) => <span key={`${segment.subject_type}-${segment.scenario}`}>{mediaLabel[segment.subject_type] || segment.subject_type} · {scenarioLabel[segment.scenario] || segment.scenario}：看过 {segment.impressions} 张，采纳 {Math.round((segment.acceptance_rate || 0) * 100)}%</span>)}</div> : null}
              {(metrics.positions || []).length >= 2 ? <small>位置偏差观察：第 1 张采纳 {Math.round((metrics.positions[0]?.acceptance_rate || 0) * 100)}%，后续卡片也会单独计量，避免只因为排在前面就被误判为更准。</small> : null}
            </details>
            {history.length ? <details><summary><History size={15} />最近的推荐记录</summary><div className="recommend-history-list">{history.map((batch) => <div className="recommend-history-item" key={batch.id}><div><strong>{mediaLabel[batch.subject_type] || batch.subject_type} · {scenarioLabel[batch.scenario] || batch.scenario}</strong><time>{new Date(batch.created_at).toLocaleString("zh-CN")}</time></div><span>{(batch.items || []).slice(0, 4).map((item: any) => `${item.name}${item.latest_event && item.latest_event !== "undo" ? `（${item.latest_event === "more" ? "多来" : item.latest_event === "less" || item.latest_event === "dismiss" ? "少来" : item.latest_event === "wishlist" ? "想看" : item.latest_event === "started" ? "已开始" : item.latest_event === "watched" ? "看过" : item.latest_event}）` : ""}`).join("、")}</span></div>)}</div></details> : null}
          </div>
        ) : null}
      </section>

      <section className="workspace-section">
        <div className="section-heading">
          <div><span className="section-kicker">本季新番</span><h2>再浏览完整季番清单</h2></div>
          <div className="filter-row">
            <input aria-label="年份" className="compact-input" type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} />
            <select aria-label="季度" value={month} onChange={(e) => setMonth(Number(e.target.value) as 1 | 4 | 7 | 10)}>
              <option value={1}>1 月番</option><option value={4}>4 月番</option><option value={7}>7 月番</option><option value={10}>10 月番</option>
            </select>
            <div className="segmented"><button className={seasonMode === "hot" ? "active" : ""} onClick={() => setSeasonMode("hot")}>当前热播</button><button className={seasonMode === "guide" ? "active" : ""} onClick={() => setSeasonMode("guide")}>按我口味</button></div>
            <button className="button-secondary icon-label" disabled={seasonBusy} onClick={() => void loadSeason()}><RefreshCw size={16} />更新</button>
          </div>
        </div>
        {seasonBusy && !season ? <div className="surface-loading">正在整理本季条目、口碑与播出资料…</div> : null}
        {season ? <SeasonGuidePanel data={season} onShareSnapshot={authenticated ? (request) => void shareSnapshot(request) : undefined} /> : null}
      </section>
    </main>
  );
}
