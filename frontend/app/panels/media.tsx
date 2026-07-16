"use client";

// 作品/媒体域面板：条目发现、评价、导视、观看与资源类证据。
// 既有面板（ReviewEvidence/SeasonGuide/Recommend/BroadcastCalendar/AiringProgress/
// WhereToWatch/ReleaseFeed/BangumiIndex/Explorer/EpisodeRadar）后续搬迁至此；
// 新媒体域面板一律写在本文件。

import { useState } from "react";
import { Badge, Panel, list, text, type AnyRecord , Meta } from "./shared";
import { type ShareSnapshotHandler, type PrepareWriteHandler, type PrepareDownloaderHandler, fmtScore, clsBySignal, pct, EmptyHint, ShareSnapshotButton } from "./shared";

export function TrendingPanel({ data }: { data: AnyRecord }) {
  const items = list(data.items);
  return (
    <Panel
      title="Bangumi 全站热门"
      subtitle={`${text(data.subject_type, "anime")} · ${text(data.count, "0")} 部 · 网页版同源数据`}
    >
      {items.length === 0 && <div className="empty-hint">热门数据暂不可用（非正式端点可能变动）。</div>}
      <div className="trending-list">
        {items.map((it, i) => (
          <a key={i} className="trending-card" href={text(it.url, "#")} target="_blank" rel="noreferrer">
            <span className="trending-rank">{i + 1}</span>
            {it.image ? <img src={it.image} alt="" loading="lazy" /> : null}
            <div className="trending-meta">
              <div className="trending-title">{text(it.name_cn || it.name, "未知条目")}</div>
              <div className="trending-sub">
                {it.score ? <Badge tone="good">{it.score}</Badge> : null}
                {it.collects ? <Badge tone="dim">{it.collects} 人收藏中</Badge> : null}
                {list<string>(it.meta_tags)
                  .slice(0, 3)
                  .map((t, j) => (
                    <Badge key={j} tone="dim">
                      {t}
                    </Badge>
                  ))}
              </div>
            </div>
          </a>
        ))}
      </div>
      {list<string>(data.caveats).length > 0 && (
        <p className="card-note">{list<string>(data.caveats)[0]}</p>
      )}
    </Panel>
  );
}

