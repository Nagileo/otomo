"use client";

import { BookmarkPlus, Compass, RefreshCw, SlidersHorizontal, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { PageHeader } from "../../components/page-header";
import { authSession, BACKEND, createShareSnapshot, productFetch } from "../../lib/api";
import { SeasonGuidePanel } from "../panels/media";
import { RecommendPanel } from "../panels/recommend";

type MediaType = "anime" | "book" | "game" | "music" | "real";
type Scenario = "general" | "tonight" | "season" | "backlog" | "gal_intro" | "cross_media";

const mediaOptions: [MediaType, string][] = [
  ["anime", "动画"], ["book", "漫画 / 轻小说"], ["game", "游戏 / Galgame"],
  ["music", "音乐"], ["real", "三次元"],
];

function currentSeason() {
  const now = new Date();
  const month = now.getMonth() + 1;
  return { year: now.getFullYear(), month: month >= 10 ? 10 : month >= 7 ? 7 : month >= 4 ? 4 : 1 };
}

export default function DiscoverPage() {
  const initial = useMemo(currentSeason, []);
  const [csrf, setCsrf] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [year, setYear] = useState(initial.year);
  const [month, setMonth] = useState<1 | 4 | 7 | 10>(initial.month as 1 | 4 | 7 | 10);
  const [seasonMode, setSeasonMode] = useState<"guide" | "hot">("hot");
  const [season, setSeason] = useState<any>(null);
  const [media, setMedia] = useState<MediaType>("anime");
  const [scenario, setScenario] = useState<Scenario>("general");
  const [tags, setTags] = useState("");
  const [niche, setNiche] = useState(false);
  const [explore, setExplore] = useState(false);
  const [recommendation, setRecommendation] = useState<any>(null);
  const [busy, setBusy] = useState<"season" | "recommend" | "">("");
  const [error, setError] = useState("");
  const [share, setShare] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const savedMedia = params.get("media") as MediaType | null;
    const savedScenario = params.get("scenario") as Scenario | null;
    if (mediaOptions.some(([value]) => value === savedMedia)) setMedia(savedMedia as MediaType);
    if (["general", "tonight", "season", "backlog", "gal_intro", "cross_media"].includes(savedScenario || "")) setScenario(savedScenario as Scenario);
    setTags(params.get("tags") || "");
    setNiche(params.get("niche") === "true");
    setExplore(params.get("explore") === "true");
    authSession().then((auth) => {
      setCsrf(auth.csrf_token || "");
      setAuthenticated(Boolean(auth.authenticated));
    }).catch(() => undefined);
    void loadSeason(initial.year, initial.month as 1 | 4 | 7 | 10, "hot");
  }, []);

  async function loadSeason(y = year, m = month, mode = seasonMode) {
    setBusy("season"); setError("");
    try {
      const payload = await productFetch(`/product/season-guide?year=${y}&month=${m}&mode=${mode}&limit=12`);
      setSeason(payload.data);
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
  }

  async function recommend() {
    setBusy("recommend"); setError("");
    try {
      const payload = await productFetch("/product/recommendations", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
        body: JSON.stringify({
          subject_type: media, scenario, limit: 8, niche, explore,
          tags: tags.split(/[，,]/).map((x) => x.trim()).filter(Boolean),
          book_subtype: media === "book" ? "auto" : undefined,
          music_subtype: media === "music" ? "auto" : undefined,
        }),
      });
      setRecommendation(payload.data);
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
  }

  async function feedback(payload: Record<string, any>) {
    try {
      await productFetch("/feedback/recommendation", {
        method: "POST", headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
        body: JSON.stringify(payload),
      });
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

  async function saveView() {
    const name = window.prompt("给这个发现视图起个名字", tags ? `${tags} · ${media}` : `${media} · ${scenario}`);
    if (!name?.trim()) return;
    try {
      await productFetch("/workspace/views", {
        method: "POST", headers: { "Content-Type": "application/json", ...(csrf ? { "x-otomo-csrf": csrf } : {}) },
        body: JSON.stringify({ name: name.trim(), surface: "discover", params: { media, scenario, tags, niche, explore } }),
      });
      setShare("saved-view");
    } catch (e) { setError(String(e)); }
  }

  return (
    <main className="page-frame discover-page">
      <PageHeader eyebrow="Discover" title="发现下一部作品" description="季番追更与跨媒介推荐共用你的口味画像，但把当下心境留给这一轮决定。" />
      {error ? <div className="surface-error">{error}</div> : null}
      {share ? <div className="inline-notice">{share === "saved-view" ? <>发现条件已保存到 <a href="/workspace">我的工作区</a>。</> : <>导视分享页已生成：<a href={share} target="_blank" rel="noreferrer">打开公开快照</a></>}</div> : null}

      <section className="workspace-section">
        <div className="section-heading">
          <div><span className="section-kicker">SEASON</span><h2>本季追什么</h2></div>
          <div className="filter-row">
            <input aria-label="年份" className="compact-input" type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} />
            <select aria-label="季度" value={month} onChange={(e) => setMonth(Number(e.target.value) as 1 | 4 | 7 | 10)}>
              <option value={1}>1 月番</option><option value={4}>4 月番</option><option value={7}>7 月番</option><option value={10}>10 月番</option>
            </select>
            <div className="segmented"><button className={seasonMode === "hot" ? "active" : ""} onClick={() => setSeasonMode("hot")}>当前热播</button><button className={seasonMode === "guide" ? "active" : ""} onClick={() => setSeasonMode("guide")}>按我口味</button></div>
            <button className="button-secondary icon-label" disabled={busy === "season"} onClick={() => void loadSeason()}><RefreshCw size={16} />更新</button>
          </div>
        </div>
        {busy === "season" && !season ? <div className="surface-loading">正在融合 Bangumi、yuc 与导视源…</div> : null}
        {season ? <SeasonGuidePanel data={season} onShareSnapshot={authenticated ? (request) => void shareSnapshot(request) : undefined} /> : null}
      </section>

      <section className="workspace-section">
        <div className="section-heading"><div><span className="section-kicker">FOR YOU</span><h2>个性化推荐</h2></div><div className="page-actions">{authenticated ? <button className="button-secondary" onClick={() => void saveView()}><BookmarkPlus size={16} />保存视图</button> : null}<SlidersHorizontal size={19} /></div></div>
        <div className="recommend-controls">
          <div className="media-switch">
            {mediaOptions.map(([value, label]) => <button key={value} className={media === value ? "active" : ""} onClick={() => setMedia(value)}>{label}</button>)}
          </div>
          <div className="filter-grid">
            <label><span>场景</span><select value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)}><option value="general">随便看看</option><option value="tonight">今晚看</option><option value="season">当季追番</option><option value="backlog">清理想看</option><option value="gal_intro">Galgame 入门</option><option value="cross_media">跨媒体延伸</option></select></label>
            <label className="wide"><span>这轮想看什么</span><input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="治愈，百合，短篇…" /></label>
            <label className="toggle-line"><input type="checkbox" checked={niche} onChange={(e) => setNiche(e.target.checked)} /><span>冷门挖宝</span></label>
            <label className="toggle-line"><input type="checkbox" checked={explore} onChange={(e) => setExplore(e.target.checked)} /><span>拓展口味</span></label>
            <button className="button-primary icon-label" onClick={() => void recommend()} disabled={!authenticated || busy === "recommend"}><Sparkles size={17} />{busy === "recommend" ? "正在召回与重排" : "生成推荐"}</button>
          </div>
          {!authenticated ? <div className="inline-notice">个性化推荐需要先 <a href={`${BACKEND}/auth/bangumi/start`}>连接 Bangumi</a>；季番热播清单仍可公开查看。</div> : null}
        </div>
        {recommendation ? <RecommendPanel data={recommendation} onFeedback={feedback} onNextBatch={nextBatch} /> : (
          <div className="feature-empty"><Compass size={24} /><strong>选择媒介和当下心境</strong><span>结果会排除已看作品，并解释召回、匹配点与风险。</span></div>
        )}
      </section>
    </main>
  );
}
