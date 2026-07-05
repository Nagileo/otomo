"use client";

import { useState, type ReactNode } from "react";

import { TrendingPanel } from "./panels/media";
import { InboxPanel } from "./panels/memory";
import { PixivPanel } from "./panels/visual";

// 公共原语已 export，供 panels/ 域文件复用（新面板一律写进 panels/<域>.tsx，
// 本文件的旧面板逐步搬迁，不再新增）。
export type AnyRecord = Record<string, any>;
type EvidenceMap = Record<string, AnyRecord[]>;
type EvidenceMode = "user" | "dev";
type SpoilerState = {
  mode?: string;
  memory_default?: string;
  soft_warning?: boolean;
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
  pending_write_actions?: AnyRecord[];
  recent_decisions?: AnyRecord[];
  watch_plan?: AnyRecord[];
  recommendation_lists?: AnyRecord[];
  weekly_digest_subscription?: AnyRecord;
  inbox?: AnyRecord[];
  recent_visual_feedback?: AnyRecord[];
  updated_at?: string;
};

export function list<T = AnyRecord>(value: any): T[] {
  return Array.isArray(value) ? value : [];
}

export function text(value: any, fallback = "未知") {
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

function confidenceLabel(value: any) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "unknown";
  if (n >= 0.85) return "high";
  if (n >= 0.6) return "medium";
  if (n > 0) return "low";
  return "unknown";
}

function sourceTone(source: any) {
  const s = String(source ?? "");
  if (s === "explicit_user") return "good";
  if (s === "derived_from_feedback") return "warn";
  if (s === "bangumi_profile") return "dim";
  return "dim";
}

function hasActionableMemory(data: AnyRecord) {
  return (
    list(data.pending_write_actions).length > 0
    || list(data.inbox).some((item) => item?.unread)
    || Boolean(data.weekly_digest_subscription?.pending)
  );
}