export function BirthdayPanel({ data }: { data: AnyRecord }) {
  const characters = list(data.characters);
  const moegirl = list(data.moegirl_entries);
  return (
    <Panel title={`今日生日 · ${text(data.date, "")}`} subtitle={`${data.count ?? characters.length} 位 · AniList 人气卡 + 萌娘完整名单`}>
      {characters.length === 0 && moegirl.length === 0 && (
        <div className="empty-hint">今天没有收录到过生日的角色。</div>
      )}
      <div className="birthday-grid">
        {characters.map((c, i) => (
          <a
            key={i}
            className="birthday-card"
            href={text(c.bangumi_search_url || c.anilist_url, "#")}
            target="_blank"
            rel="noreferrer"
          >
            {c.image ? <img src={c.image} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
            <div className="birthday-meta">
              <div className="birthday-name">{text(c.name_native || c.name, "未知角色")}</div>
              <div className="birthday-from">{text(c.from_media, "")}</div>
              {c.favourites ? <Badge tone="dim">♥ {c.favourites}</Badge> : null}
            </div>
          </a>
        ))}
      </div>
      {moegirl.length > 0 && (
        <>
          <div className="section-title">
            萌娘完整名单（含游戏角色 / 声优 / 创作者）
            {data.moegirl_category_url && (
              <a className="inline-link" href={data.moegirl_category_url} target="_blank" rel="noreferrer"> 查看分类</a>
            )}
          </div>
          <div className="birthday-names">
            {moegirl.map((m, i) => (
              <a key={i} href={text(m.url, "#")} target="_blank" rel="noreferrer" className="birthday-tag">
                {text(m.name)}
                {m.from_media ? <small>（{text(m.from_media)}）</small> : null}
              </a>
            ))}
          </div>
        </>
      )}
      {list<string>(data.caveats).length > 0 && (
        <p className="card-note">{list<string>(data.caveats)[0]}</p>
      )}
    </Panel>
  );
}

export function PilgrimagePanel({ data }: { data: AnyRecord }) {
  const points = list(data.points);
  return (
    <Panel
      title={`圣地巡礼 · ${text(data.title)}`}
      subtitle={`${text(data.city, "多地")} · 共 ${data.count ?? points.length} 个取景点`}
    >
      <div className="evidence-row">
        <Badge tone="dim">source: anitabi 社区共建</Badge>
        {data.map_url && (
          <a className="inline-link" href={data.map_url} target="_blank" rel="noreferrer">打开完整地图 →</a>
        )}
      </div>
      <div className="pilgrimage-grid">
        {points.map((p, i) => (
          <a
            key={i}
            className="pilgrimage-card"
            href={text(p.google_maps_url || data.map_url, "#")}
            target="_blank"
            rel="noreferrer"
            title={text(p.name)}
          >
            {p.image ? <img src={p.image} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
            <div className="pilgrimage-meta">
              <div className="pilgrimage-name">{text(p.name)}</div>
              <div className="pilgrimage-sub">
                {p.episode != null && <Badge tone="dim">ep{p.episode}</Badge>}
                {p.second != null && <Badge tone="dim">{Math.floor(p.second / 60)}:{String(p.second % 60).padStart(2, "0")}</Badge>}
                {p.origin && <small>{text(p.origin)}</small>}
              </div>
            </div>
          </a>
        ))}
      </div>
      {list<string>(data.caveats).length > 0 && (
        <p className="card-note">{list<string>(data.caveats)[0]}</p>
      )}
    </Panel>
  );
}

const TRIP_TIERS: [string, string][] = [
  ["core", "目的地"],
  ["nearby", "顺路近郊"],
  ["bonus", "稍远惊喜"],
];

function TripCard({ e }: { e: AnyRecord }) {
  return (
    <a className="trip-card" href={text(e.map_url, "#")} target="_blank" rel="noreferrer">
      {e.cover ? <img src={e.cover} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
      <div className="trip-meta">
        <div className="trip-title">{text(e.title)}</div>
        <div className="trip-sub">
          <Badge tone="good">{e.point_count} 个取景点</Badge>
          {e.city && <Badge tone="dim">{text(e.city)}</Badge>}
          {e.distance_km != null && <Badge tone="warn">约 {e.distance_km}km</Badge>}
        </div>
        {list<string>(e.sample_points).length > 0 && (
          <div className="trip-samples">{list<string>(e.sample_points).join(" · ")}</div>
        )}
      </div>
    </a>
  );
}

export function PilgrimageTripPanel({ data }: { data: AnyRecord }) {
  const entries = list(data.entries);
  const hasTiers = entries.some((e) => e.tier && e.tier !== "core");
  return (
    <Panel
      title={`巡礼行程 · @${text(data.username)}`}
      subtitle={`${data.city_filter ? `目的地「${text(data.city_filter)}」 · ` : ""}检查 ${data.checked ?? 0} 部 → ${entries.length} 部有圣地数据`}
    >
      {entries.length === 0 && <div className="empty-hint">看过/在看里没有命中巡礼数据；可去掉城市过滤或换用 东京/关西 等常用目的地名重查。</div>}
      {hasTiers ? (
        TRIP_TIERS.map(([tier, label]) => {
          const group = entries.filter((e) => (e.tier || "core") === tier);
          if (!group.length) return null;
          return (
            <div key={tier}>
              <div className="section-title">{label}（{group.length}）</div>
              <div className="trip-list">
                {group.map((e, i) => <TripCard e={e} key={`${tier}-${i}`} />)}
              </div>
            </div>
          );
        })
      ) : (
        <div className="trip-list">
          {entries.map((e, i) => <TripCard e={e} key={i} />)}
        </div>
      )}
      {list<string>(data.caveats).length > 1 && (
        <p className="card-note">{list<string>(data.caveats)[1]}</p>
      )}
    </Panel>
  );
}

const COMPARE_ROWS: [string, string, (c: Record<string, any>) => string][] = [
  ["score", "评分", (c) => (c.score != null ? String(c.score) : "—")],
  ["rank", "排名", (c) => (c.rank ? `#${c.rank}` : "—")],
  ["rating_total", "评分人数", (c) => (c.rating_total ? String(c.rating_total) : "—")],
  ["doing", "在看", (c) => (c.doing != null ? String(c.doing) : "—")],
  ["collect", "看过", (c) => (c.collect != null ? String(c.collect) : "—")],
  ["dropped", "抛弃", (c) => (c.dropped != null ? String(c.dropped) : "—")],
  ["eps", "话数", (c) => (c.eps ? String(c.eps) : "—")],
  ["date", "开播", (c) => text(c.date, "—")],
];

export function ComparePanel({ data }: { data: AnyRecord }) {
  const columns = list(data.columns);
  if (columns.length < 2) return null;
  return (
    <Panel title="作品硬指标对比" subtitle={columns.map((c) => text(c.name_cn || c.name)).join(" vs ")}>
      <div className="compare-table" style={{ gridTemplateColumns: `72px repeat(${columns.length}, 1fr)` }}>
        <div className="compare-cell head" />
        {columns.map((c, i) => (
          <div className="compare-cell head" key={`head-${i}`}>
            <a href={text(c.url, "#")} target="_blank" rel="noreferrer">
              {c.image ? <img src={c.image} alt="" loading="lazy" /> : null}
              <div className="compare-name">{text(c.name_cn || c.name)}</div>
            </a>
          </div>
        ))}
        {COMPARE_ROWS.map(([key, label, fmt]) => (
          <div style={{ display: "contents" }} key={key}>
            <div className="compare-cell label">{label}</div>
            {columns.map((c, i) => (
              <div className="compare-cell" key={`${key}-${i}`}>{fmt(c)}</div>
            ))}
          </div>
        ))}
        <div className="compare-cell label">特有标签</div>
        {columns.map((c, i) => (
          <div className="compare-cell tags" key={`tags-${i}`}>
            {list<string>(c.unique_tags).slice(0, 4).map((t, j) => <Badge key={j} tone="dim">{t}</Badge>)}
          </div>
        ))}
      </div>
      {list<string>(data.shared_tags).length > 0 && (
        <div className="evidence-row">
          共同标签：{list<string>(data.shared_tags).map((t, i) => <Badge key={i} tone="good">{t}</Badge>)}
        </div>
      )}
      {list<string>(data.highlights).length > 0 && (
        <ul className="compare-highlights">
          {list<string>(data.highlights).map((h, i) => <li key={i}>{h}</li>)}
        </ul>
      )}
      {list<string>(data.caveats).length > 0 && (
        <p className="card-note">{list<string>(data.caveats)[0]}</p>
      )}
    </Panel>
  );
}

export function ReviewEvidencePanel({ data }: { data: AnyRecord }) {
  const ratings = list(data.ratings);
  const aspects = list(data.aspect_summary);
  const matrix = list(data.source_matrix);
  const groups = list(data.source_groups);
  return (
    <Panel
      title={`口碑速览 · ${text(data.title)}`}
      subtitle={text(data.subject_type)}
    >
      <div className="evidence-row">
        <Badge tone={clsBySignal(data.confidence)}>
          {String(data.confidence) === "high" ? "样本充足" : String(data.confidence) === "medium" ? "样本一般" : "样本偏少，仅供参考"}
        </Badge>
        <Badge tone={data.spoiler_level === "none" ? "good" : "warn"}>
          {data.spoiler_level === "none" ? "无剧透" : `剧透 ${text(data.spoiler_level)}`}
        </Badge>
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

      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}

export function SourceRoutingPanel({ data }: { data: AnyRecord }) {
  const layers = data.source_layers || {};
  const layerOrder = [
    ["canonical", "事实层"],
    ["metadata", "元数据层"],
    ["reputation", "口碑层"],
    ["discourse", "话语层"],
    ["navigation", "导航/资源层"],
  ];
  return (
    <Panel
      title="跨媒介源路由"
      subtitle={`${text(data.subject_type)} · ${text(data.intent)} · ${text(data.subject?.name, "未定锚")}`}
    >
      {data.decision && <p className="evidence-copy">{text(data.decision)}</p>}
      {list<string>(data.recommended_tools).length > 0 && (
        <div className="evidence-row">
          {list<string>(data.recommended_tools).map((tool) => <Badge key={tool} tone="good">{tool}</Badge>)}
        </div>
      )}
      <div className="taste-groups">
        {layerOrder.map(([key, label]) => {
          const sources = list(layers[key]);
          return (
            <div className="taste-group" key={key}>
              <div className="section-title">{label}</div>
              {sources.length ? (
                <div className="compact-list">
                  {sources.map((src, i) => (
                    <span key={`${src.name}-${i}`}>
                      <b>{text(src.name)}</b> · {text(src.role)}
                      {src.recommended_next_tool ? ` · ${src.recommended_next_tool}` : ""}
                      {src.can_answer_fact ? " · fact-ok" : ""}
                      {src.risk ? ` · risk ${src.risk}` : ""}
                    </span>
                  ))}
                </div>
              ) : <EmptyHint text="本层暂无推荐源" />}
            </div>
          );
        })}
      </div>
      {list<string>(data.blocked_uses).length > 0 && (
        <>
          <div className="section-title">禁用用法</div>
          <div className="compact-list">
            {list<string>(data.blocked_uses).map((n, i) => <span key={i}>{n}</span>)}
          </div>
        </>
      )}
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}

export function TasteAffinityPanel({ data }: { data: AnyRecord }) {
  const affinity = data.affinity || {};
  const matrix = list(data.matrix);
  const pulse = data.pulse;
  // friends_pulse 模式：好友圈聚合三榜
  if (pulse) {
    const boardNode = (title: string, items: any[], showRate: boolean) => (
      <>
        <div className="section-title">{title}</div>
        {items.length ? (
          <div className="compact-list" style={{ display: "grid", gap: 5 }}>
            {items.map((e: AnyRecord, i: number) => (
              <div key={`${title}-${i}`} style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <Badge tone={e.count >= 3 ? "good" : "dim"}>{showRate && e.avg_rate != null ? `${e.avg_rate} 分` : `${e.count} 人`}</Badge>
                <a href={`https://bgm.tv/subject/${e.subject_id}`} target="_blank" rel="noreferrer">{text(e.name)}</a>
                {e.my_status && <Badge tone="dim">我：{e.my_status}</Badge>}
                <span style={{ opacity: 0.55, fontSize: 12 }}>{list(e.friends).slice(0, 4).map((f) => `@${f}`).join(" ")}{e.count > 4 ? " …" : ""}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyHint text="暂无聚合结果" />
        )}
      </>
    );
    return (
      <Panel title={`好友圈动态 · @${text(data.username)}`} subtitle={`${text(data.subject_type)} · 聚合 ${pulse.friends_counted} 位好友的公开收藏`}>
        {boardNode("🔥 好友都在追", list(pulse.watching_hot), false)}
        {boardNode("⭐ 好友都想看", list(pulse.wishlist_hot), false)}
        {boardNode("🏆 好友圈高分（≥2 人评分）", list(pulse.top_rated), true)}
      </Panel>
    );
  }
  // friends_matrix 模式：全好友收缩排名表
  if (matrix.length) {
    return (
      <Panel title={`好友口味排名 · @${text(data.username)}`} subtitle={`${text(data.subject_type)} · 贝叶斯收缩分（防小样本虚高）`}>
        <div className="compact-list" style={{ display: "grid", gap: 6 }}>
          {matrix.map((e, i) => (
            <div key={`${e.username}-${i}`} style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <b style={{ minWidth: 22 }}>{i + 1}.</b>
              <a href={`https://bgm.tv/user/${e.username}`} target="_blank" rel="noreferrer">@{text(e.username)}</a>
              {e.shrunk_score != null ? (
                <>
                  <Badge tone={e.shrunk_score >= 70 ? "good" : e.shrunk_score >= 45 ? "dim" : "warn"}>
                    {e.shrunk_score} 分 · Lv{e.sync_level}
                  </Badge>
                  <span style={{ opacity: 0.6, fontSize: 12 }}>共同评分 {e.common_rated}{e.sync_score !== e.shrunk_score ? ` · 原始 ${e.sync_score}` : ""}</span>
                </>
              ) : (
                <span style={{ opacity: 0.6, fontSize: 12 }}>{text(e.note, "样本不足")}</span>
              )}
            </div>
          ))}
        </div>
      </Panel>
    );
  }
  const picks = list(affinity.wishlist_picks);
  const watching = list(affinity.watching_together);
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
      {affinity.sync_score != null && (
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "2px 0 10px" }}>
          <span style={{ fontSize: 26, fontWeight: 700 }}>{affinity.sync_score} 分</span>
          <Badge tone={affinity.sync_score >= 70 ? "good" : "dim"}>Lv{affinity.sync_level}</Badge>
          <span style={{ opacity: 0.65, fontSize: 12 }}>
            隐藏分同步（按各自评分分布归一）· 样本置信 {Math.round((affinity.sample_confidence || 0) * 100)}%
          </span>
        </div>
      )}
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
        {watching.length > 0 && (
          <>
            <div className="section-title">共同追新 · 双方都在看</div>
            <div className="compact-list">
              {watching.map((x: AnyRecord, i: number) => (
                <span key={`watch-${i}`}>
                  <a href={`https://bgm.tv/subject/${x.id}`} target="_blank" rel="noreferrer">{text(x.name)}</a>
                </span>
              ))}
            </div>
          </>
        )}
        {picks.length > 0 && (
          <>
            <div className="section-title">想看推荐 · TA 已看过你想看的</div>
            <div className="compact-list">
              {picks.map((x: AnyRecord, i: number) => (
                <span key={`pick-${i}`}>
                  <a href={`https://bgm.tv/subject/${x.id}`} target="_blank" rel="noreferrer">{text(x.name)}</a>
                  {" "}<Badge tone={x.peer_rate >= 8 ? "good" : "dim"}>{x.peer_rate} 分</Badge>
                </span>
              ))}
            </div>
          </>
        )}
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


export function WhereToWatchPanel({ data }: { data: AnyRecord }) {
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
              <Badge tone={src.confidence >= 0.8 ? "good" : "warn"}>{src.confidence >= 0.8 ? "对齐可靠" : "对齐存疑"}</Badge>
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
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}

export function ReleaseItemCard({
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

export function ReleaseFeedsPanel({ data, onPrepareDownloaderPush }: { data: AnyRecord; onPrepareDownloaderPush?: PrepareDownloaderHandler }) {
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
        <Badge tone={data.mapping_confidence >= 0.8 ? "good" : "warn"}>{data.mapping_confidence >= 0.8 ? "外站对齐可靠" : "外站对齐存疑"}</Badge>
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
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}

export function BangumiIndexPanel({ data, onPrepareWrite }: { data: AnyRecord; onPrepareWrite?: PrepareWriteHandler }) {
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
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}

export function SeasonGuidePanel({
  data,
  onPrepareWrite,
  onShareSnapshot,
  anchor,
}: {
  data: AnyRecord;
  onPrepareWrite?: PrepareWriteHandler;
  onShareSnapshot?: ShareSnapshotHandler;
  anchor?: string;
}) {
  const items = list(data.items);
  const anchorKey = String(anchor ?? "").trim();
  const norm = (v: any) => String(v ?? "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  const anchoredItem = anchorKey
    ? items.find((item) => (
      String(item.subject_id ?? "") === anchorKey
      || norm(item.title) === norm(anchorKey)
      || norm(item.yuc_title) === norm(anchorKey)
      || norm(item.title_jp) === norm(anchorKey)
    ))
    : null;
  if (anchorKey && !anchoredItem) return null;
  const visibleItems = anchoredItem ? [anchoredItem] : items;
  const single = Boolean(anchoredItem);
  const renderGuideRoute = (video: AnyRecord, idx: number) => {
    const hit = list(video.verified_hits)[0] || null;
    const href = hit?.url || video.url || video.up_url || "";
    return (
      <a className={`guide-route ${video.verified ? "verified" : ""}`} href={href || undefined} target={href ? "_blank" : undefined} rel={href ? "noreferrer" : undefined} key={`${video.up_name}-${idx}`}>
        <div className="guide-route-head">
          <span>{text(video.up_name)}</span>
          <Badge tone={video.verified ? "good" : video.confidence === "high" ? "warn" : "dim"}>
            {video.verified ? "已命中" : "仅导航"}
          </Badge>
        </div>
        <div className="card-meta">{text(video.positioning)}</div>
        {hit ? (
          <>
            <div className="guide-hit-title">{text(hit.title)}</div>
            <div className="card-meta">
              conf {pct(hit.match_confidence)}
              {hit.play ? ` · 播放 ${hit.play}` : ""}
              {hit.danmaku ? ` · 弹幕 ${hit.danmaku}` : ""}
            </div>
          </>
        ) : (
          <div className="card-meta">{text(video.verification_note || video.match_reason)}</div>
        )}
        {list(video.verticals).length > 0 && (
          <div className="compact-list inline">
            {list(video.verticals).slice(0, 2).map((v, j) => <span key={`${v.name}-${j}`}>{text(v.label)} {pct(v.confidence)}</span>)}
          </div>
        )}
      </a>
    );
  };
  return (
    <Panel
      title={single ? `季番导视 · ${text(anchoredItem?.title)}` : `季番导视 · ${text(data.season)}`}
      subtitle={`${data.personalized ? "已按用户画像分诊" : "非个性化导视"} · ${single ? "单部锚定" : `${items.length} 部`} · mode: ${text(data.mode, "guide")}`}
    >
      {!single && <div className="panel-actions">
        <ShareSnapshotButton
          type="season_guide"
          title={`季番导视 · ${text(data.season)}`}
          payload={data}
          onShareSnapshot={onShareSnapshot}
        />
      </div>}
      <div className="evidence-row">
        <Badge tone={data.mode === "hot" ? "warn" : "dim"}>{data.mode === "hot" ? "热播优先" : "口味导视"}</Badge>
        {list<string>(data.profile_tags).slice(0, 8).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
        {list<string>(data.focus_tags).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
      </div>
      <div className="season-grid">
        {visibleItems.map((item, i) => (
          <div className="season-card" key={`${item.subject_id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="season-noimg" />}
            <div className="season-main">
              <a className="card-title title-link" href={`https://bgm.tv/subject/${item.subject_id}`} target="_blank" rel="noreferrer">{text(item.title)}</a>
              <div className="card-meta">
                {item.bangumi_score ? `Bangumi ${item.bangumi_score}` : "暂无评分"}
                {item.broadcast ? ` · ${item.broadcast}` : ""}
              </div>
              <div className="evidence-row tight">
                <Badge tone={clsBySignal(item.fit)}>{text(item.fit)}</Badge>
                <Badge tone={item.match_confidence >= 0.8 ? "good" : item.match_confidence > 0 ? "warn" : "dim"}>
                  match {pct(item.match_confidence)}
                </Badge>
                <Badge tone={item.hotness_level === "surge" || item.hotness_level === "hot" ? "warn" : item.hotness_level === "warm" ? "dim" : "dim"}>
                  heat {text(item.hotness_level, "none")} {pct(item.hotness)}
                </Badge>
                {item.pre_air_wish != null && <Badge tone="dim">播前期待 {item.pre_air_wish}</Badge>}
              </div>
              {list(item.verticals).length > 0 && (
                <div className="evidence-row tight">
                  {list(item.verticals).slice(0, 3).map((v) => (
                    <Badge key={v.name} tone={v.confidence >= 0.75 ? "good" : v.confidence >= 0.55 ? "warn" : "dim"}>
                      {text(v.label)} {pct(v.confidence)}
                    </Badge>
                  ))}
                </div>
              )}
              <p className="card-note">{item.reason}</p>
              {item.studio && <div className="card-meta">制作：{item.studio}</div>}
              {(item.doing || item.trending_rank || item.episode_comment_peak) && (
                <div className="card-meta">
                  {item.doing ? `在看 ${item.doing}` : ""}
                  {item.trending_rank ? ` · 热门 #${item.trending_rank}` : ""}
                  {item.episode_comment_peak ? ` · 分集峰值 ${item.episode_comment_peak}` : ""}
                </div>
              )}
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
              </div>
              {list(item.guide_videos).length > 0 && (
                <div className="guide-route-list">
                  {list(item.guide_videos).slice(0, 3).map(renderGuideRoute)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      {!single && list(data.guide_videos).length > 0 && (
        <>
          <div className="section-title">季度导视源</div>
          <div className="guide-route-list global">
            {list(data.guide_videos).slice(0, 6).map(renderGuideRoute)}
          </div>
        </>
      )}
      {!single && list(data.guide_comment_digests).length > 0 && (
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
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}

export function WeekGrid({ days }: { days: AnyRecord[] }) {
  // 周视图：周一~周日 7 列时间表（追追日历/B站追番日历的形态），今天高亮
  return (
    <div className="week-grid">
      {days.map((day, i) => {
        const items = list(day.items);
        return (
          <div className={`week-col ${day.is_today ? "today" : ""}`} key={`${day.weekday_id}-${i}`}>
            <div className="week-col-head">
              {text(day.weekday_cn)}
              {day.is_today ? <Badge tone="good">今天</Badge> : null}
            </div>
            {items.map((item, idx) => (
              <a
                className={`week-cell${item.my_collection === "watching" ? " mine" : ""}`}
                href={item.url || `https://bgm.tv/subject/${item.id}`}
                target="_blank"
                rel="noreferrer"
                key={`${item.id}-${idx}`}
                title={text(item.name_cn || item.name)}
              >
                {item.image ? <img src={item.image} alt="" loading="lazy" /> : null}
                <div className="week-cell-meta">
                  <div className="week-cell-name">{text(item.name_cn || item.name)}</div>
                  <div className="week-cell-sub">
                    {item.broadcast ? <span className="week-slot">{text(item.broadcast)}</span> : null}
                    {item.my_collection_label ? <Badge tone={item.my_collection === "watching" ? "good" : "dim"}>{text(item.my_collection_label)}</Badge> : null}
                  </div>
                </div>
              </a>
            ))}
            {items.length === 0 && <div className="week-empty">—</div>}
          </div>
        );
      })}
    </div>
  );
}

export function BroadcastCalendarPanel({ data, onPrepareWrite }: { data: AnyRecord; onPrepareWrite?: PrepareWriteHandler }) {
  const days = list(data.days);
  if (data.scope === "week" && days.length > 1) {
    return (
      <Panel
        title="本周放送时间表"
        subtitle={`${text(data.today)} · ${data.count ?? 0} 部${data.only_mine ? ` · @${text(data.username)}` : ""} · 档期来自 yuc（日本时间）`}
      >
        <WeekGrid days={days} />
        {list<string>(data.notes).length > 0 && (
          <p className="card-note">{list<string>(data.notes)[0]}</p>
        )}
      </Panel>
    );
  }
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
                            {item.broadcast || item.air_date || "日期未定"}
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
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}

export function AiringProgressPanel({ data }: { data: AnyRecord }) {
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
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}


export function EpisodeRadarPanel({ data }: { data: AnyRecord }) {
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
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}

export function ExplorerPanel({ data }: { data: AnyRecord }) {
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
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}




export function SubjectTrendPanel({ data }: { data: AnyRecord }) {
  const pts = list(data.points).filter((p: AnyRecord) => p.score != null);
  const W = 560, H = 150, PAD = 34;
  const scores = pts.map((p: AnyRecord) => Number(p.score));
  const collects = list(data.points).map((p: AnyRecord) => Number(p.collect_total || 0));
  const sMin = Math.min(...scores, 10), sMax = Math.max(...scores, 0);
  const cMax = Math.max(...collects, 1);
  const x = (i: number, n: number) => PAD + (i / Math.max(1, n - 1)) * (W - PAD * 2);
  const yScore = (v: number) => H - 22 - ((v - sMin) / Math.max(0.1, sMax - sMin)) * (H - 44);
  const yCollect = (v: number) => H - 22 - (v / cMax) * (H - 44);
  const scoreLine = pts.map((p: AnyRecord, i: number) => `${x(i, pts.length).toFixed(1)},${yScore(Number(p.score)).toFixed(1)}`).join(" ");
  const allPts = list(data.points);
  const collectLine = allPts.map((p: AnyRecord, i: number) => `${x(i, allPts.length).toFixed(1)},${yCollect(Number(p.collect_total || 0)).toFixed(1)}`).join(" ");
  const chg = (v: any) => (v == null ? null : (
    <Badge tone={Number(v) >= 0 ? "good" : "warn"}>{Number(v) >= 0 ? "+" : ""}{v}</Badge>
  ));
  return (
    <Panel
      title={`口碑走势 · ${text(data.title)}`}
      subtitle={`netaba.re 每日快照 · ${text(data.first_recorded)} ~ ${text(data.last_recorded)}`}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", marginBottom: 6 }}>
        {data.current_score != null && <span style={{ fontSize: 22, fontWeight: 700 }}>{data.current_score}</span>}
        {data.score_change_30d != null && <span style={{ fontSize: 12, opacity: 0.75 }}>30天 {chg(data.score_change_30d)}</span>}
        {data.score_change_90d != null && <span style={{ fontSize: 12, opacity: 0.75 }}>90天 {chg(data.score_change_90d)}</span>}
        {data.pre_air_wish != null && <span style={{ fontSize: 12, opacity: 0.75 }}>开播前想看 {data.pre_air_wish} 人</span>}
      </div>
      {pts.length >= 2 && (
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }} aria-label="评分与收藏走势">
          <polyline points={collectLine} fill="none" stroke="#5b6b8c" strokeWidth="1.4" opacity="0.55" />
          <polyline points={scoreLine} fill="none" stroke="#7c8cff" strokeWidth="2" />
          <text x={PAD} y={H - 6} fontSize="10" fill="currentColor" opacity="0.6">{text(pts[0]?.date)}</text>
          <text x={W - PAD} y={H - 6} fontSize="10" fill="currentColor" opacity="0.6" textAnchor="end">{text(pts[pts.length - 1]?.date)}</text>
          <text x={PAD - 4} y={yScore(sMax) + 4} fontSize="10" fill="#7c8cff" textAnchor="end">{sMax.toFixed(1)}</text>
          <text x={PAD - 4} y={yScore(sMin) + 4} fontSize="10" fill="#7c8cff" textAnchor="end">{sMin.toFixed(1)}</text>
        </svg>
      )}
      {Object.keys(data.rating_distribution || {}).length > 0 && (() => {
        const dist = data.rating_distribution as Record<string, number>;
        const maxN = Math.max(...Object.values(dist), 1);
        return (
          <div style={{ marginTop: 8 }}>
            <div className="section-title">评分分布{data.rating_std != null ? ` · 标准差 ${data.rating_std}` : ""}{data.controversy ? ` · ${data.controversy}` : ""}</div>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 72 }}>
              {Array.from({ length: 10 }, (_, i) => 10 - i).map((r) => {
                const n = Number(dist[String(r)] || 0);
                return (
                  <div key={r} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }} title={`${r} 分：${n} 人`}>
                    <div style={{ width: "100%", height: `${Math.max(2, (n / maxN) * 56)}px`, background: "#7c8cff", opacity: 0.35 + 0.65 * (n / maxN), borderRadius: 2 }} />
                    <span style={{ fontSize: 10, opacity: 0.6 }}>{r}</span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}
      <div style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
        <span style={{ color: "#7c8cff" }}>━ 均分</span>　<span style={{ color: "#5b6b8c" }}>━ 收藏总数（归一）</span>
        　<a href={text(data.netabare_url)} target="_blank" rel="noreferrer">netaba.re 详情 →</a>
      </div>
      {list(data.caveats).map((c, i) => <div className="card-note" key={i}>{text(c)}</div>)}
    </Panel>
  );
}


export function RatingMoversPanel({ data }: { data: AnyRecord }) {
  const boards: [string, string, AnyRecord[]][] = [
    ["📈 口碑上涨", "good", list(data.up)],
    ["📉 口碑下跌", "warn", list(data.down)],
    ["🏁 近期完结", "dim", list(data.done)],
  ];
  const analysis = data.season_analysis || {};
  const sections: [string, string][] = [["score", "评分格局"], ["rank", "排名变化"], ["divisive", "争议作品"], ["popularity", "热度观察"]];
  return (
    <Panel title="口碑异动榜 · 近 30 天" subtitle="netaba.re 每日快照 · 第三方数据">
      {boards.map(([title, tone, items]) => items.length > 0 && (
        <div key={title}>
          <div className="section-title">{title}</div>
          <div className="compact-list" style={{ display: "grid", gap: 4 }}>
            {items.map((e: AnyRecord, i: number) => (
              <div key={`${title}-${i}`} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                <Badge tone={tone}>{e.delta_score > 0 ? "+" : ""}{e.delta_score}</Badge>
                <a href={`https://bgm.tv/subject/${e.subject_id}`} target="_blank" rel="noreferrer">{text(e.name || e.title)}</a>
                {e.current_score != null && <span style={{ opacity: 0.6, fontSize: 12 }}>现 {e.current_score}（{e.rating_total} 人）</span>}
              </div>
            ))}
          </div>
        </div>
      ))}
      {sections.some(([k]) => analysis[k]) && (
        <>
          <div className="section-title">当季评分格局（netaba.re AI 分析，第三方观点）</div>
          {sections.map(([k, label]) => analysis[k] && (
            <p className="card-note" key={k} style={{ whiteSpace: "pre-wrap" }}><b>{label}：</b>{analysis[k]}</p>
          ))}
        </>
      )}
      {list(data.caveats).map((c, i) => <div className="card-note" key={`c-${i}`}>{text(c)}</div>)}
    </Panel>
  );
}


export function OmikujiPanel({ data }: { data: AnyRecord }) {
  const tone = data.fortune === "大吉" ? "good" : data.fortune === "末吉" ? "warn" : "dim";
  return (
    <Panel title={`今日番签 · ${text(data.date)}`} subtitle={data.from_pool === "wishlist" ? "抽自你的想看列表" : "抽自经典池"}>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        {data.image && <img src={text(data.image)} alt="" style={{ width: 88, borderRadius: 8 }} />}
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span style={{ fontSize: 30, fontWeight: 800 }}>{text(data.fortune)}</span>
            <Badge tone={tone}>幸运标签：{text(data.lucky_tag)}</Badge>
          </div>
          <div style={{ marginTop: 6, fontSize: 16 }}>
            今日之番：<a href={`https://bgm.tv/subject/${data.subject_id}`} target="_blank" rel="noreferrer"><b>{text(data.subject_name)}</b></a>
          </div>
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            {list<string>(data.advice).map((a, i) => <li key={i} style={{ fontSize: 13, opacity: 0.85 }}>{a}</li>)}
          </ul>
        </div>
      </div>
      <div className="card-note">同一天重复抽签结果不变——今日运势只有一次。</div>
    </Panel>
  );
}

export function QuizPanel({ data }: { data: AnyRecord }) {
  const questions = list(data.questions);
  const [picked, setPicked] = useState<Record<number, number>>({});
  const answered = Object.keys(picked).length;
  const correct = questions.reduce((n: number, q: AnyRecord, i: number) => n + (picked[i] === q.answer_index ? 1 : 0), 0);
  return (
    <Panel title="ACGN 小测验" subtitle={data.source === "my_watched" ? "题目出自你看过的作品" : "经典池出题"}>
      {questions.map((q: AnyRecord, qi: number) => {
        const done = picked[qi] !== undefined;
        return (
          <div key={qi} style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>{qi + 1}. {text(q.q)}</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {list<string>(q.options).map((opt, oi) => {
                const isAnswer = oi === q.answer_index;
                const isPicked = picked[qi] === oi;
                const style: Record<string, string | number> = {
                  padding: "4px 12px", borderRadius: 8, cursor: done ? "default" : "pointer",
                  border: "1px solid var(--border, #2a2a32)", background: "transparent", color: "inherit", fontSize: 13,
                };
                if (done && isAnswer) { style.borderColor = "#4ade80"; style.background = "rgba(74,222,128,.15)"; }
                else if (done && isPicked) { style.borderColor = "#f87171"; style.background = "rgba(248,113,113,.15)"; }
                return (
                  <button key={oi} style={style} disabled={done}
                    onClick={() => setPicked((p) => ({ ...p, [qi]: oi }))}>{opt}</button>
                );
              })}
            </div>
            {done && <div className="card-note" style={{ marginTop: 4 }}>{picked[qi] === q.answer_index ? "✅ " : "❌ "}{text(q.explain)}</div>}
          </div>
        );
      })}
      {answered === questions.length && questions.length > 0 && (
        <div style={{ fontWeight: 700, fontSize: 15 }}>
          🎉 {correct}/{questions.length} 正确{correct === questions.length ? " —— 全对，浓度惊人！" : correct >= questions.length / 2 ? " —— 有两把刷子" : " —— 该补番了"}
        </div>
      )}
    </Panel>
  );
}


export function EpisodeProgressPanel({ data }: { data: AnyRecord }) {
  const eps = list(data.episodes);
  const total = Number(data.total_main || eps.length || 0);
  const watched = Number(data.watched || 0);
  const ratio = total > 0 ? Math.round((watched / total) * 100) : 0;
  return (
    <Panel
      title={`追番进度 · ${text(data.subject_name)}`}
      subtitle={data.next_episode != null ? `下一集：第 ${data.next_episode} 集` : "本篇已全部看完 🎉"}
    >
      <div className="stat-row">
        <span className="stat-big good"><span className="stat-value">{data.watched_up_to ?? 0}</span><span className="stat-label">看到第几集</span></span>
        <span className="stat-big"><span className="stat-value">{watched}/{total}</span><span className="stat-label">已看集数</span></span>
        <span className="stat-big"><span className="stat-value">{ratio}%</span><span className="stat-label">完成度</span></span>
      </div>
      <div className="ep-strip">
        {eps.map((e, i) => (
          <span
            key={i}
            className={`ep-cell ${e.status === "看过" ? "done" : e.status === "抛弃" ? "drop" : ""}`}
            title={`第 ${e.sort} 集 ${e.name || ""} · ${e.status}`}
          >
            {Math.round(Number(e.sort))}
          </span>
        ))}
      </div>
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}


export function CsvExportPanel({ data }: { data: AnyRecord }) {
  const download = () => {
    const blob = new Blob([text(data.csv_text, "")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = text(data.filename, "bangumi_collections.csv");
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <Panel title={`收藏导出 · @${text(data.username)}`} subtitle={`${data.count ?? 0} 条记录已生成`}>
      <div className="evidence-row">
        <button type="button" className="inline-action" onClick={download}>⬇ 下载 {text(data.filename, "CSV")}</button>
      </div>
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}
