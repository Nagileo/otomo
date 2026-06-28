import type { ReactNode } from "react";

type AnyRecord = Record<string, any>;
type EvidenceMap = Record<string, AnyRecord[]>;
type SpoilerState = {
  mode?: string;
  progress_episode?: number;
  pending_followup?: boolean;
  followup_question?: string;
};
type MemoryState = {
  username?: string;
  likes?: AnyRecord[];
  dislikes?: AnyRecord[];
  spoiler_default?: string;
  progress?: Record<string, AnyRecord>;
  recent_feedback?: AnyRecord[];
  profile_snapshot?: Record<string, AnyRecord>;
  aspect_profiles?: Record<string, AnyRecord>;
  updated_at?: string;
};

function list<T = AnyRecord>(value: any): T[] {
  return Array.isArray(value) ? value : [];
}

function text(value: any, fallback = "未知") {
  const s = String(value ?? "").trim();
  return s || fallback;
}

function fmtScore(score: any, scale?: any) {
  if (score === null || score === undefined || score === "") return "暂无";
  return scale ? `${score}/${scale}` : String(score);
}

function clsBySignal(signal: any) {
  const s = String(signal ?? "").toLowerCase();
  if (["strong", "positive", "high", "used"].includes(s)) return "good";
  if (["mixed", "maybe", "medium", "low_data"].includes(s)) return "warn";
  if (["weak", "wait", "hidden", "unavailable"].includes(s)) return "bad";
  return "dim";
}

function pct(value: any) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0.00";
  return n.toFixed(2);
}

function sourceTone(source: any) {
  const s = String(source ?? "");
  if (s === "explicit_user") return "good";
  if (s === "derived_from_feedback") return "warn";
  if (s === "bangumi_profile") return "dim";
  return "dim";
}

function Badge({ children, tone = "dim" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="evidence-panel">
      <div className="evidence-head">
        <div>
          <div className="evidence-title">{title}</div>
          {subtitle && <div className="evidence-sub">{subtitle}</div>}
        </div>
      </div>
      {children}
    </section>
  );
}

function EmptyHint({ text }: { text: string }) {
  return <div className="empty-hint">{text}</div>;
}