export function Badge({ children, tone = "dim" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Panel({
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

type PrepareWriteHandler = (subjectId: number, subjectName: string, collectionType?: number) => void;
type PrepareDownloaderHandler = (payload: AnyRecord) => void;

function WhereToWatchPanel({ data }: { data: AnyRecord }) {
  const official = list(data.official_sources);
  const fallbacks = list(data.search_fallbacks);
  return (
    <Panel
      title={`正版观看 · ${text(data.title)}`}
      subtitle={`${official.length} 个官方候选 · ${fallbacks.length} 个搜索兜底`}
    >
      <div className="evidence-row">
        <Badge tone={official.length ? "good" : "warn"}>{official.length ? "official sources" : "no verified platform"}</Badge>
        {data.offline_hint && <Badge tone="dim">可继续查 RSS/BD</Badge>}
      </div>
      {official.length ? (
        <div className="rating-grid">
          {official.map((src, i) => (
            <a className="rating-card" href={src.url} target="_blank" rel="noreferrer" key={`${src.url}-${i}`}>
              <div className="rating-source">{text(src.label)}</div>
              <div className="card-meta">{text(src.source)} · {list<string>(src.regions).join("/") || "region unknown"}</div>
              <Badge tone={src.confidence >= 0.8 ? "good" : "warn"}>match {pct(src.confidence)}</Badge>
              {src.note && <p className="card-note">{text(src.note)}</p>}
            </a>
          ))}
        </div>
      ) : (
        <EmptyHint text="没有查到明确正版平台入口，下面只给搜索兜底。" />
      )}
      {fallbacks.length > 0 && (
        <>
          <div className="section-title">搜索兜底</div>
          <div className="compact-list">
            {fallbacks.map((src, i) => (
              <a href={src.url} target="_blank" rel="noreferrer" key={`${src.url}-${i}`}>
                {text(src.label)}<small> · {text(src.note, "")}</small>
              </a>
            ))}
          </div>
        </>
      )}
      {list<string>(data.mapping_notes).length > 0 && (
        <p className="card-note">{list<string>(data.mapping_notes).join(" · ")}</p>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((c, i) => <span key={i}>{c}</span>)}</div>
      )}
    </Panel>
  );
}

function ReleaseItemCard({
  item,
  subjectId,
  subjectName,
  onPrepareDownloaderPush,
}: {
  item: AnyRecord;
  subjectId?: number;
  subjectName: string;
  onPrepareDownloaderPush?: PrepareDownloaderHandler;
}) {
  return (
    <div className="release-item">
      <div className="release-item-head">
        {item.subgroup && <Badge tone="good">{text(item.subgroup)}</Badge>}
        <Badge tone="dim">{text(item.source)}</Badge>
        {item.quality && item.quality !== "tv" && <Badge tone="warn">{text(item.quality)}</Badge>}
        {item.pub_date && <span className="release-date">{String(item.pub_date).slice(0, 10)}</span>}
      </div>
      <div className="release-item-title" title={text(item.title)}>{text(item.title)}</div>
      <div className="release-item-actions">
        {item.page_url && <a href={item.page_url} target="_blank" rel="noreferrer">页面</a>}
        {item.torrent_url && <a href={item.torrent_url} target="_blank" rel="noreferrer">种子</a>}
        {item.magnet && <a href={item.magnet}>磁力</a>}
        {onPrepareDownloaderPush && (item.torrent_url || item.magnet) && (
          <button
            type="button"
            className="inline-action"
            onClick={() => onPrepareDownloaderPush({
              torrent_url: item.torrent_url || "",
              magnet: item.magnet || "",
              title: item.title,
              subject_id: subjectId,
              subject_name: subjectName,
            })}
          >
            推送下载器
          </button>
        )}
      </div>
    </div>
  );
}

function ReleaseFeedsPanel({ data, onPrepareDownloaderPush }: { data: AnyRecord; onPrepareDownloaderPush?: PrepareDownloaderHandler }) {
  const groups = list(data.groups);
  const fallback = list(data.fallback_items);
  const links = list(data.search_links);
  const subjectId = data.subject_id ? Number(data.subject_id) : undefined;
  return (
    <Panel
      title={`离线资源/RSS · ${text(data.title)}`}
      subtitle={`Mikan ${list(data.mikan_ids).length} 映射 · ${groups.length} 组 · 兜底 ${fallback.length} 条`}
    >
      <div className="evidence-row">
        <Badge tone={data.mapping_confidence >= 0.8 ? "good" : "warn"}>mapping {pct(data.mapping_confidence)}</Badge>
        <Badge tone="warn">link aggregation only</Badge>
      </div>
      {groups.length ? (
        <div className="digest-list">
          {groups.map((group, i) => (
            <div className="digest-card" key={`${group.source}-${group.subgroup}-${i}`}>
              <div className="release-group-head">
                <span className="digest-title">{text(group.subgroup)}</span>
                <Badge tone="dim">{text(group.source)}</Badge>
                {group.quality && group.quality !== "tv" && <Badge tone="warn">{text(group.quality)}</Badge>}
                {group.rss_url && (
                  <>
                    <a href={group.rss_url} target="_blank" rel="noreferrer" className="inline-link">RSS</a>
                    <button
                      type="button"
                      className="inline-action"
                      onClick={() => navigator.clipboard?.writeText(group.rss_url)}
                    >
                      复制 RSS
                    </button>
                  </>
                )}
              </div>
              <div className="release-list">
                {list(group.latest_items).slice(0, 4).map((item, idx) => (
                  <ReleaseItemCard
                    item={item}
                    subjectId={subjectId}
                    subjectName={text(data.title)}
                    onPrepareDownloaderPush={onPrepareDownloaderPush}
                    key={`${item.title}-${idx}`}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyHint text="没有查到 Mikan 分组 RSS；可看下方兜底结果。" />
      )}
      {fallback.length > 0 && (
        <>
          <div className="section-title">DMHY / ACGNX 兜底</div>
          <div className="release-list">
            {fallback.map((item, i) => (
              <ReleaseItemCard
                item={item}
                subjectId={subjectId}
                subjectName={text(data.title)}
                onPrepareDownloaderPush={onPrepareDownloaderPush}
                key={`${item.title}-${i}`}
              />
            ))}
          </div>
        </>
      )}
      {links.length > 0 && (
        <>
          <div className="section-title">搜索入口</div>
          <div className="compact-list">
            {links.map((link, i) => (
              <a href={link.url} target="_blank" rel="noreferrer" key={`${link.url}-${i}`}>
                {text(link.label)}<small> · {text(link.note, "")}</small>
              </a>
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

function BangumiIndexPanel({ data, onPrepareWrite }: { data: AnyRecord; onPrepareWrite?: PrepareWriteHandler }) {
  const items = list(data.items);
  return (
    <Panel
      title={`Bangumi 目录 · ${text(data.title)}`}
      subtitle={`${text(data.creator, "unknown")} · ${items.length} 条 · index ${text(data.index_id)}`}
    >
      {data.description && <p className="evidence-copy">{text(data.description)}</p>}
      <div className="season-grid">
        {items.map((item, i) => (
          <a className="season-card" href={item.url || `https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="season-noimg" />}
            <div className="season-main">
              <div className="card-title">{text(item.name_cn || item.name)}</div>
              <div className="card-meta">
                {item.score ? `Bangumi ${item.score}` : "暂无评分"}
                {item.rank ? ` · rank ${item.rank}` : ""}
                {item.collection_status ? ` · 收藏状态 ${item.collection_status}` : ""}
              </div>
              {item.comment && <p className="card-note">{text(item.comment)}</p>}
              {item.id && onPrepareWrite && (
                <button
                  type="button"
                  className="inline-action card-action"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onPrepareWrite(Number(item.id), text(item.name_cn || item.name), 1);
                  }}
                >
                  想看
                </button>
              )}
            </div>
          </a>
        ))}
      </div>
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function SeasonGuidePanel({ data, onPrepareWrite }: { data: AnyRecord; onPrepareWrite?: PrepareWriteHandler }) {
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
                {item.subject_id && onPrepareWrite && (
                  <button
                    type="button"
                    className="inline-action card-action"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onPrepareWrite(Number(item.subject_id), text(item.title), 1);
                    }}
                  >
                    想看
                  </button>
                )}
                {item.official_url && <span>官网</span>}
                {item.pv_url && <span>PV</span>}
                {item.bili_url && <span>B站正版</span>}
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

function BroadcastCalendarPanel({ data, onPrepareWrite }: { data: AnyRecord; onPrepareWrite?: PrepareWriteHandler }) {
  const days = list(data.days);
  return (
    <Panel
      title={data.scope === "today" ? "今日放送" : "本周放送日历"}
      subtitle={`${text(data.today)} · ${data.count ?? 0} 部${data.only_mine ? ` · @${text(data.username)}` : ""}`}
    >
      {days.length > 0 ? (
        <div className="calendar-stack">
          {days.map((day, i) => {
            const items = list(day.items);
            return (
              <div className={`calendar-day ${day.is_today ? "today" : ""}`} key={`${day.weekday_id}-${i}`}>
                <div className="calendar-head">
                  <strong>{text(day.weekday_cn)}</strong>
                  {day.is_today && <Badge tone="good">今天</Badge>}
                  <span>{items.length} 部</span>
                </div>
                {items.length > 0 ? (
                  <div className="rec-grid">
                    {items.map((item, idx) => (
                      <a className="rec-card" href={item.url || `https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${idx}`}>
                        {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
                        <div className="rec-body">
                          <div className="card-title">{text(item.name_cn || item.name)}</div>
                          <div className="card-meta">
                            {item.air_date || "日期未定"}
                            {item.score ? ` · BGM ${item.score}` : ""}
                            {item.doing ? ` · 在看 ${item.doing}` : ""}
                          </div>
                          <div className="evidence-row tight">
                            {item.my_collection_label && <Badge tone={item.my_collection === "watching" ? "good" : "dim"}>{text(item.my_collection_label)}</Badge>}
                            {item.ep_status != null && <Badge tone="dim">进度 {item.ep_status}</Badge>}
                            {item.id && onPrepareWrite && (
                              <button
                                type="button"
                                className="inline-action card-action"
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  onPrepareWrite(Number(item.id), text(item.name_cn || item.name), 1);
                                }}
                              >
                                想看
                              </button>
                            )}
                          </div>
                        </div>
                      </a>
                    ))}
                  </div>
                ) : (
                  <EmptyHint text="这一天没有命中条目" />
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyHint text="没有拿到放送条目；如果只看自己的列表，可能需要登录或公开收藏" />
      )}
      {list<string>(data.notes).length > 0 && (
        <div className="caveats">{list<string>(data.notes).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function AiringProgressPanel({ data }: { data: AnyRecord }) {
  const items = list(data.items);
  return (
    <Panel
      title="追番进度"
      subtitle={`@${text(data.username)} · ${text(data.today)} · 落后 ${data.behind_count ?? 0} 部`}
    >
      {items.length > 0 ? (
        <div className="progress-list">
          {items.map((item, i) => {
            const max = Math.max(Number(item.aired_ep || 0), Number(item.my_ep || 0), 1);
            const pctDone = Math.min(100, Math.round((Number(item.my_ep || 0) / max) * 100));
            return (
              <a className="progress-item" href={item.url || `https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
                {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
                <div className="progress-body">
                  <div className="progress-title">
                    <strong>{text(item.name)}</strong>
                    <Badge tone={item.behind > 0 ? "warn" : "good"}>{item.behind > 0 ? `落后 ${item.behind}` : "同步"}</Badge>
                  </div>
                  <div className="card-meta">
                    你看到 {item.my_ep ?? 0} · 已播 {item.aired_ep ?? 0}
                    {item.total_eps ? ` / ${item.total_eps}` : ""}
                    {item.next_air_date ? ` · 下集 ${item.next_air_date}` : ""}
                  </div>
                  <div className="progress-bar"><span style={{ width: `${pctDone}%` }} /></div>
                  <p className="card-note">{text(item.action)}</p>
                </div>
              </a>
            );
          })}
        </div>
      ) : (
        <EmptyHint text="没有拿到在看进度；可能收藏列表为空、私有，或这些条目没有正片 airdate" />
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

function RecommendPanel({
  data,
  onCritique,
  onPrepareWrite,
}: {
  data: AnyRecord;
  onCritique?: (q: string) => void;
  onPrepareWrite?: PrepareWriteHandler;
}) {
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
                {item.id && onPrepareWrite && (
                  <button
                    type="button"
                    className="inline-action card-action"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onPrepareWrite(Number(item.id), text(item.name), 1);
                    }}
                  >
                    想看
                  </button>
                )}
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
                      {text(m.source)} 对齐《{text(m.external_title)}》 · 置信度 {confidenceLabel(m.mapping_confidence)}
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

function WeeklyDigestPanel({ data }: { data: AnyRecord }) {
  const sections = list(data.sections);
  return (
    <Panel title={`本周周报 · ${text(data.username)}`} subtitle={text(data.week, "本周")}>
      <div className="evidence-row">
        {list<string>(data.profile_tags).slice(0, 10).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
      </div>
      {sections.map((section, i) => (
        <div key={`${section.title}-${i}`}>
          <div className="section-title">{text(section.title)}</div>
          <div className="rec-grid">
            {list(section.items).slice(0, 6).map((item, idx) => (
              <a className="rec-card" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${idx}`}>
                {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
                <div className="rec-body">
                  <div className="card-title">{text(item.name)}</div>
                  <div className="card-meta">
                    {text(item.status)} · {text(item.action)} · BGM {item.bangumi_score ?? "暂无"}
                  </div>
                  <div className="compact-list inline">
                    {list<string>(item.why).slice(0, 3).map((why, j) => <span key={j}>{why}</span>)}
                  </div>
                </div>
              </a>
            ))}
          </div>
          {list<string>(section.notes).length > 0 && (
            <div className="caveats">{list<string>(section.notes).map((n, j) => <span key={j}>{n}</span>)}</div>
          )}
        </div>
      ))}
      {list<string>(data.next_actions).length > 0 && (
        <>
          <div className="section-title">下一步</div>
          <div className="compact-list">{list<string>(data.next_actions).map((n, i) => <span key={i}>{n}</span>)}</div>
        </>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function VisualCorrectionButton({
  item,
  imageUri,
  subjectType,
  onSearch,
  onSubmit,
}: {
  item: AnyRecord;
  imageUri: string;
  subjectType: string;
  onSearch: (query: string, subjectType?: string) => Promise<AnyRecord[]>;
  onSubmit: (payload: AnyRecord) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(text(item.predicted_title || item.title || item.bangumi_name, ""));
  const [note, setNote] = useState("");
  const [results, setResults] = useState<AnyRecord[]>([]);
  const [busy, setBusy] = useState(false);

  async function runSearch() {
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    try {
      setResults(await onSearch(q, subjectType));
    } finally {
      setBusy(false);
    }
  }

  function basePayload(signal: string) {
    return {
      image_uri: imageUri,
      tool_name: "route_image_source",
      predicted_subject_id: item.bangumi_id ?? null,
      predicted_subject_name: item.bangumi_name || "",
      predicted_title: item.title || item.bangumi_name || "",
      source: item.source || "",
      confidence: Number(item.confidence || 0),
      signal,
      note,
    };
  }

  return (
    <div className="correction-box">
      <button type="button" className="inline-action" onClick={() => setOpen((v) => !v)}>
        改正
      </button>
      {open && (
        <div className="correction-panel">
          <div className="correction-row">
            <input
              type="text"
              value={query}
              placeholder="搜索正确 Bangumi 条目"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runSearch()}
            />
            <button type="button" className="inline-action" disabled={busy || !query.trim()} onClick={runSearch}>
              {busy ? "搜索中" : "搜索"}
            </button>
          </div>
          <input
            type="text"
            value={note}
            placeholder="可选备注：错在哪里 / 正确线索"
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="correction-results">
            {results.map((cand) => (
              <button
                type="button"
                key={cand.id}
                className="correction-result"
                onClick={() => {
                  onSubmit({
                    ...basePayload("wrong"),
                    corrected_subject_id: cand.id ?? null,
                    corrected_subject_name: cand.name_cn || cand.name || "",
                  });
                  setOpen(false);
                }}
              >
                {cand.image ? <img src={cand.image} alt="" /> : <span className="shared-noimg" />}
                <span>
                  <strong>{text(cand.name_cn || cand.name)}</strong>
                  <small>{cand.score ? `BGM ${cand.score}` : "Bangumi 候选"}</small>
                </span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className="inline-action"
            onClick={() => {
              onSubmit(basePayload("ambiguous"));
              setOpen(false);
            }}
          >
            只记录为不确定
          </button>
        </div>
      )}
    </div>
  );
}

function VisualTextPanel({ data }: { data: AnyRecord }) {
  const items = list(data.structured_items);
  const entities = list(data.entities);
  const tags = list<string>(data.visual_tags);
  return (
    <Panel
      title={`图片 OCR / 结构化 · ${text(data.mode, "auto")}`}
      subtitle={`${data.image_count ?? 1} 张图 · 置信度 ${pct(data.confidence)}`}
    >
      {tags.length > 0 && (
        <div className="evidence-row">
          {tags.map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
        </div>
      )}
      {data.markdown_text && (
        <>
          <div className="section-title">读取文本</div>
          <pre className="ocr-block">{text(data.markdown_text)}</pre>
        </>
      )}
      {items.length > 0 && (
        <>
          <div className="section-title">结构化条目</div>
          <div className="rating-grid">
            {items.map((item, i) => (
              <div className="rating-card" key={`${item.type}-${item.name}-${i}`}>
                <div className="rating-source">{text(item.type)}</div>
                <div className="card-title">{text(item.name || item.value, "条目")}</div>
                {item.value && <p className="card-note">{text(item.value)}</p>}
                {item.note && <div className="card-meta">{text(item.note)}</div>}
              </div>
            ))}
          </div>
        </>
      )}
      {entities.length > 0 && (
        <>
          <div className="section-title">Bangumi 回锚实体</div>
          <div className="rec-grid">
            {entities.map((item, i) => (
              <a
                className="rec-card"
                href={item.bangumi_id ? `https://bgm.tv/subject/${item.bangumi_id}` : "#"}
                target="_blank"
                rel="noreferrer"
                key={`${item.name}-${i}`}
              >
                {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
                <div className="rec-body">
                  <div className="card-title">{text(item.bangumi_name || item.name)}</div>
                  <div className="card-meta">
                    {item.bangumi_id ? "已回锚" : "未对齐"} · 置信度 {pct(item.confidence)}
                    {item.bangumi_score ? ` · BGM ${item.bangumi_score}` : ""}
                  </div>
                </div>
              </a>
            ))}
          </div>
        </>
      )}
      {data.raw_vlm_answer && (
        <details className="quiet-detail">
          <summary>查看视觉模型原始结构化输出</summary>
          <p className="evidence-copy">{text(data.raw_vlm_answer)}</p>
        </details>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function VisualStylePanel({ data }: { data: AnyRecord }) {
  const candidates = list(data.candidates);
  const visualTags = list<string>(data.visual_tags);
  const bangumiTags = list<string>(data.bangumi_tags);
  return (
    <Panel title="按画风/氛围推荐" subtitle={`置信度 ${pct(data.confidence)} · ${candidates.length} 个候选`}>
      {data.style_description && <p className="evidence-copy">{text(data.style_description)}</p>}
      {(visualTags.length > 0 || bangumiTags.length > 0) && (
        <div className="evidence-row">
          {visualTags.map((tag) => <Badge key={`v-${tag}`} tone="dim">{tag}</Badge>)}
          {bangumiTags.map((tag) => <Badge key={`b-${tag}`} tone="good">BGM {tag}</Badge>)}
        </div>
      )}
      <div className="rec-grid">
        {candidates.map((item, i) => (
          <a className="rec-card" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
            <div className="rec-body">
              <div className="card-title">{text(item.name)}</div>
              <div className="card-meta">Bangumi {item.score ?? "暂无"} · {text(item.reason)}</div>
              <div className="evidence-row tight">
                {list<string>(item.matched_tags).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
              </div>
            </div>
          </a>
        ))}
      </div>
      {data.raw_vlm_answer && (
        <details className="quiet-detail">
          <summary>查看视觉模型风格摘要</summary>
          <p className="evidence-copy">{text(data.raw_vlm_answer)}</p>
        </details>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function ImageSourcePanel({ data }: { data: AnyRecord }) {
  const matches = list(data.matches);
  const links = list(data.navigation_links);
  return (
    <Panel title="图片溯源候选" subtitle={`${matches.length} 个匹配 · ${links.length} 个导航入口`}>
      {matches.length > 0 ? (
        <div className="rec-grid">
          {matches.map((item, i) => (
            <a className="rec-card" href={item.url || "#"} target="_blank" rel="noreferrer" key={`${item.engine}-${i}`}>
              {item.thumbnail ? <img src={item.thumbnail} alt="" /> : <div className="rec-noimg" />}
              <div className="rec-body">
                <div className="card-title">{text(item.title || item.source_site || item.engine)}</div>
                <div className="card-meta">
                  {text(item.engine)} · sim {pct(item.similarity)} · conf {pct(item.confidence)}
                  {item.timestamp ? ` · ${item.timestamp}` : ""}
                </div>
                {item.author && <div className="card-meta">作者：{text(item.author)}</div>}
                {item.episode != null && <Badge tone="good">第 {text(item.episode)} 集</Badge>}
                {item.note && <p className="card-note">{text(item.note)}</p>}
              </div>
            </a>
          ))}
        </div>
      ) : (
        <EmptyHint text="没有结构化溯源候选；可能需要配置 SauceNAO API key 或换更清晰原图" />
      )}
      {links.length > 0 && (
        <>
          <div className="section-title">导航入口</div>
          <div className="source-links">
            {links.map((link, i) => (
              <a key={`${link.url}-${i}`} href={link.url} target="_blank" rel="noreferrer">
                <span>{text(link.source, "source")}</span>
                {text(link.title)}
              </a>
            ))}
          </div>
        </>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function RouteImageSourcePanel({
  data,
  onVisualFeedback,
  onVisualCorrectionSearch,
}: {
  data: AnyRecord;
  onVisualFeedback?: (payload: AnyRecord) => void;
  onVisualCorrectionSearch?: (query: string, subjectType?: string) => Promise<AnyRecord[]>;
}) {
  const candidates = list(data.candidates);
  const characters = list(data.character_candidates);
  const links = list(data.navigation_links);
  const nextTools = list<string>(data.next_tools);
  const tags = list<string>(data.visual_tags);
  const imageRefs = list<string>(data.image_refs);
  const confirm = Boolean(data.needs_user_confirmation);
  return (
    <Panel
      title="图片来源路由"
      subtitle={`${text(data.decision, "low_confidence")} · 置信度 ${pct(data.confidence)}${confirm ? " · 需要确认" : ""}`}
    >
      <div className="evidence-row">
        {list<string>(data.routes_considered).map((route) => <Badge key={route} tone="dim">{route}</Badge>)}
        {confirm && <Badge tone="warn">候选待确认</Badge>}
        {!confirm && <Badge tone="good">可作为入口</Badge>}
      </div>
      {tags.length > 0 && (
        <div className="evidence-row tight">
          {tags.map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
        </div>
      )}
      {candidates.length > 0 ? (
        <div className="rec-grid">
          {candidates.map((item, i) => {
            const href = item.bangumi_id ? `https://bgm.tv/subject/${item.bangumi_id}` : item.url || "#";
            return (
              <div className="rec-card" key={`${item.route}-${item.source}-${i}`}>
                {item.thumbnail ? <img src={item.thumbnail} alt="" /> : <div className="rec-noimg" />}
                <div className="rec-body">
                  <a className="card-title" href={href} target="_blank" rel="noreferrer">
                    {text(item.bangumi_name || item.title || item.source_site || item.source)}
                  </a>
                  <div className="card-meta">
                    {text(item.route, "unknown")} · {text(item.source, "source")} · conf {pct(item.confidence)}
                    {item.timestamp ? ` · ${item.timestamp}` : ""}
                    {item.bangumi_score ? ` · BGM ${item.bangumi_score}` : ""}
                  </div>
                  {item.author && <div className="card-meta">作者：{text(item.author)}</div>}
                  {(item.episode != null || item.timestamp) && (
                    <div className="evidence-row tight">
                      {item.episode != null && <Badge tone="good">第 {text(item.episode)} 集</Badge>}
                      {item.timestamp && <Badge tone="good">{text(item.timestamp)}</Badge>}
                    </div>
                  )}
                  {list<string>(item.evidence).length > 0 && (
                    <div className="evidence-row tight">
                      {list<string>(item.evidence).slice(0, 3).map((ev) => <Badge key={ev} tone="dim">{ev}</Badge>)}
                    </div>
                  )}
                  {(item.reason || item.note || item.match_note) && (
                    <p className="card-note">{text(item.reason || item.note || item.match_note)}</p>
                  )}
                  {item.match_note && <Badge tone={item.bangumi_id ? "good" : "warn"}>{text(item.match_note)}</Badge>}
                  {onVisualFeedback && (
                    <div className="feedback-actions">
                      <button
                        type="button"
                        className="inline-action"
                        onClick={(e) => {
                          onVisualFeedback({
                            image_uri: imageRefs[item.image_index ?? 0] || "",
                            tool_name: "route_image_source",
                            predicted_subject_id: item.bangumi_id ?? null,
                            predicted_subject_name: item.bangumi_name || "",
                            predicted_title: item.title || item.bangumi_name || "",
                            source: item.source || "",
                            confidence: Number(item.confidence || 0),
                            signal: "correct",
                          });
                        }}
                      >
                        正确
                      </button>
                      <button
                        type="button"
                        className="inline-action"
                        onClick={(e) => {
                          onVisualFeedback({
                            image_uri: imageRefs[item.image_index ?? 0] || "",
                            tool_name: "route_image_source",
                            predicted_subject_id: item.bangumi_id ?? null,
                            predicted_subject_name: item.bangumi_name || "",
                            predicted_title: item.title || item.bangumi_name || "",
                            source: item.source || "",
                            confidence: Number(item.confidence || 0),
                            signal: "wrong",
                          });
                        }}
                      >
                        不对
                      </button>
                      {onVisualCorrectionSearch && (
                        <VisualCorrectionButton
                          item={item}
                          imageUri={imageRefs[item.image_index ?? 0] || ""}
                          subjectType={text(item.bangumi_type, "anime")}
                          onSearch={onVisualCorrectionSearch}
                          onSubmit={onVisualFeedback}
                        />
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyHint text="没有足够候选；可以换更清晰原图，或补充作品/角色/来源类型线索" />
      )}
      {characters.length > 0 && (
        <>
          <div className="section-title">角色候选</div>
          <div className="compact-list inline">
            {characters.map((item, i) => (
              <span key={`${item.name}-${i}`}>
                {text(item.bangumi_name || item.name)} · 置信度 {pct(item.confidence)}
                {item.match_note ? ` · ${text(item.match_note)}` : ""}
              </span>
            ))}
          </div>
        </>
      )}
      {data.ocr_text && (
        <details className="quiet-detail">
          <summary>查看 OCR / 图片文字</summary>
          <pre className="ocr-block">{text(data.ocr_text)}</pre>
        </details>
      )}
      {data.raw_vlm_answer && (
        <details className="quiet-detail">
          <summary>查看视觉模型摘要</summary>
          <p className="evidence-copy">{text(data.raw_vlm_answer)}</p>
        </details>
      )}
      {nextTools.length > 0 && (
        <>
          <div className="section-title">建议后续工具</div>
          <div className="evidence-row tight">
            {nextTools.map((tool) => <Badge key={tool} tone="dim">{tool}</Badge>)}
          </div>
        </>
      )}
      {links.length > 0 && (
        <>
          <div className="section-title">反搜 / 导航入口</div>
          <div className="source-links">
            {links.map((link, i) => (
              <a key={`${link.url}-${i}`} href={link.url} target="_blank" rel="noreferrer">
                <span>{text(link.source, "source")}</span>
                {text(link.title)}
              </a>
            ))}
          </div>
        </>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function BiliVideoContentPanel({ data }: { data: AnyRecord }) {
  const layers = list<string>(data.read_layers);
  const content = list<string>(data.content_summary);
  const audience = list<string>(data.audience_summary);
  const metadata = list<string>(data.metadata_summary);
  const subtitles = list(data.subtitle_segments);
  const danmaku = list(data.danmaku_samples);
  const comments = list<string>(data.comment_samples);
  const href = text(data.source_url, "#");
  return (
    <Panel title="B站视频公开内容分析" subtitle={`${text(data.access_level, "unavailable")} · ${layers.join(" / ") || "未读到内容层"}`}>
      <div className="evidence-row">
        {layers.map((layer) => <Badge key={layer} tone={layer === "subtitle" ? "good" : layer === "metadata" ? "dim" : "warn"}>{layer}</Badge>)}
        {data.bvid && <Badge tone="dim">{text(data.bvid)}</Badge>}
        {data.aid && <Badge tone="dim">av{data.aid}</Badge>}
      </div>
      {data.title && (
        <a className="source-primary" href={href} target="_blank" rel="noreferrer">
          {text(data.title)}
        </a>
      )}
      {metadata.length > 0 && (
        <div className="compact-list inline">
          {metadata.slice(0, 4).map((item, i) => <span key={i}>{item}</span>)}
        </div>
      )}
      {content.length > 0 && (
        <>
          <div className="section-title">正文层摘要（字幕/ASR）</div>
          <div className="compact-list">
            {content.map((item, i) => <span key={i}>{item}</span>)}
          </div>
        </>
      )}
      {audience.length > 0 && (
        <>
          <div className="section-title">观众反应层（弹幕/评论）</div>
          <div className="compact-list">
            {audience.map((item, i) => <span key={i}>{item}</span>)}
          </div>
        </>
      )}
      {subtitles.length > 0 && (
        <details className="quiet-detail">
          <summary>查看字幕片段（{subtitles.length}）</summary>
          <div className="compact-list">
            {subtitles.map((seg, i) => (
              <span key={i}>{seg.start != null ? `${Math.floor(Number(seg.start))}s · ` : ""}{text(seg.text)}</span>
            ))}
          </div>
        </details>
      )}
      {(danmaku.length > 0 || comments.length > 0) && (
        <details className="quiet-detail">
          <summary>查看弹幕 / 评论样本（{danmaku.length + comments.length}）</summary>
          <div className="compact-list">
            {danmaku.slice(0, 8).map((item, i) => (
              <span key={`d-${i}`}>弹幕{item.time != null ? ` ${Math.floor(Number(item.time))}s` : ""} · {text(item.text)}</span>
            ))}
            {comments.slice(0, 8).map((item, i) => <span key={`c-${i}`}>评论 · {item}</span>)}
          </div>
        </details>
      )}
      {list<string>(data.analysis_plan).length > 0 && (
        <>
          <div className="section-title">后续分析建议</div>
          <div className="compact-list">{list<string>(data.analysis_plan).map((n, i) => <span key={i}>{n}</span>)}</div>
        </>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
      )}
    </Panel>
  );
}

function VideoFramePanel({ data }: { data: AnyRecord }) {
  const frames = list(data.frames);
  const subjects = list(data.candidate_subjects);
  return (
    <Panel title="视频关键帧分析" subtitle={`${data.frame_count ?? frames.length} 帧 · ${text(data.purpose, "both")}`}>
      {data.merged_ocr_text && (
        <>
          <div className="section-title">合并 OCR 摘要</div>
          <pre className="ocr-block">{text(data.merged_ocr_text)}</pre>
        </>
      )}
      {subjects.length > 0 && (
        <>
          <div className="section-title">识番候选</div>
          <div className="rec-grid">
            {subjects.map((item, i) => (
              <a className="rec-card" href={item.bangumi_id ? `https://bgm.tv/subject/${item.bangumi_id}` : "#"} target="_blank" rel="noreferrer" key={`${item.title}-${i}`}>
                {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
                <div className="rec-body">
                  <div className="card-title">{text(item.bangumi_name || item.title)}</div>
                  <div className="card-meta">
                    {text(item.source, "trace")} · conf {pct(item.confidence)}
                    {item.episode != null ? ` · 第 ${item.episode} 集` : ""}
                    {item.timestamp ? ` · ${item.timestamp}` : ""}
                  </div>
                </div>
              </a>
            ))}
          </div>
        </>
      )}
      {frames.length > 0 && (
        <>
          <div className="section-title">逐帧证据</div>
          <div className="rating-grid">
            {frames.map((frame, i) => (
              <div className="rating-card" key={`${frame.index}-${i}`}>
                <div className="rating-source">frame {frame.index ?? i}{frame.timestamp ? ` · ${frame.timestamp}` : ""}</div>
                <div className="card-meta">confidence {pct(frame.confidence)}</div>
                {frame.ocr_text && <p className="card-note">{text(frame.ocr_text)}</p>}
                {list<string>(frame.visual_tags).length > 0 && (
                  <div className="evidence-row tight">
                    {list<string>(frame.visual_tags).slice(0, 5).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
                  </div>
                )}
                {list(frame.structured_items).length > 0 && (
                  <div className="compact-list inline">
                    {list(frame.structured_items).slice(0, 3).map((item, idx) => (
                      <span key={idx}>{text(item.name || item.value, "条目")}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((n, i) => <span key={i}>{n}</span>)}</div>
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

function DistributionBadges({ data }: { data: AnyRecord }) {
  const entries = Object.entries(data || {}).slice(0, 10);
  if (!entries.length) return <span className="card-meta">暂无</span>;
  return (
    <div className="evidence-row tight">
      {entries.map(([key, value]) => <Badge key={key} tone="dim">{key}: {String(value)}</Badge>)}
    </div>
  );
}

function SubjectMiniList({ title, items }: { title: string; items: AnyRecord[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="section-title">{title}</div>
      <div className="compact-subject-grid">
        {items.slice(0, 8).map((item, i) => (
          <a
            key={`${item.id || item.name}-${i}`}
            className="compact-subject"
            href={item.id ? `https://bgm.tv/subject/${item.id}` : undefined}
            target="_blank"
            rel="noreferrer"
          >
            {item.image ? <img src={item.image} alt="" loading="lazy" /> : <span className="shared-noimg" />}
            <span>
              <strong>{text(item.name)}</strong>
              <small>{item.rate ? `评分 ${item.rate}` : text(item.status, "")}{item.ep_status ? ` · ep ${item.ep_status}` : ""}</small>
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}

function YearlyActivityList({ items }: { items: AnyRecord[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="section-title">年度活动</div>
      <div className="compact-list">
        {items.slice(0, 8).map((item, i) => (
          <span key={`${item.year}-${i}`}>
            {text(item.year)} · {item.total ?? 0} 项
            {item.avg_rating ? ` · 均分 ${item.avg_rating}` : ""}
            {item.high_rated ? ` · 高分 ${item.high_rated}` : ""}
            {item.on_hold_or_abandoned ? ` · 搁置/抛弃 ${item.on_hold_or_abandoned}` : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

function TagDriftList({ items }: { items: AnyRecord[] }) {
  if (!items.length) return null;
  const rising = items.filter((item) => item.trend === "rising").slice(0, 5);
  const receding = items.filter((item) => item.trend === "receding").slice(0, 5);
  return (
    <div>
      <div className="section-title">Tag 漂移</div>
      <div className="evidence-row tight">
        {rising.map((item, i) => (
          <Badge key={`r-${item.tag}-${i}`} tone="good">↑ {text(item.tag)} {pct(Number(item.delta || 0) * 100)}%</Badge>
        ))}
        {receding.map((item, i) => (
          <Badge key={`d-${item.tag}-${i}`} tone="warn">↓ {text(item.tag)} {pct(Math.abs(Number(item.delta || 0)) * 100)}%</Badge>
        ))}
      </div>
    </div>
  );
}

function AffinityList({ title, items }: { title: string; items: AnyRecord[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="section-title">{title}</div>
      <div className="compact-list">
        {items.slice(0, 6).map((item, i) => {
          const works = list(item.works).slice(0, 3).map((w) => text(w.name, "")).filter(Boolean).join(" / ");
          return (
            <span key={`${item.name}-${item.relation}-${i}`}>
              {text(item.name)}
              <small> · {text(item.relation)} · 命中 {item.count ?? 0}{works ? ` · ${works}` : ""}</small>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function DashboardOverview({ media, data }: { media: AnyRecord[]; data: AnyRecord }) {
  const biggest = [...media].sort((a, b) => Number(b.total || 0) - Number(a.total || 0))[0];
  const bestRated = [...media]
    .filter((m) => Number(m.rated || 0) >= 3 && Number.isFinite(Number(m.avg_rating)))
    .sort((a, b) => Number(b.avg_rating || 0) - Number(a.avg_rating || 0))[0];
  const drift: AnyRecord[] = media
    .flatMap((m): AnyRecord[] => list<AnyRecord>(m.tag_drift).map((item): AnyRecord => ({ ...item, subject_type: m.subject_type })))
    .sort((a, b) => Math.abs(Number(b.delta || 0)) - Math.abs(Number(a.delta || 0)))
    .slice(0, 4);
  const creators: AnyRecord[] = media
    .flatMap((m): AnyRecord[] => [
      ...list<AnyRecord>(m.studio_affinity).map((x): AnyRecord => ({ ...x, kind: "制作/开发", subject_type: m.subject_type })),
      ...list<AnyRecord>(m.staff_affinity).map((x): AnyRecord => ({ ...x, kind: "staff", subject_type: m.subject_type })),
      ...list<AnyRecord>(m.cv_affinity).map((x): AnyRecord => ({ ...x, kind: "CV", subject_type: m.subject_type })),
    ])
    .sort((a, b) => Number(b.count || 0) - Number(a.count || 0))
    .slice(0, 4);
  return (
    <div className="dashboard-overview">
      <div className="overview-card">
        <div className="metric-label">主收藏媒介</div>
        <div className="overview-value">{text(biggest?.subject_type, "-")}</div>
        <p className="card-note">{biggest ? `${biggest.total ?? 0} 项 · 已评分 ${biggest.rated ?? 0}` : "暂无收藏样本"}</p>
      </div>
      <div className="overview-card">
        <div className="metric-label">均分最高媒介</div>
        <div className="overview-value">{text(bestRated?.subject_type, "-")}</div>
        <p className="card-note">{bestRated ? `均分 ${bestRated.avg_rating} · ${bestRated.rated ?? 0} 个评分` : "评分样本不足"}</p>
      </div>
      <div className="overview-card wide">
        <div className="metric-label">近期口味漂移</div>
        {drift.length ? (
          <div className="evidence-row tight">
            {drift.map((item, i) => (
              <Badge key={`${item.subject_type}-${item.tag}-${i}`} tone={item.trend === "rising" ? "good" : "warn"}>
                {item.subject_type} {item.trend === "rising" ? "↑" : "↓"} {text(item.tag)}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="card-note">年代样本不足，暂不判断 drift。</p>
        )}
      </div>
      <div className="overview-card wide">
        <div className="metric-label">高频创作者 / 声优</div>
        {creators.length ? (
          <div className="compact-list inline">
            {creators.map((item, i) => (
              <span key={`${item.kind}-${item.name}-${i}`}>
                {text(item.name)} <small>· {text(item.kind)} · {text(item.subject_type)} · {item.count ?? 0}</small>
              </span>
            ))}
          </div>
        ) : (
          <p className="card-note">尚无 enrichment 命中。</p>
        )}
      </div>
      <div className="overview-card wide">
        <div className="metric-label">下一步</div>
        <p className="card-note">{text(list<string>(data.recommendations_for_next_step)[0], "可先运行推荐或弃坑分析，把仪表盘转成行动。")}</p>
      </div>
    </div>
  );
}

function YearSparkline({ items }: { items: AnyRecord[] }) {
  if (!items.length) return null;
  const rows = [...items]
    .sort((a, b) => String(a.year || "").localeCompare(String(b.year || "")))
    .slice(-10);
  const maxTotal = Math.max(...rows.map((x) => Number(x.total || 0)), 1);
  return (
    <div className="year-sparkline" aria-label="年度收藏趋势">
      {rows.map((item, i) => {
        const total = Number(item.total || 0);
        const high = Number(item.high_rated || 0);
        return (
          <span className="year-bar-wrap" key={`${item.year}-${i}`} title={`${item.year}: ${total} 项，高分 ${high}`}>
            <span className="year-bar" style={{ height: `${Math.max(10, Math.round((total / maxTotal) * 54))}px` }} />
            <small>{String(item.year || "").slice(2)}</small>
          </span>
        );
      })}
    </div>
  );
}

function CollectionDashboardPanel({ data }: { data: AnyRecord }) {
  const totals = data.totals || {};
  const media = list(data.media);
  const weekly = data.weekly_subscription || {};
  const enrichment = data.enrichment || {};
  const [selectedType, setSelectedType] = useState("all");
  const visibleMedia = selectedType === "all" ? media : media.filter((m) => m.subject_type === selectedType);
  const mediaTypes = ["all", ...media.map((m) => String(m.subject_type || "")).filter(Boolean)];
  return (
    <Panel title={`收藏仪表盘 · ${text(data.username)}`} subtitle={`生成于 ${text(data.generated_at, "-")}`}>
      <div className="metric-grid">
        <div className="metric-card"><div className="metric-label">总收藏</div><div className="metric-value">{totals.items ?? 0}</div></div>
        <div className="metric-card"><div className="metric-label">已评分</div><div className="metric-value">{totals.rated ?? 0}</div></div>
        <div className="metric-card"><div className="metric-label">计划板</div><div className="metric-value">{totals.watch_plan ?? 0}</div></div>
        <div className="metric-card"><div className="metric-label">未读周报</div><div className="metric-value">{totals.unread_inbox ?? 0}</div></div>
      </div>
      {data.rating_strictness && <p className="evidence-copy">{text(data.rating_strictness)}</p>}
      <div className="evidence-row">
        {list(data.global_top_tags).slice(0, 14).map((tag) => <Badge key={tag.tag} tone="good">{text(tag.tag)} · {tag.weight}</Badge>)}
      </div>
      <DashboardOverview media={media} data={data} />
      {enrichment.enabled && (
        <div className="evidence-row">
          <Badge tone="dim">enrichment: 每类最多 {enrichment.limit_per_type ?? "-"} 条代表作</Badge>
          {Object.entries(enrichment.sampled_by_type || {}).slice(0, 5).map(([subjectType, row]) => (
            <Badge key={subjectType} tone="dim">{subjectType}: {(row as AnyRecord).sampled_count ?? 0} 样本</Badge>
          ))}
        </div>
      )}
      <div className="dashboard-filter">
        <div className="segmented" aria-label="收藏媒介筛选">
          {mediaTypes.map((kind) => (
            <button
              type="button"
              key={kind}
              className={selectedType === kind ? "active" : ""}
              onClick={() => setSelectedType(kind)}
            >
              {kind === "all" ? "全部" : kind}
            </button>
          ))}
        </div>
      </div>
      <div className="rating-grid">
        {visibleMedia.map((m, i) => (
          <div className="rating-card" key={`${m.subject_type}-${i}`}>
            <div className="rating-source">{text(m.subject_type)}</div>
            <div className="rating-score">{m.total ?? 0}</div>
            <div className="card-meta">评分 {m.rated ?? 0} · 均分 {m.avg_rating ?? "暂无"}</div>
            <div className="section-title">收藏状态</div>
            <DistributionBadges data={m.status_counts} />
            <div className="section-title">评分分布</div>
            <DistributionBadges data={m.rating_distribution} />
            <div className="section-title">年代趋势</div>
            <DistributionBadges data={m.decade_distribution} />
            <YearSparkline items={list(m.yearly_activity)} />
            <YearlyActivityList items={list(m.yearly_activity)} />
            <div className="evidence-row tight">
              {list(m.top_tags).slice(0, 7).map((tag) => <Badge key={tag.tag} tone="dim">{text(tag.tag)}</Badge>)}
            </div>
            <TagDriftList items={list(m.tag_drift)} />
            <AffinityList title="制作公司 / 开发商命中" items={list(m.studio_affinity)} />
            <AffinityList title="Staff 命中" items={list(m.staff_affinity)} />
            <AffinityList title="CV 命中" items={list(m.cv_affinity)} />
            {list<string>(m.notes).length > 0 && (
              <div className="caveats">{list<string>(m.notes).map((n, j) => <span key={j}>{n}</span>)}</div>
            )}
            <SubjectMiniList title="高分代表" items={list(m.high_rated)} />
            <SubjectMiniList title="待看/在看" items={list(m.backlog)} />
            <SubjectMiniList title="搁置/抛弃" items={list(m.on_hold_or_abandoned)} />
          </div>
        ))}
      </div>
      {!visibleMedia.length && <EmptyHint text="该媒介暂无可展示收藏数据" />}
      <div className="memory-grid">
        <div className="rating-card">
          <div className="rating-source">计划板状态</div>
          <DistributionBadges data={data.plan_summary || {}} />
        </div>
        <div className="rating-card">
          <div className="rating-source">主动周报</div>
          <div className="evidence-row tight">
            <Badge tone={weekly.enabled ? "good" : "dim"}>{weekly.enabled ? "已开启" : "未开启"}</Badge>
            <Badge tone="dim">weekday {weekly.weekday ?? "-"}</Badge>
            <Badge tone="dim">hour {weekly.hour ?? "-"}</Badge>
          </div>
        </div>
      </div>
      <div className="compact-list">
        {list<string>(data.recommendations_for_next_step).map((x, i) => <span key={i}>{x}</span>)}
      </div>
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
      {spoiler.memory_default && spoiler.memory_default !== mode && (
        <Badge tone="dim">长期默认 {spoiler.memory_default}</Badge>
      )}
      {spoiler.soft_warning && <Badge tone="warn">先标注剧透</Badge>}
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
  const visualFeedbackCount = list(memory.recent_visual_feedback).length;
  const pendingCount = list(memory.pending_write_actions).length;
  const planCount = list(memory.watch_plan).length;
  const inboxCount = list(memory.inbox).filter((x) => x.unread).length;
  const weeklyEnabled = Boolean(memory.weekly_digest_subscription?.enabled);
  const likePreview = list(memory.likes).slice(0, 3).map((x) => text(x.value, "")).filter(Boolean).join(" / ");
  const dislikePreview = list(memory.dislikes).slice(0, 3).map((x) => text(x.value, "")).filter(Boolean).join(" / ");
  return (
    <div className="memory-state">
      <Badge tone="dim">记忆: {text(memory.username, "未绑定")}</Badge>
      <Badge tone={likeCount ? "good" : "dim"}>喜欢 {likeCount}{likePreview ? ` · ${likePreview}` : ""}</Badge>
      <Badge tone={dislikeCount ? "warn" : "dim"}>避雷 {dislikeCount}{dislikePreview ? ` · ${dislikePreview}` : ""}</Badge>
      {feedbackCount > 0 && <Badge tone="dim">反馈 {feedbackCount}</Badge>}
      {visualFeedbackCount > 0 && <Badge tone="dim">视觉纠错 {visualFeedbackCount}</Badge>}
      {pendingCount > 0 && <Badge tone="warn">待确认 {pendingCount}</Badge>}
      {planCount > 0 && <Badge tone="good">计划 {planCount}</Badge>}
      {weeklyEnabled && <Badge tone="good">周报已订阅</Badge>}
      {inboxCount > 0 && <Badge tone="warn">未读周报 {inboxCount}</Badge>}
      {memory.spoiler_default && memory.spoiler_default !== "none" && (
        <Badge tone={memory.spoiler_default === "full" ? "bad" : "warn"}>默认剧透 {memory.spoiler_default}</Badge>
      )}
    </div>
  );
}

function MemoryPanel({
  data,
  onConfirmAction,
  onCancelAction,
  onUndoAction,
}: {
  data: MemoryState;
  onConfirmAction?: (id: string) => void;
  onCancelAction?: (id: string) => void;
  onUndoAction?: (id: string) => void;
}) {
  const likes = list(data.likes);
  const dislikes = list(data.dislikes);
  const feedback = list(data.recent_feedback);
  const visualFeedback = list(data.recent_visual_feedback);
  const pendingActions = list(data.pending_write_actions);
  const decisions = list(data.recent_decisions);
  const watchPlan = list(data.watch_plan);
  const recLists = list(data.recommendation_lists);
  const inbox = list(data.inbox);
  const weeklySub = data.weekly_digest_subscription || {};
  const progress = data.progress || {};
  const progressEntries = Object.entries(progress).slice(0, 12);
  const profiles = Object.entries(data.profile_snapshot || {}).slice(0, 3);
  const aspectProfiles = Object.entries(data.aspect_profiles || {}).slice(0, 4);
  return (
    <Panel
      title={`长期记忆 · ${text(data.username, "unknown")}`}
      subtitle={`喜欢 ${likes.length} · 避雷 ${dislikes.length} · 反馈 ${feedback.length} · 视觉纠错 ${visualFeedback.length}`}
    >
      <div className="evidence-row">
        <Badge tone="dim">spoiler_default: {text(data.spoiler_default, "none")}</Badge>
        {data.updated_at && <Badge tone="dim">updated {data.updated_at}</Badge>}
        {pendingActions.length > 0 && <Badge tone="warn">待确认写回 {pendingActions.length}</Badge>}
        {watchPlan.length > 0 && <Badge tone="good">计划板 {watchPlan.length}</Badge>}
        {weeklySub.enabled && <Badge tone="good">周报 {weeklySub.weekday ?? "-"} / {weeklySub.hour ?? "-"} 点</Badge>}
        {inbox.filter((x) => x.unread).length > 0 && <Badge tone="warn">未读 inbox {inbox.filter((x) => x.unread).length}</Badge>}
      </div>

      {pendingActions.length > 0 && (
        <>
          <div className="section-title">待确认动作</div>
          <div className="action-list">
            {pendingActions.map((action, i) => (
              <div className="action-card" key={`${action.id}-${i}`}>
                <div>
                  <div className="card-title">{text(action.summary)}</div>
                  <div className="card-meta">
                    {text(action.operation)} · {text(action.subject_name || action.subject_id, "未知条目")}
                  </div>
                  <div className="card-meta">
                    {action.operation === "push_downloader" ? "等待你确认后才会推送到下载器" : "等待你确认后才会写回 Bangumi"}
                  </div>
                </div>
                <div className="action-buttons">
                  {onConfirmAction && (
                    <button className="chip action-confirm" onClick={() => onConfirmAction(text(action.id, ""))}>
                      {action.operation === "push_downloader" ? "确认推送" : "确认写回"}
                    </button>
                  )}
                  {onCancelAction && <button className="chip" onClick={() => onCancelAction(text(action.id, ""))}>取消</button>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

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

      {visualFeedback.length > 0 && (
        <>
          <div className="section-title">视觉识别反馈</div>
          <div className="compact-list">
            {visualFeedback.slice(-8).map((item, i) => (
              <span key={`${item.id}-${i}`}>
                {text(item.predicted_subject_name || item.predicted_title, "候选")} · {text(item.signal)}
                <small> · {pct(item.confidence)}{item.note ? ` · ${text(item.note)}` : ""}</small>
              </span>
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

      {watchPlan.length > 0 && (
        <>
          <div className="section-title">计划板</div>
          <div className="rating-grid">
            {watchPlan.slice(0, 8).map((item, i) => (
              <div className="rating-card" key={`${item.subject_id}-${i}`}>
                <div className="rating-source">{text(item.status)} · priority {item.priority ?? "-"}</div>
                <div className="card-title">{text(item.name || item.subject_id)}</div>
                <p className="card-note">{text(item.reason, "")}</p>
                <div className="evidence-row tight">
                  {list<string>(item.tags).slice(0, 5).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {(weeklySub.enabled || inbox.length > 0) && (
        <>
          <div className="section-title">周报订阅 / Inbox</div>
          <div className="rating-grid">
            <div className="rating-card">
              <div className="rating-source">订阅状态</div>
              <div className="card-title">{weeklySub.enabled ? "已开启" : "未开启"}</div>
              <p className="card-note">
                weekday {weeklySub.weekday ?? "-"} · hour {weeklySub.hour ?? "-"} · {text(weeklySub.timezone, "Asia/Shanghai")}
              </p>
              <div className="evidence-row tight">
                {list<string>(weeklySub.channels).map((ch) => <Badge key={ch} tone="dim">{ch}</Badge>)}
                {weeklySub.email && <Badge tone="dim">email</Badge>}
                {weeklySub.webhook_url && <Badge tone="dim">webhook</Badge>}
              </div>
              {weeklySub.last_run_key && <Badge tone="dim">last {weeklySub.last_run_key}</Badge>}
              {list(weeklySub.last_delivery).length > 0 && (
                <div className="compact-list">
                  {list(weeklySub.last_delivery).slice(-4).map((d, j) => (
                    <span key={j}>{text(d.channel)} · {d.ok ? "ok" : text(d.error, "failed")}</span>
                  ))}
                </div>
              )}
            </div>
            {inbox.slice().reverse().slice(0, 5).map((item, i) => {
              const payload = item.payload || {};
              return (
                <div className="rating-card" key={`${item.id}-${i}`}>
                  <div className="rating-source">{item.unread ? "未读" : "已读"} · {text(item.created_at, "")}</div>
                  <div className="card-title">{text(item.title)}</div>
                  <p className="card-note">
                    {text(payload.week, "")}
                    {list(payload.sections).length ? ` · ${list(payload.sections).map((s) => text(s.title)).join(" / ")}` : ""}
                  </p>
                </div>
              );
            })}
          </div>
        </>
      )}

      {decisions.length > 0 && (
        <>
          <div className="section-title">近期决策</div>
          <div className="compact-list">
            {decisions.slice().reverse().slice(0, 8).map((item, i) => (
              <span key={`${item.id}-${i}`}>
                {text(item.kind)} · {text(item.subject_name || item.subject_id, "条目")} · {text(item.reason, "")}
                {item.kind === "write" && onUndoAction && item.action_id ? (
                  <button className="inline-action" onClick={() => onUndoAction(text(item.action_id, ""))}>撤销</button>
                ) : null}
              </span>
            ))}
          </div>
        </>
      )}

      {recLists.length > 0 && (
        <>
          <div className="section-title">保存的推荐列表</div>
          <div className="compact-list">
            {recLists.slice().reverse().slice(0, 4).map((item, i) => (
              <span key={`${item.id}-${i}`}>
                {text(item.title)} · {text(item.subject_type)} · {list(item.items).length} 项
                {item.reason ? ` · ${item.reason}` : ""}
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
      <div className="compact-list" style={{ flexDirection: "column", alignItems: "stretch" }}>
        {peaks.map((p: AnyRecord, i: number) => (
          <div key={i} style={{ marginBottom: 4 }}>
            <span>第 {p.sort} 集 · {text(p.name, "")} · {p.comments} 讨论</span>
            {list<string>(p.discussion).length > 0 && (
              <div className="caveats" style={{ marginTop: 2 }}>
                {list<string>(p.discussion).map((d, j) => <span key={j}>{d}</span>)}
              </div>
            )}
          </div>
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

function ClaimCheckPanel({ data }: { data: AnyRecord }) {
  const claims = list(data.claims);
  const verifiableCount = Number(data.supported_count || 0) + Number(data.unsupported_count || 0);
  const supportLabel = verifiableCount ? `${(Number(data.support_rate || 0) * 100).toFixed(0)}%` : "N/A";
  return (
    <Panel
      title="逐条事实校验"
      subtitle={
        verifiableCount
          ? `support ${supportLabel} · supported ${data.supported_count ?? 0} · unsupported ${data.unsupported_count ?? 0}`
          : "本轮没有强 canonical 硬事实需要自动回退"
      }
    >
      <div className="metric-grid">
        <div className="metric-card">
          <div className="metric-label">支持率</div>
          <div className="metric-value">{supportLabel}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">未支持</div>
          <div className="metric-value">{data.unsupported_count ?? 0}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">不可验证</div>
          <div className="metric-value">{data.unverifiable_count ?? 0}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">需修正</div>
          <div className="metric-value">{data.needs_revision ? "是" : "否"}</div>
        </div>
      </div>
      {list<string>(data.revision_hints).length > 0 && (
        <>
          <div className="section-title">修正建议</div>
          <div className="compact-list">
            {list<string>(data.revision_hints).map((hint, i) => <span key={i}>{hint}</span>)}
          </div>
        </>
      )}
      {claims.length ? (
        <div className="claim-list">
          {claims.slice(0, 12).map((claim, i) => {
            const tone = claim.supported ? "good" : claim.severity === "block" ? "bad" : claim.severity === "warn" ? "warn" : "dim";
            return (
              <div className="claim-card" key={`${claim.text}-${i}`}>
                <div className="claim-top">
                  <Badge tone={tone}>{claim.supported ? "supported" : "unsupported"}</Badge>
                  <Badge tone="dim">{text(claim.kind)}</Badge>
                  {claim.severity && <Badge tone={tone}>{text(claim.severity)}</Badge>}
                  <Badge tone="dim">conf {pct(claim.confidence)}</Badge>
                </div>
                <p className="card-note">{text(claim.text)}</p>
                {list(claim.evidence).length > 0 ? (
                  <div className="compact-list inline">
                    {list(claim.evidence).slice(0, 3).map((ev, idx) => (
                      <span key={idx}>{text(ev.source)} · {text(ev.text, "")}</span>
                    ))}
                  </div>
                ) : (
                  <div className="card-meta">{text(claim.note, "没有命中本轮证据")}</div>
                )}
                {claim.suggestion && <div className="card-meta">建议：{text(claim.suggestion)}</div>}
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyHint text="最终答案没有切出可校验 claim" />
      )}
      {list<string>(data.caveats).length > 0 && (
        <div className="caveats">{list<string>(data.caveats).map((c, i) => <span key={i}>{c}</span>)}</div>
      )}
    </Panel>
  );
}

export function EvidencePanels({
  evidence,
  mode = "user",
  onCritique,
  onConfirmAction,
  onCancelAction,
  onUndoAction,
  onPrepareWrite,
  onPrepareDownloaderPush,
  onVisualFeedback,
  onVisualCorrectionSearch,
}: {
  evidence: EvidenceMap;
  mode?: EvidenceMode;
  onCritique?: (q: string) => void;
  onConfirmAction?: (id: string) => void;
  onCancelAction?: (id: string) => void;
  onUndoAction?: (id: string) => void;
  onPrepareWrite?: PrepareWriteHandler;
  onPrepareDownloaderPush?: PrepareDownloaderHandler;
  onVisualFeedback?: (payload: AnyRecord) => void;
  onVisualCorrectionSearch?: (query: string, subjectType?: string) => Promise<AnyRecord[]>;
}) {
  const devMode = mode === "dev";
  const review = list(evidence.review_subject);
  const taste = list(evidence.compare_user_taste);
  const season = list(evidence.season_guide_brief);
  const whereToWatch = list(evidence.where_to_watch);
  const releaseFeeds = list(evidence.get_anime_release_feeds);
  const bangumiIndex = list(evidence.get_bangumi_index);
  const recommend = list(evidence.recommend_subjects);
  const broadcastCalendar = list(evidence.get_broadcast_calendar);
  const airingProgress = list(evidence.get_airing_progress);
  const aspect = devMode ? list(evidence.build_aspect_profile) : [];
  const watchCopilot = list(evidence.plan_watch_copilot);
  const weeklyDigest = list(evidence.build_weekly_digest);
  const tasteReport = list(evidence.build_taste_report);
  const dashboard = list(evidence.build_collection_dashboard);
  const explorer = list(evidence.explore_voice_network);
  const episodeRadar = list(evidence.episode_buzz_radar);
  const routeImage = list(evidence.route_image_source);
  const visualText = list(evidence.extract_visual_text);
  const visualStyle = list(evidence.recommend_by_visual_style);
  const imageSource = list(evidence.search_image_source);
  const biliVideo = list(evidence.summarize_bilibili_video_content);
  const videoFrames = list(evidence.analyze_video_frames);
  const pixiv = [
    ...list(evidence.get_pixiv_ranking),
    ...list(evidence.search_pixiv_illusts),
    ...list(evidence.get_pixiv_artist_portfolio),
  ];
  const trending = list(evidence.get_trending_subjects);
  const inbox = list(evidence.list_weekly_digest_inbox);
  const claimChecks = devMode ? list(evidence.claim_check) : [];
  const memoryEvidence = [
    ...list(evidence.get_user_memory),
    ...list(evidence.remember_user_preference),
    ...list(evidence.forget_user_memory),
    ...list(evidence.record_recommendation_feedback),
    ...list(evidence.prepare_bangumi_write_action),
    ...list(evidence.prepare_downloader_push),
    ...list(evidence.cancel_bangumi_write_action),
    ...list(evidence.upsert_watch_plan_item),
    ...list(evidence.list_watch_plan),
    ...list(evidence.record_decision_log),
    ...list(evidence.save_recommendation_list),
  ];
  const memory = devMode ? memoryEvidence : memoryEvidence.filter(hasActionableMemory);
  if (
    !review.length && !taste.length && !season.length && !recommend.length && !broadcastCalendar.length && !airingProgress.length && !memory.length
    && !whereToWatch.length && !releaseFeeds.length && !bangumiIndex.length
    && !aspect.length && !watchCopilot.length && !weeklyDigest.length && !tasteReport.length && !dashboard.length && !explorer.length
    && !episodeRadar.length && !routeImage.length && !visualText.length && !visualStyle.length && !imageSource.length
    && !biliVideo.length && !videoFrames.length && !claimChecks.length
    && !pixiv.length && !trending.length && !inbox.length
  ) return null;
  return (
    <div className={`evidence-stack ${devMode ? "dev-mode" : "user-mode"}`}>
      {routeImage.map((data, i) => (
        <RouteImageSourcePanel
          data={data}
          onVisualFeedback={onVisualFeedback}
          onVisualCorrectionSearch={onVisualCorrectionSearch}
          key={`route-image-${i}`}
        />
      ))}
      {visualText.map((data, i) => <VisualTextPanel data={data} key={`visual-text-${i}`} />)}
      {visualStyle.map((data, i) => <VisualStylePanel data={data} key={`visual-style-${i}`} />)}
      {imageSource.map((data, i) => <ImageSourcePanel data={data} key={`image-source-${i}`} />)}
      {biliVideo.map((data, i) => <BiliVideoContentPanel data={data} key={`bili-video-${i}`} />)}
      {videoFrames.map((data, i) => <VideoFramePanel data={data} key={`video-frames-${i}`} />)}
      {pixiv.map((data, i) => <PixivPanel data={data} key={`pixiv-${i}`} />)}
      {trending.map((data, i) => <TrendingPanel data={data} key={`trending-${i}`} />)}
      {inbox.map((data, i) => <InboxPanel data={data} key={`inbox-${i}`} />)}
      {broadcastCalendar.map((data, i) => <BroadcastCalendarPanel data={data} onPrepareWrite={onPrepareWrite} key={`broadcast-${i}`} />)}
      {airingProgress.map((data, i) => <AiringProgressPanel data={data} key={`airing-progress-${i}`} />)}
      {whereToWatch.map((data, i) => <WhereToWatchPanel data={data} key={`watch-${i}`} />)}
      {releaseFeeds.map((data, i) => (
        <ReleaseFeedsPanel data={data} onPrepareDownloaderPush={onPrepareDownloaderPush} key={`release-${i}`} />
      ))}
      {bangumiIndex.map((data, i) => <BangumiIndexPanel data={data} onPrepareWrite={onPrepareWrite} key={`index-${i}`} />)}
      {recommend.map((data, i) => <RecommendPanel data={data} onCritique={onCritique} onPrepareWrite={onPrepareWrite} key={`recommend-${i}`} />)}
      {season.map((data, i) => <SeasonGuidePanel data={data} onPrepareWrite={onPrepareWrite} key={`season-${i}`} />)}
      {review.map((data, i) => <ReviewEvidencePanel data={data} key={`review-${i}`} />)}
      {taste.map((data, i) => <TasteAffinityPanel data={data} key={`taste-${i}`} />)}
      {explorer.map((data, i) => <ExplorerPanel data={data} key={`explorer-${i}`} />)}
      {episodeRadar.map((data, i) => <EpisodeRadarPanel data={data} key={`ep-radar-${i}`} />)}
      {watchCopilot.map((data, i) => <WatchCopilotPanel data={data} key={`watch-copilot-${i}`} />)}
      {weeklyDigest.map((data, i) => <WeeklyDigestPanel data={data} key={`weekly-${i}`} />)}
      {dashboard.map((data, i) => <CollectionDashboardPanel data={data} key={`dashboard-${i}`} />)}
      {tasteReport.map((data, i) => <TasteReportPanel data={data} key={`taste-report-${i}`} />)}
      {aspect.map((data, i) => <AspectProfilePanel data={data} key={`aspect-${i}`} />)}
      {claimChecks.map((data, i) => <ClaimCheckPanel data={data} key={`claim-${i}`} />)}
      {memory.map((data, i) => (
        <MemoryPanel
          data={data}
          key={`memory-${i}`}
          onConfirmAction={onConfirmAction}
          onCancelAction={onCancelAction}
          onUndoAction={onUndoAction}
        />
      ))}
    </div>
  );
}
