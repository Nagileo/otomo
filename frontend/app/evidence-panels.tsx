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

function RecommendPanel({ data }: { data: AnyRecord }) {
  const items = list(data.items);
  return (
    <Panel
      title={`推荐证据 · ${text(data.subject_type)}`}
      subtitle={`mode: ${text(data.mode, "normal")} · ${items.length} 个候选`}
    >
      <div className="evidence-row">
        {list<string>(data.based_on_tags).slice(0, 10).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
      </div>
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
                {list<string>(item.explicit_tag_matches).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
                {list<string>(item.quality_badges).map((tag) => <Badge key={tag} tone="warn">{tag}</Badge>)}
              </div>
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
            </div>
          </a>
        ))}
      </div>
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

export function EvidencePanels({ evidence }: { evidence: EvidenceMap }) {
  const review = list(evidence.review_subject);
  const taste = list(evidence.compare_user_taste);
  const season = list(evidence.season_guide_brief);
  const recommend = list(evidence.recommend_subjects);
  const memory = [
    ...list(evidence.get_user_memory),
    ...list(evidence.remember_user_preference),
    ...list(evidence.forget_user_memory),
    ...list(evidence.record_recommendation_feedback),
  ];
  if (!review.length && !taste.length && !season.length && !recommend.length && !memory.length) return null;
  return (
    <div className="evidence-stack">
      {memory.map((data, i) => <MemoryPanel data={data} key={`memory-${i}`} />)}
      {review.map((data, i) => <ReviewEvidencePanel data={data} key={`review-${i}`} />)}
      {taste.map((data, i) => <TasteAffinityPanel data={data} key={`taste-${i}`} />)}
      {season.map((data, i) => <SeasonGuidePanel data={data} key={`season-${i}`} />)}
      {recommend.map((data, i) => <RecommendPanel data={data} key={`recommend-${i}`} />)}
    </div>
  );
}