function ReviewEvidencePanel({ data }: { data: AnyRecord }) {
  const ratings = list(data.ratings);
  const aspects = list(data.aspect_summary);
  const matrix = list(data.source_matrix);
  const groups = list(data.source_groups);
  return (
    <Panel
      title={`评价证据 · ${text(data.title)}`}
      subtitle={`${text(data.subject_type)} · 置信度 ${text(data.confidence, "low")} · 剧透 ${text(data.spoiler_level, "none")}`}
    >
      <div className="evidence-row">
        <Badge tone={clsBySignal(data.confidence)}>confidence: {text(data.confidence, "low")}</Badge>
        <Badge tone={data.spoiler_level === "none" ? "good" : "warn"}>spoiler: {text(data.spoiler_level, "none")}</Badge>
      </div>
      {data.consensus && <p className="evidence-copy">{data.consensus}</p>}

      {groups.length > 0 && (
        <>
          <div className="section-title">三圈层对比</div>
          <div className="rating-grid">
            {groups.map((g, i) => (
              <div className="rating-card" key={`${g.group}-${i}`}>
                <div className="rating-source">{text(g.group)}</div>
                <div className="card-meta">{text(g.role, "")}</div>
                <p className="card-note">{text(g.consensus, "暂无证据")}</p>
                <Badge tone={clsBySignal(g.confidence)}>confidence: {text(g.confidence, "low")}</Badge>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="section-title">评分 / 圈层</div>
      {ratings.length ? (
        <div className="rating-grid">
          {ratings.map((r, i) => (
            <a className="rating-card" href={r.url || "#"} target="_blank" rel="noreferrer" key={`${r.source}-${i}`}>
              <div className="rating-source">{text(r.source)}</div>
              <div className="rating-score">{fmtScore(r.score, r.scale)}</div>
              <div className="rating-meta">
                {r.count ? `${r.count} 样本` : "样本未知"}
                {r.rank ? ` · rank ${r.rank}` : ""}
              </div>
              <Badge tone={clsBySignal(r.signal)}>{text(r.signal)}</Badge>
              {r.note && <div className="card-note">{r.note}</div>}
            </a>
          ))}
        </div>
      ) : (
        <EmptyHint text="没有可用评分证据" />
      )}

      <div className="section-title">方面口碑</div>
      {aspects.length ? (
        <div className="aspect-list">
          {aspects.map((a, i) => {
            const total = Math.max(Number(a.total ?? 0), 1);
            const pos = Math.round((Number(a.positive ?? 0) / total) * 100);
            const neg = Math.round((Number(a.negative ?? 0) / total) * 100);
            return (
              <div className="aspect-row" key={`${a.aspect}-${i}`}>
                <div className="aspect-top">
                  <span>{text(a.label || a.aspect)}</span>
                  <Badge tone={clsBySignal(a.dominant_sentiment)}>{text(a.dominant_sentiment)}</Badge>
                </div>
                <div className="aspect-bars">
                  <span className="bar pos" style={{ width: `${pos}%` }} />
                  <span className="bar neg" style={{ width: `${neg}%` }} />
                </div>
                <div className="aspect-meta">
                  +{a.positive ?? 0} / -{a.negative ?? 0} / mixed {a.mixed ?? 0} · {text(a.confidence, "low")}
                  {a.spoiler_risk ? ` · spoiler ${a.spoiler_risk}` : ""}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyHint text="暂无方面级口碑样本，可能因无剧透模式隐藏了短评原文" />
      )}

      {matrix.length > 0 && (
        <>
          <div className="section-title">来源矩阵</div>
          <div className="source-matrix">
            {matrix.map((m, i) => (
              <div className="matrix-item" key={`${m.source}-${i}`}>
                <Badge tone={clsBySignal(m.status)}>{text(m.status)}</Badge>
                <span>{text(m.source)} · {text(m.role, "")}</span>
                {m.note && <small>{m.note}</small>}
              </div>
            ))}
          </div>
        </>
      )}

      {list<string>(data.source_routing_notes).length > 0 && (
        <div className="compact-list">
          {list<string>(data.source_routing_notes).map((n, i) => <span key={i}>{n}</span>)}
        </div>
      )}

      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((c, i) => <span key={i}>{c}</span>)}</div>
      )}
    </Panel>
  );
}

function TasteAffinityPanel({ data }: { data: AnyRecord }) {
  const affinity = data.affinity || {};
  const metrics = [
    ["评分同步", affinity.rating_similarity],
    ["收藏重叠", affinity.collection_similarity],
    ["你的空间", affinity.user_space_similarity],
    ["对方空间", affinity.peer_space_similarity],
    ["极限空间", affinity.extreme_similarity],
    ["严格度对齐", affinity.severity_alignment],
  ];
  const groups = [
    ["共同高分", "liked_together"],
    ["共同低分", "disliked_together"],
    ["最大分歧", "biggest_disagreements"],
  ];
  return (
    <Panel
      title={`同步率 · ${text(data.username)} × ${text(data.peer_username)}`}
      subtitle={`${text(data.subject_type)} · ${affinity.common_rated ?? 0} 个共同评分 · ${affinity.common_collections ?? 0} 个共同收藏`}
    >
      <div className="metric-grid">
        {metrics.map(([label, value]) => (
          <div className="metric-card" key={String(label)}>
            <div className="metric-label">{label}</div>
            <div className="metric-value">{pct(value)}</div>
          </div>
        ))}
      </div>
      <div className="evidence-row">
        <Badge tone={clsBySignal(affinity.confidence)}>confidence: {text(affinity.confidence, "low")}</Badge>
        <Badge tone="dim">peer weight: {pct(affinity.peer_weight)}</Badge>
      </div>
      {affinity.explanation && <p className="evidence-copy">{affinity.explanation}</p>}
      {list<string>(affinity.confidence_reasons).length > 0 && (
        <div className="compact-list">
          {list<string>(affinity.confidence_reasons).map((r, i) => <span key={i}>{r}</span>)}
        </div>
      )}
      <div className="taste-groups">
        {groups.map(([label, key]) => (
          <div className="taste-group" key={key}>
            <div className="section-title">{label}</div>
            {list(affinity[key]).length ? (
              list(affinity[key]).map((item, i) => (
                <div className="shared-item" key={`${item.id}-${i}`}>
                  {item.image ? <img src={item.image} alt="" /> : <div className="shared-noimg" />}
                  <div>
                    <div className="shared-name">{text(item.name)}</div>
                    <div className="shared-meta">
                      你 {item.user_rate ?? "-"} · 对方 {item.peer_rate ?? "-"} · Δ {item.delta ?? 0}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <EmptyHint text="暂无样本" />
            )}
          </div>
        ))}
      </div>
    </Panel>
  );
}

function SeasonGuidePanel({ data }: { data: AnyRecord }) {
  const items = list(data.items);
  return (
    <Panel
      title={`季番导视 · ${text(data.season)}`}
      subtitle={`${data.personalized ? "已按用户画像分诊" : "非个性化导视"} · ${items.length} 部`}
    >
      <div className="evidence-row">
        {list<string>(data.profile_tags).slice(0, 8).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
        {list<string>(data.focus_tags).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
      </div>
      <div className="season-grid">
        {items.map((item, i) => (
          <a className="season-card" href={`https://bgm.tv/subject/${item.subject_id}`} target="_blank" rel="noreferrer" key={`${item.subject_id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="season-noimg" />}
            <div className="season-main">
              <div className="card-title">{text(item.title)}</div>
              <div className="card-meta">
                {item.bangumi_score ? `Bangumi ${item.bangumi_score}` : "暂无评分"}
                {item.broadcast ? ` · ${item.broadcast}` : ""}
              </div>
              <div className="evidence-row tight">
                <Badge tone={clsBySignal(item.fit)}>{text(item.fit)}</Badge>
                <Badge tone={item.match_confidence >= 0.8 ? "good" : item.match_confidence > 0 ? "warn" : "dim"}>
                  match {pct(item.match_confidence)}
                </Badge>
              </div>
              <p className="card-note">{item.reason}</p>
              {item.studio && <div className="card-meta">制作：{item.studio}</div>}
              {list<string>(item.evidence).length > 0 && (
                <div className="compact-list inline">
                  {list<string>(item.evidence).slice(0, 3).map((e, idx) => <span key={idx}>{e}</span>)}
                </div>
              )}
              <div className="link-row">
                {item.official_url && <span>官网</span>}
                {item.pv_url && <span>PV</span>}
                {list(item.guide_videos).slice(0, 2).map((v) => <span key={v.url}>{text(v.up_name)}</span>)}
              </div>
            </div>
          </a>
        ))}
      </div>
      {list(data.guide_comment_digests).length > 0 && (
        <>
          <div className="section-title">导视评论摘要</div>
          <div className="digest-list">
            {list(data.guide_comment_digests).map((d, i) => (
              <a className="digest-card" href={d.url} target="_blank" rel="noreferrer" key={`${d.aid}-${i}`}>
                <div className="digest-title">{text(d.author)} · {text(d.video_title)}</div>
                <div className="compact-list">
                  {list<string>(d.opinion_summary).map((x, idx) => <span key={idx}>{x}</span>)}
                </div>
              </a>
            ))}
          </div>
        </>
      )}
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function AspectProfilePanel({ data }: { data: AnyRecord }) {
  const profile = data.profile || {};
  const likes = list(profile.likes);
  const dislikes = list(profile.dislikes);
  return (
    <Panel
      title={`Aspect 情感画像 · ${text(data.subject_type || profile.subject_type)}`}
      subtitle={`${text(data.extraction_source || profile.extraction_source, "none")} · ${data.samples_seen ?? profile.sample_count ?? 0} 条私评样本`}
    >
      <div className="memory-grid">
        <div>
          <div className="section-title">好球区</div>
          {likes.length ? (
            <div className="compact-list">
              {likes.map((item, i) => (
                <span key={`${item.aspect}-${i}`}>
                  {text(item.label || item.aspect)} · weight {pct(item.weight)} · {item.evidence_count ?? 0} 证据
                  {item.sample ? <small> · {item.sample}</small> : null}
                </span>
              ))}
            </div>
          ) : <EmptyHint text="暂无好球区" />}
        </div>
        <div>
          <div className="section-title">雷区</div>
          {dislikes.length ? (
            <div className="compact-list">
              {dislikes.map((item, i) => (
                <span key={`${item.aspect}-${i}`}>
                  {text(item.label || item.aspect)} · weight {pct(item.weight)} · {item.evidence_count ?? 0} 证据
                  {item.sample ? <small> · {item.sample}</small> : null}
                </span>
              ))}
            </div>
          ) : <EmptyHint text="暂无雷区" />}
        </div>
      </div>
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((c, i) => <span key={i}>{c}</span>)}</div>
      )}
    </Panel>
  );
}

function RecommendPanel({ data, onCritique }: { data: AnyRecord; onCritique?: (q: string) => void }) {
  const items = list(data.items);
  const aspectProfile = data.aspect_profile_summary || {};
  const mediaStrategy = data.media_strategy || {};
  return (
    <Panel
      title={`推荐证据 · ${text(data.subject_type)}`}
      subtitle={`mode: ${text(data.mode, "normal")} · ${items.length} 个候选`}
    >
      <div className="evidence-row">
        {list<string>(data.based_on_tags).slice(0, 10).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
        {list<string>(data.applied_constraints).map((x) => <Badge key={x} tone="warn">{x}</Badge>)}
        {mediaStrategy.book_subtype && mediaStrategy.book_subtype !== "auto" && (
          <Badge tone="good">book: {text(mediaStrategy.book_subtype)}</Badge>
        )}
        {mediaStrategy.music_subtype && mediaStrategy.music_subtype !== "auto" && (
          <Badge tone="good">music: {text(mediaStrategy.music_subtype)}</Badge>
        )}
      </div>
      {mediaStrategy.policy && <p className="evidence-copy">{text(mediaStrategy.policy)}</p>}
      {(list(aspectProfile.likes).length > 0 || list(aspectProfile.dislikes).length > 0) && (
        <div className="evidence-row">
          {list(aspectProfile.likes).slice(0, 4).map((x) => <Badge key={`like-${x.aspect}`} tone="good">好球 {text(x.label || x.aspect)}</Badge>)}
          {list(aspectProfile.dislikes).slice(0, 4).map((x) => <Badge key={`dislike-${x.aspect}`} tone="warn">雷区 {text(x.label || x.aspect)}</Badge>)}
        </div>
      )}
      <div className="rec-grid">
        {items.map((item, i) => (
          <a className="rec-card" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
            <div className="rec-body">
              <div className="card-title">{text(item.name)}</div>
              <div className="card-meta">
                Otomo {item.score ?? "-"} · Bangumi {item.bangumi_score ?? "暂无"}
                {item.rank ? ` · rank ${item.rank}` : ""}
              </div>
              {item.review_consensus && <p className="card-note">{item.review_consensus}</p>}
              <div className="evidence-row tight">
                {item.media_subtype && <Badge tone="dim">{text(item.media_subtype)}</Badge>}
                {list<string>(item.explicit_tag_matches).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
                {list<string>(item.quality_badges).map((tag) => <Badge key={tag} tone="warn">{tag}</Badge>)}
                {list<string>(item.aspect_matches).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
                {list<string>(item.aspect_warnings).map((tag) => <Badge key={tag} tone="warn">{tag}</Badge>)}
              </div>
              {list<string>(item.media_notes).length > 0 && (
                <div className="compact-list inline">
                  {list<string>(item.media_notes).slice(0, 3).map((r, idx) => <span key={idx}>{r}</span>)}
                </div>
              )}
              <div className="compact-list inline">
                {list<string>(item.reasons).slice(0, 4).map((r, idx) => <span key={idx}>{r}</span>)}
              </div>
              {list(item.external_mappings).length > 0 && (
                <div className="mapping-list">
                  {list(item.external_mappings).map((m, idx) => (
                    <span key={idx}>
                      {text(m.source)}《{text(m.external_title)}》→ BGM {m.bangumi_id} · {pct(m.mapping_confidence)}
                    </span>
                  ))}
                </div>
              )}
              {list<string>(item.source_routes).length > 0 && (
                <div className="compact-list">
                  {list<string>(item.source_routes).slice(0, 3).map((r, idx) => <span key={idx}>{r}</span>)}
                </div>
              )}
            </div>
          </a>
        ))}
      </div>
      {onCritique && list<string>(data.critique_chips).length > 0 && (
        <div className="followups">
          {list<string>(data.critique_chips).map((q, i) => (
            <button className="chip" key={i} onClick={() => onCritique(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
      {onCritique && list<string>(data.cold_start_questions).length > 0 && (
        <div className="followups">
          {list<string>(data.cold_start_questions).map((q, i) => (
            <button className="chip" key={i} onClick={() => onCritique(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
      {list<string>(data.mapping_warnings).length > 0 && (
        <div className="caveats">
          <div className="section-title">映射告警（未安全对齐，已跳过）</div>
          {list<string>(data.mapping_warnings).map((w, i) => <span key={i}>⚠ {w}</span>)}
        </div>
      )}
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function WatchCopilotPanel({ data }: { data: AnyRecord }) {
  const queue = list(data.queue);
  const groups = [
    ["继续追", "continue_watching"],
    ["想看开坑", "start_from_wishlist"],
    ["搁置盘活", "revive_on_hold"],
  ];
  return (
    <Panel title={`追番副驾 · ${text(data.username)}`} subtitle={`${queue.length} 个本周候选`}>
      <div className="evidence-row">
        {list<string>(data.profile_tags).slice(0, 10).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
      </div>
      <div className="rec-grid">
        {queue.map((item, i) => (
          <a className="rec-card" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
            <div className="rec-body">
              <div className="card-title">{text(item.name)}</div>
              <div className="card-meta">
                {text(item.status)} · Otomo {item.score ?? "-"} · BGM {item.bangumi_score ?? "暂无"}
                {item.eps ? ` · ${item.ep_status ?? 0}/${item.eps}` : ""}
              </div>
              <Badge tone={item.status === "在看" ? "good" : item.status === "搁置" ? "warn" : "dim"}>{text(item.action)}</Badge>
              <div className="compact-list inline">
                {list<string>(item.why).slice(0, 4).map((r, idx) => <span key={idx}>{r}</span>)}
              </div>
            </div>
          </a>
        ))}
      </div>
      <div className="taste-groups">
        {groups.map(([label, key]) => (
          <div className="taste-group" key={key}>
            <div className="section-title">{label}</div>
            {list(data[key]).length ? (
              <div className="compact-list">
                {list(data[key]).slice(0, 5).map((item, i) => (
                  <span key={`${item.id}-${i}`}>{text(item.name)} · {text(item.action)}</span>
                ))}
              </div>
            ) : <EmptyHint text="暂无候选" />}
          </div>
        ))}
      </div>
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function _wrapText(ctx: CanvasRenderingContext2D, content: string, x: number, y: number, maxW: number, lh: number): number {
  let line = "";
  for (const ch of String(content)) {
    if (ctx.measureText(line + ch).width > maxW && line) {
      ctx.fillText(line, x, y); line = ch; y += lh;
    } else line += ch;
  }
  if (line) { ctx.fillText(line, x, y); y += lh; }
  return y;
}

function exportTasteCard(data: AnyRecord): void {
  const canvas = document.createElement("canvas");
  canvas.width = 600; canvas.height = 760;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.fillStyle = "#0f1117"; ctx.fillRect(0, 0, 600, 760);
  ctx.fillStyle = "#c9a3ff"; ctx.font = "bold 30px sans-serif";
  ctx.fillText("Otomo · 二次元人格卡", 40, 64);
  ctx.fillStyle = "#ffffff"; ctx.font = "bold 24px sans-serif";
  ctx.fillText(`@${text(data.username, "用户")}`, 40, 108);
  ctx.fillStyle = "#9aa4b2"; ctx.font = "16px sans-serif";
  let y = _wrapText(ctx, text(data.share_summary, ""), 40, 146, 520, 24) + 16;
  ctx.fillStyle = "#86efac"; ctx.font = "16px sans-serif";
  y = _wrapText(ctx, "标签：" + (list<string>(data.report_tags).join(" · ") || "—"), 40, y, 520, 24) + 20;
  const sec = list(data.sections)[0] as AnyRecord | undefined;
  if (sec) {
    ctx.fillStyle = "#ffffff"; ctx.font = "bold 19px sans-serif";
    ctx.fillText(`${text(sec.subject_type)} · 看过 ${sec.watched ?? 0} · 均分 ${sec.avg_rating ?? "-"}`, 40, y); y += 32;
    ctx.fillStyle = "#9aa4b2"; ctx.font = "15px sans-serif";
    y = _wrapText(ctx, text(sec.persona, ""), 40, y, 520, 22) + 14;
    const likes = list(sec.aspect_likes).map((x: AnyRecord) => text(x.label || x.aspect)).slice(0, 3);
    const dislikes = list(sec.aspect_dislikes).map((x: AnyRecord) => text(x.label || x.aspect)).slice(0, 3);
    ctx.fillStyle = "#86efac"; ctx.fillText("好球区：" + (likes.join("、") || "—"), 40, y); y += 28;
    ctx.fillStyle = "#fca5a5"; ctx.fillText("雷区：" + (dislikes.join("、") || "—"), 40, y); y += 36;
  }
  ctx.fillStyle = "#6b7280"; ctx.font = "13px sans-serif";
  ctx.fillText("由 Otomo · 番组搭子 生成", 40, 730);
  const a = document.createElement("a");
  a.href = canvas.toDataURL("image/png");
  a.download = `otomo-taste-${text(data.username, "card")}.png`;
  a.click();
}

function TasteReportPanel({ data }: { data: AnyRecord }) {
  const sections = list(data.sections);
  return (
    <Panel title={`口味报告 · ${text(data.username)}`} subtitle={text(data.share_summary, "")}>
      <div className="evidence-row">
        {list<string>(data.report_tags).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
        <button className="chip" onClick={() => exportTasteCard(data)}>📷 导出人格卡</button>
      </div>
      <div className="rating-grid">
        {sections.map((section, i) => (
          <div className="rating-card" key={`${section.subject_type}-${i}`}>
            <div className="rating-source">{text(section.subject_type)}</div>
            <div className="card-meta">
              看过 {section.watched ?? 0} · 评分 {section.rated ?? 0} · 均分 {section.avg_rating ?? "暂无"}
            </div>
            <p className="card-note">{text(section.persona, "")}</p>
            <div className="evidence-row tight">
              {list(section.top_tags).slice(0, 5).map((tag) => <Badge key={tag.tag} tone="dim">{text(tag.tag)}</Badge>)}
            </div>
            <div className="evidence-row tight">
              {list(section.aspect_likes).slice(0, 3).map((item) => <Badge key={`l-${item.aspect}`} tone="good">好球 {text(item.label || item.aspect)}</Badge>)}
              {list(section.aspect_dislikes).slice(0, 3).map((item) => <Badge key={`d-${item.aspect}`} tone="warn">雷区 {text(item.label || item.aspect)}</Badge>)}
            </div>
            {list<string>(section.next_actions).length > 0 && (
              <div className="compact-list">
                {list<string>(section.next_actions).map((x, idx) => <span key={idx}>{x}</span>)}
              </div>
            )}
          </div>
        ))}
      </div>
      {(list(data.global_likes).length > 0 || list(data.global_dislikes).length > 0) && (
        <div className="memory-grid">
          <div>
            <div className="section-title">长期喜欢</div>
            <div className="compact-list">{list(data.global_likes).slice(0, 8).map((x, i) => <span key={i}>{text(x.value)}</span>)}</div>
          </div>
          <div>
            <div className="section-title">长期避雷</div>
            <div className="compact-list">{list(data.global_dislikes).slice(0, 8).map((x, i) => <span key={i}>{text(x.value)}</span>)}</div>
          </div>
        </div>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

export function SpoilerBadge({ spoiler }: { spoiler: SpoilerState | null }) {
  if (!spoiler) return null;
  const mode = spoiler.mode || "none";
  const tone = mode === "full" ? "bad" : mode === "mild" ? "warn" : "good";
  return (
    <div className="spoiler-state">
      <Badge tone={tone}>剧透: {mode}</Badge>
      {spoiler.progress_episode !== undefined && spoiler.progress_episode !== null && (
        <Badge tone="dim">进度: 第 {spoiler.progress_episode} 集</Badge>
      )}
      {spoiler.pending_followup && <Badge tone="warn">等待确认</Badge>}
    </div>
  );
}

export function MemoryBadge({ memory }: { memory: MemoryState | null }) {
  if (!memory) return null;
  const likeCount = list(memory.likes).length;
  const dislikeCount = list(memory.dislikes).length;
  const feedbackCount = list(memory.recent_feedback).length;
  const likePreview = list(memory.likes).slice(0, 3).map((x) => text(x.value, "")).filter(Boolean).join(" / ");
  const dislikePreview = list(memory.dislikes).slice(0, 3).map((x) => text(x.value, "")).filter(Boolean).join(" / ");
  return (
    <div className="memory-state">
      <Badge tone="dim">记忆: {text(memory.username, "未绑定")}</Badge>
      <Badge tone={likeCount ? "good" : "dim"}>喜欢 {likeCount}{likePreview ? ` · ${likePreview}` : ""}</Badge>
      <Badge tone={dislikeCount ? "warn" : "dim"}>避雷 {dislikeCount}{dislikePreview ? ` · ${dislikePreview}` : ""}</Badge>
      {feedbackCount > 0 && <Badge tone="dim">反馈 {feedbackCount}</Badge>}
      {memory.spoiler_default && memory.spoiler_default !== "none" && (
        <Badge tone={memory.spoiler_default === "full" ? "bad" : "warn"}>默认剧透 {memory.spoiler_default}</Badge>
      )}
    </div>
  );
}

function MemoryPanel({ data }: { data: MemoryState }) {
  const likes = list(data.likes);
  const dislikes = list(data.dislikes);
  const feedback = list(data.recent_feedback);
  const progress = data.progress || {};
  const progressEntries = Object.entries(progress).slice(0, 12);
  const profiles = Object.entries(data.profile_snapshot || {}).slice(0, 3);
  const aspectProfiles = Object.entries(data.aspect_profiles || {}).slice(0, 4);
  return (
    <Panel
      title={`长期记忆 · ${text(data.username, "unknown")}`}
      subtitle={`喜欢 ${likes.length} · 避雷 ${dislikes.length} · 反馈 ${feedback.length}`}
    >
      <div className="evidence-row">
        <Badge tone="dim">spoiler_default: {text(data.spoiler_default, "none")}</Badge>
        {data.updated_at && <Badge tone="dim">updated {data.updated_at}</Badge>}
      </div>

      <div className="memory-grid">
        <div>
          <div className="section-title">喜欢 / 正偏好</div>
          {likes.length ? (
            <div className="compact-list">
              {likes.map((item, i) => (
                <span key={`${item.value}-${i}`}>
                  {text(item.value)}
                  <small> · {text(item.source, "unknown")} · {pct(item.confidence)}</small>
                </span>
              ))}
            </div>
          ) : (
            <EmptyHint text="还没有长期喜欢项" />
          )}
        </div>
        <div>
          <div className="section-title">避雷 / 负偏好</div>
          {dislikes.length ? (
            <div className="compact-list">
              {dislikes.map((item, i) => (
                <span key={`${item.value}-${i}`}>
                  {text(item.value)}
                  <small> · {text(item.source, "unknown")} · {pct(item.confidence)}</small>
                </span>
              ))}
            </div>
          ) : (
            <EmptyHint text="还没有长期避雷项" />
          )}
        </div>
      </div>

      {progressEntries.length > 0 && (
        <>
          <div className="section-title">观看进度</div>
          <div className="evidence-row">
            {progressEntries.map(([subject, item]) => (
              <Badge key={subject} tone={sourceTone(item.source)}>
                {subject}: 第 {item.episode ?? "-"} 集
              </Badge>
            ))}
          </div>
        </>
      )}

      {profiles.length > 0 && (
        <>
          <div className="section-title">画像摘要</div>
          <div className="memory-grid">
            {profiles.map(([subjectType, profile]) => (
              <div className="rating-card" key={subjectType}>
                <div className="rating-source">{subjectType}</div>
                <div className="card-meta">
                  看过 {profile.watched ?? "-"} · 均分 {profile.avg_rating ?? "暂无"}
                </div>
                <div className="evidence-row tight">
                  {list(profile.top_tags).slice(0, 6).map((item) => (
                    <Badge key={item.tag} tone="dim">{text(item.tag)}</Badge>
                  ))}
                </div>
                {list<string>(profile.favorites).length > 0 && (
                  <div className="compact-list inline">
                    {list<string>(profile.favorites).slice(0, 3).map((name) => <span key={name}>{name}</span>)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {aspectProfiles.length > 0 && (
        <>
          <div className="section-title">Aspect 好球区 / 雷区</div>
          <div className="memory-grid">
            {aspectProfiles.map(([subjectType, profile]) => (
              <div className="rating-card" key={subjectType}>
                <div className="rating-source">{subjectType}</div>
                <div className="card-meta">
                  {text(profile.extraction_source, "none")} · {profile.sample_count ?? 0} 样本
                </div>
                <div className="evidence-row tight">
                  {list(profile.likes).slice(0, 4).map((item) => (
                    <Badge key={`like-${item.aspect}`} tone="good">好球 {text(item.label || item.aspect)}</Badge>
                  ))}
                  {list(profile.dislikes).slice(0, 4).map((item) => (
                    <Badge key={`dislike-${item.aspect}`} tone="warn">雷区 {text(item.label || item.aspect)}</Badge>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {feedback.length > 0 && (
        <>
          <div className="section-title">近期推荐反馈</div>
          <div className="compact-list">
            {feedback.map((item, i) => (
              <span key={`${item.name || item.subject_id}-${i}`}>
                {text(item.name || item.subject_id, "候选")} · {text(item.signal)}
                {item.note ? ` · ${item.note}` : ""}
              </span>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}

function EpisodeRadarPanel({ data }: { data: AnyRecord }) {
  const curve = list(data.curve);
  const peaks = list(data.peaks);
  const maxC = Math.max(...curve.map((p: AnyRecord) => Number(p.comments) || 0), 1);
  return (
    <Panel title={`分集口碑雷达 · subject ${text(data.subject_id)}`} subtitle={`共 ${data.total ?? curve.length} 集 · 讨论热度曲线`}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 92, marginBottom: 10, overflowX: "auto" }}>
        {curve.map((p: AnyRecord, i: number) => (
          <div key={i} title={`第 ${p.sort} 集 ${text(p.name)} · ${p.comments} 讨论`}
               style={{ flex: "1 0 6px", display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
            <span style={{ width: "100%", minHeight: 2, borderRadius: 2,
                           height: `${Math.round((Number(p.comments) || 0) / maxC * 80)}px`,
                           background: "var(--accent, #c9a3ff)" }} />
            <small style={{ fontSize: 9, color: "var(--dim, #888)" }}>{p.sort}</small>
          </div>
        ))}
      </div>
      <div className="section-title">高能集（讨论最热）</div>
      <div className="compact-list">
        {peaks.map((p: AnyRecord, i: number) => (
          <span key={i}>第 {p.sort} 集 · {text(p.name, "")} · {p.comments} 讨论</span>
        ))}
      </div>
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function ExplorerPanel({ data }: { data: AnyRecord }) {
  const nodes = list(data.nodes);
  return (
    <Panel
      title={`角色/声优网络 · ${text(data.anchor)}`}
      subtitle={data.anchor_kind === "person" ? "声优出演网络（按评分）" : "作品角色声优阵容"}
    >
      <div className="rec-grid">
        {nodes.map((n, i) => (
          <a className="rec-card" href={n.url || "#"} target="_blank" rel="noreferrer" key={`${n.id}-${i}`}>
            {n.image ? <img src={n.image} alt="" /> : <div className="rec-noimg" />}
            <div className="rec-body">
              <div className="card-title">{text(n.name)}</div>
              <div className="card-meta">
                {n.detail ? text(n.detail) : ""}{n.score ? ` · ${n.score}` : ""}
              </div>
            </div>
          </a>
        ))}
      </div>
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((nt, i) => <span key={i}>{nt}</span>)}</div>
      )}
    </Panel>
  );
}

export function EvidencePanels({ evidence, onCritique }: { evidence: EvidenceMap; onCritique?: (q: string) => void }) {
  const review = list(evidence.review_subject);
  const taste = list(evidence.compare_user_taste);
  const season = list(evidence.season_guide_brief);
  const recommend = list(evidence.recommend_subjects);
  const aspect = list(evidence.build_aspect_profile);
  const watchCopilot = list(evidence.plan_watch_copilot);
  const tasteReport = list(evidence.build_taste_report);
  const explorer = list(evidence.explore_voice_network);
  const episodeRadar = list(evidence.episode_buzz_radar);
  const memory = [
    ...list(evidence.get_user_memory),
    ...list(evidence.remember_user_preference),
    ...list(evidence.forget_user_memory),
    ...list(evidence.record_recommendation_feedback),
  ];
  if (
    !review.length && !taste.length && !season.length && !recommend.length && !memory.length
    && !aspect.length && !watchCopilot.length && !tasteReport.length && !explorer.length
    && !episodeRadar.length
  ) return null;
  return (
    <div className="evidence-stack">
      {memory.map((data, i) => <MemoryPanel data={data} key={`memory-${i}`} />)}
      {aspect.map((data, i) => <AspectProfilePanel data={data} key={`aspect-${i}`} />)}
      {tasteReport.map((data, i) => <TasteReportPanel data={data} key={`taste-report-${i}`} />)}
      {review.map((data, i) => <ReviewEvidencePanel data={data} key={`review-${i}`} />)}
      {taste.map((data, i) => <TasteAffinityPanel data={data} key={`taste-${i}`} />)}
      {season.map((data, i) => <SeasonGuidePanel data={data} key={`season-${i}`} />)}
      {explorer.map((data, i) => <ExplorerPanel data={data} key={`explorer-${i}`} />)}
      {episodeRadar.map((data, i) => <EpisodeRadarPanel data={data} key={`ep-radar-${i}`} />)}
      {watchCopilot.map((data, i) => <WatchCopilotPanel data={data} key={`watch-copilot-${i}`} />)}
      {recommend.map((data, i) => <RecommendPanel data={data} onCritique={onCritique} key={`recommend-${i}`} />)}
    </div>
  );
}
