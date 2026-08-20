"use client";

// 作品/媒体域面板：条目发现、评价、导视、观看与资源类证据。
// 既有面板（ReviewEvidence/SeasonGuide/Recommend/BroadcastCalendar/AiringProgress/
// WhereToWatch/ReleaseFeed/BangumiIndex/Explorer/EpisodeRadar）后续搬迁至此；
// 新媒体域面板一律写在本文件。

import { useState } from "react";
import { Badge, Panel, list, text, type AnyRecord , Meta } from "./shared";
import { type ShareSnapshotHandler, type PrepareWriteHandler, type PrepareDownloaderHandler, type PrepareRssFollowHandler, fmtScore, clsBySignal, pct, EmptyHint, ShareSnapshotButton } from "./shared";
import { AnimeMusicThemesPanel } from "./product";

const RELEASE_SOURCE_LABEL: Record<string, string> = {
  mikan: "Mikan",
  dmhy: "动漫花园",
  acgnx: "末日资源库",
  vcb: "VCB-Studio",
};

function releaseSourceLabel(value: unknown) {
  const key = text(value).toLowerCase();
  return RELEASE_SOURCE_LABEL[key] || text(value, "来源待确认");
}

export function TrendingPanel({ data }: { data: AnyRecord }) {
  const items = list(data.items);
  return (
    <Panel
      title="Bangumi 全站热门"
      subtitle={`${text(data.subject_type, "anime")} · ${text(data.count, "0")} 部 · 实时热度`}
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

export function TodayCockpitPanel({ data, onPrepareWrite }: { data: AnyRecord; onPrepareWrite?: PrepareWriteHandler }) {
  const items = list(data.today);
  const backlog = list(data.backlog);
  return (
    <Panel title={`今日追番 · ${text(data.date)}`} subtitle={`${items.length} 部今日放送 · ${backlog.length} 部落后候选`}>
      <div className="today-panel-list">
        {items.length ? items.map((item, index) => (
          <div className="today-panel-item" key={`${item.id}-${index}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
            <div>
              <a className="card-title title-link" href={item.url || `https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer">{text(item.name_cn || item.name)}</a>
              <div className="card-meta">{text(item.broadcast, "放送时间未定")} · 看到 {item.my_ep ?? 0}{item.aired_ep ? ` / 已播 ${item.aired_ep}` : ""}</div>
              <div className="evidence-row tight">
                {item.pinned ? <Badge tone="good">置顶</Badge> : null}
                {item.behind ? <Badge tone="warn">落后 {item.behind} 集</Badge> : <Badge tone="good">已跟上</Badge>}
                {onPrepareWrite && Number(item.my_ep || 0) < Number(item.aired_ep || 0) ? (
                  <button className="inline-action card-action" onClick={() => onPrepareWrite(
                    Number(item.id), text(item.name_cn || item.name), 3,
                    { operation: "mark_episodes_watched", upToEpisode: Number(item.my_ep || 0) + 1 },
                  )}>看完下一集</button>
                ) : null}
              </div>
            </div>
          </div>
        )) : <EmptyHint text="今天没有你的在看/想看条目" />}
      </div>
      <div className="panel-actions"><a className="ghost" href="/today">打开完整今日页</a></div>
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}

export function BirthdayPanel({ data }: { data: AnyRecord }) {
  const characters = list(data.characters);
  const moegirl = list(data.moegirl_entries);
  return (
    <Panel title={`今日生日 · ${text(data.date, "")}`} subtitle={`${data.count ?? characters.length} 位角色今天过生日`}>
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
        <Badge tone="dim">数据来自巡礼社区 anitabi</Badge>
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
      <Panel title={`好友口味排名 · @${text(data.username)}`} subtitle={`${text(data.subject_type)} · 综合排名（共同评分少的自动降权）`}>
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
    <div id="watch-online" className="watch-hub-anchor">
      <Panel
        title={`正版观看 · ${text(data.title)}`}
        subtitle={`${official.length} 个官方候选 · ${fallbacks.length} 个搜索兜底`}
      >
      <div className="evidence-row">
        <Badge tone={data.availability_status === "verified" ? "good" : data.availability_status === "not_found" || data.availability_status === "unavailable" ? "warn" : "dim"}>
          {text(data.availability_label, official.length ? "有官方候选" : "未找到可靠正版入口")}
        </Badge>
        {data.last_verified ? <Badge tone="dim">核验于 {String(data.last_verified).slice(0, 16).replace("T", " ")}</Badge> : null}
        {data.offline_hint && <Badge tone="dim">可继续查 RSS/BD</Badge>}
      </div>
      {data.availability_note ? <p className="evidence-copy">{text(data.availability_note)}</p> : null}
      {official.length ? (
        <div className="rating-grid">
          {official.map((src, i) => (
            <a className="rating-card" href={src.url} target="_blank" rel="noreferrer" key={`${src.url}-${i}`}>
              <div className="rating-source">{text(src.label)}</div>
              <div className="card-meta">{list<string>(src.regions).join("/") || "地区未注明"} · {text(src.availability_label, "可用性待确认")}</div>
              <Badge tone={src.availability_status === "verified" ? "good" : src.availability_status === "catalog_match" ? "dim" : "warn"}>
                {src.availability_status === "verified" ? "平台实时核验" : src.availability_status === "catalog_match" ? "官方目录命中" : "打开后确认"}
              </Badge>
              {src.note && <p className="card-note">{src.availability_status === "catalog_match" ? "平台官方页面" : text(src.note)}</p>}
              {src.availability_note ? <p className="card-note">{src.availability_status === "catalog_match" ? "目录信息可能随地区版权或下架状态变化，打开后请再确认。" : text(src.availability_note)}</p> : null}
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
      <Meta notes={[...list<string>(data.mapping_notes), ...list<string>(data.caveats)]} />
    </Panel>
    </div>
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
  const sizeBytes = Number(item.size_bytes || 0);
  const sizeLabel = sizeBytes >= 1024 ** 3
    ? `${(sizeBytes / 1024 ** 3).toFixed(1)} GB`
    : sizeBytes >= 1024 ** 2 ? `${Math.round(sizeBytes / 1024 ** 2)} MB` : "";
  return (
    <div className="release-item">
      <div className="release-item-head">
        {item.subgroup && <Badge tone="good">{text(item.subgroup)}</Badge>}
        <Badge tone="dim">{releaseSourceLabel(item.source)}</Badge>
        {item.resolution ? <Badge tone="dim">{text(item.resolution)}</Badge> : null}
        {item.subtitle ? <Badge tone="dim">{text(item.subtitle)}</Badge> : null}
        {item.episode_label ? <Badge tone="good">{text(item.episode_label)}</Badge> : null}
        {item.release_kind ? <Badge tone="dim">{item.release_kind === "episode" ? "单集" : item.release_kind === "batch" ? "合集" : item.release_kind === "bd" ? "BD" : item.release_kind === "movie" ? "电影" : "类型待确认"}</Badge> : null}
        {item.quality && item.quality !== "tv" && !item.resolution && <Badge tone="warn">{String(item.quality).toLowerCase() === "bd" ? "BD" : text(item.quality)}</Badge>}
        {sizeLabel ? <Badge tone="dim">{sizeLabel}</Badge> : null}
        {item.pub_date && <span className="release-date">{String(item.pub_date).slice(0, 10)}</span>}
        {item.scope_status === "exact" && <Badge tone="good">当前篇章</Badge>}
        {item.scope_status === "compatible" && <Badge tone="dim">未发现篇章冲突</Badge>}
        {item.scope_status === "bundle" && <Badge tone="warn">跨季/合集</Badge>}
        {item.scope_status === "conflict" && <Badge tone="warn">其他篇章/内容</Badge>}
        {item.scope_status === "unknown" && <Badge tone="warn">身份待确认</Badge>}
      </div>
      <div className="release-item-title" title={text(item.title)}>{text(item.title)}</div>
      {item.scope_reason && <div className="card-meta">{text(item.scope_reason)}</div>}
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

export function ReleaseFeedsPanel({ data, onPrepareDownloaderPush, onCreateRssFollow }: { data: AnyRecord; onPrepareDownloaderPush?: PrepareDownloaderHandler; onCreateRssFollow?: PrepareRssFollowHandler }) {
  const groups = list(data.groups);
  const fallback = list(data.fallback_items);
  const related = list(data.related_items);
  const links = list(data.search_links);
  const subjectId = data.subject_id ? Number(data.subject_id) : undefined;
  const [rssNotice, setRssNotice] = useState("");
  return (
    <div id="watch-release" className="watch-hub-anchor">
      <Panel
        title={`离线资源/RSS · ${text(data.title)}`}
        subtitle={`${groups.length} 个RSS/收藏分组 · ${fallback.length} 条当前篇章兜底 · ${Number(data.filtered_count || related.length)} 条移入确认区`}
      >
      <div className="evidence-row">
        <Badge tone={data.mapping_confidence >= 0.8 ? "good" : "warn"}>{data.mapping_confidence >= 0.8 ? "外站对齐可靠" : "外站对齐存疑"}</Badge>
        <Badge tone="warn">仅提供外部链接</Badge>
      </div>
      {rssNotice ? <div className="inline-notice">{rssNotice}</div> : null}
      {groups.length ? (
        <div className="digest-list">
          {groups.map((group, i) => (
            <div className="digest-card" key={`${group.source}-${group.subgroup}-${i}`}>
              <div className="release-group-head">
                <span className="digest-title">{text(group.subgroup)}</span>
                <Badge tone="dim">{releaseSourceLabel(group.source)}</Badge>
                {group.quality && group.quality !== "tv" && <Badge tone="warn">{String(group.quality).toLowerCase() === "bd" ? "BD" : text(group.quality)}</Badge>}
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
                    {onCreateRssFollow ? (
                      <button
                        type="button"
                        className="inline-action"
                        onClick={async () => {
                          setRssNotice("正在创建真实追更规则…");
                          try {
                            await onCreateRssFollow({
                              rss_url: group.rss_url,
                              title: text(data.title),
                              subgroup: text(group.subgroup),
                              subject_id: subjectId,
                              source: text(group.source),
                            });
                            setRssNotice(`已追更 ${text(group.subgroup)}；默认每小时检查并写入 Otomo 收件箱。`);
                          } catch (error) {
                            setRssNotice(`创建追更失败：${String(error)}`);
                          }
                        }}
                      >
                        追更这个 RSS
                      </button>
                    ) : null}
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
          <div className="section-title">资源站候选</div>
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
      {related.length > 0 && (
        <details className="pending-guide-sources release-related-items">
          <summary>相关篇章 / 合集 / 身份待确认 {Number(data.filtered_count || related.length)}</summary>
          <div className="section-copy">这些结果不会混入当前条目的默认下载区。若确实需要系列合集，请先核对标题与源站页面；推送下载器仍需再次确认。</div>
          <div className="release-list">
            {related.map((item, i) => (
              <ReleaseItemCard
                item={item}
                subjectId={subjectId}
                subjectName={text(data.title)}
                onPrepareDownloaderPush={onPrepareDownloaderPush}
                key={`${item.source}-${item.title}-${i}`}
              />
            ))}
          </div>
        </details>
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
    </div>
  );
}

export function AnimeWatchHubPanel({
  data,
  onPrepareDownloaderPush,
  onPrepareWrite,
  onPrepareProgress,
  onCreateRssFollow,
  onUpdatePreferences,
  onAddWatchPlan,
  onToggleFollow,
  isFollowing = false,
}: {
  data: AnyRecord;
  onPrepareDownloaderPush?: PrepareDownloaderHandler;
  onPrepareWrite?: PrepareWriteHandler;
  onPrepareProgress?: (subjectId: number, subjectName: string, upToEpisode: number) => void;
  onCreateRssFollow?: PrepareRssFollowHandler;
  onUpdatePreferences?: (payload: AnyRecord) => Promise<void>;
  onAddWatchPlan?: () => Promise<void>;
  onToggleFollow?: () => Promise<void>;
  isFollowing?: boolean;
}) {
  const subject = data.subject || {};
  const lifecycle = data.lifecycle || {};
  const bili = data.bilibili || {};
  const videos = list(bili.videos);
  const playableVideos = videos.filter((video) => video.watch_candidate);
  const uncertainVideos = videos.filter((video) => video.role === "episode_candidate");
  const editorialVideos = videos.filter((video) => !video.watch_candidate && video.role !== "episode_candidate");
  const versionConflicts = list(bili.version_conflicts);
  const [activeTab, setActiveTab] = useState(() => {
    if (typeof window === "undefined") return "overview";
    return ({
      "#watch-online": "online",
      "#watch-bilibili": "videos",
      "#watch-release": "releases",
    } as Record<string, string>)[window.location.hash] || "overview";
  });
  const [hubNotice, setHubNotice] = useState("");
  const viewer = data.viewer_state || {};
  const overview = data.overview || {};
  const preferences = data.preferences || {};
  const selectTab = (value: string) => {
    setActiveTab(value);
    const hash = value === "online" ? "#watch-online" : value === "videos" ? "#watch-bilibili" : value === "releases" ? "#watch-release" : "";
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
  };
  const applyPreference = async (payload: AnyRecord, success: string) => {
    if (!onUpdatePreferences) return;
    try {
      await onUpdatePreferences(payload);
      setHubNotice(success);
    } catch (error) {
      setHubNotice(`偏好保存失败：${String(error)}`);
    }
  };
  const roleLabel: Record<string, string> = {
    public_full_episode: "公开视频正片",
    episode_candidate: "疑似正片",
    official_pv: "官方/PV",
    review: "漫评",
    retrospective: "回顾/复盘",
    fan_creation: "二创",
    related: "相关视频",
  };
  const uploaderLabel: Record<string, string> = {
    platform_account: "平台动画账号",
    staff_or_production: "制作方/Staff信号",
    self_claimed_official: "官方身份待核验",
    creator: "内容创作者",
    unknown: "作者身份未知",
  };
  const formatCount = (value: unknown) => {
    const number = Number(value || 0);
    return number ? new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(number) : "";
  };
  const formatDuration = (value: unknown) => {
    const seconds = Number(value || 0);
    if (!seconds) return "";
    const minutes = Math.round(seconds / 60);
    return minutes >= 60 ? `${Math.floor(minutes / 60)}小时${minutes % 60 ? `${minutes % 60}分` : ""}` : `${minutes}分钟`;
  };
  const renderVideoCard = (video: AnyRecord, i: number) => {
    const href = video.url || "";
    const pageLinks = list(video.page_links);
    const upHref = video.mid ? `https://space.bilibili.com/${video.mid}` : "";
    const copyrightLabel = video.copyright_declaration === "repost" ? "投稿声明：转载" : video.copyright_declaration === "original" ? "投稿声明：自制" : "";
    return (
      <article className={`bili-video-card subject-video ${video.watch_candidate ? "public-upload" : ""}`} key={`${video.bvid || video.aid || href}-${i}`}>
        <a className="bili-video-cover" href={href} target="_blank" rel="noreferrer">
          {video.thumbnail_url ? <img src={video.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span><b>BILI</b><small>{roleLabel[String(video.role)] || "相关视频"}</small></span>}
          <i>{roleLabel[String(video.role)] || "相关视频"}</i>
        </a>
        <div className="bili-video-body">
          <div className="evidence-row tight">
            <Badge tone={video.watch_candidate || video.role === "episode_candidate" ? "warn" : "dim"}>{roleLabel[String(video.role)] || "相关视频"}</Badge>
            {video.watch_candidate ? <Badge tone="warn">非正版入口 · 版权未核验</Badge> : null}
            <Badge tone={video.uploader_class === "staff_or_production" || video.uploader_class === "platform_account" ? "good" : video.uploader_class === "self_claimed_official" ? "warn" : "dim"}>{uploaderLabel[String(video.uploader_class)] || "作者身份未知"}</Badge>
            <Badge tone={video.verified ? "good" : "warn"}>{video.verified ? "稿件元数据已读取" : "仅搜索元数据"}</Badge>
          </div>
          <a className="bili-video-title" href={href} target="_blank" rel="noreferrer">{text(video.title)}</a>
          <div className="bili-video-byline">
            {upHref ? <a href={upHref} target="_blank" rel="noreferrer">{text(video.author)}</a> : <span>{text(video.author)}</span>}
            {video.pubdate ? <span>{new Date(Number(video.pubdate) * 1000).toLocaleDateString("zh-CN")}</span> : null}
          </div>
          <div className="bili-video-stats">
            {video.duration_seconds ? <span>时长 {formatDuration(video.duration_seconds)}</span> : null}
            {Number(video.page_count || 0) > 1 ? <span>{video.page_count} 个分P</span> : null}
            {video.episode_coverage ? <span>{text(video.episode_coverage)}</span> : null}
            {copyrightLabel ? <span>{copyrightLabel}</span> : null}
            {video.play ? <span>播放 {formatCount(video.play)}</span> : null}
            {video.danmaku ? <span>弹幕 {formatCount(video.danmaku)}</span> : null}
            <span>作品匹配 {pct(video.match_confidence)}</span>
          </div>
          {onUpdatePreferences ? (
            <div className="panel-actions compact-actions">
              <button type="button" className="inline-action" onClick={async () => {
                setHubNotice("正在隐藏这条不相关视频…");
                await applyPreference({ video_id: String(video.bvid || video.aid || ""), video_action: "hide" }, "已隐藏；视频模块会按新偏好重新筛选。");
              }}>不相关</button>
              {video.author ? <button type="button" className="inline-action" onClick={async () => {
                await applyPreference({ uploader: String(video.author), uploader_action: "like" }, `已记录：多推荐 ${String(video.author)} 的可靠内容。`);
              }}>多来这个 UP</button> : null}
              {video.author ? <button type="button" className="inline-action" onClick={async () => {
                await applyPreference({ uploader: String(video.author), uploader_action: "mute" }, `已记录：减少 ${String(video.author)} 的内容。`);
              }}>少来这个 UP</button> : null}
            </div>
          ) : null}
          {pageLinks.length ? (
            <details className="bili-video-pages">
              <summary>按分P打开具体集 · {pageLinks.length}</summary>
              <div className="bili-page-links">
                {pageLinks.map((page, pageIndex) => (
                  <a href={page.url} target="_blank" rel="noreferrer" key={`${page.page || pageIndex}-${page.url}`}>
                    <b>P{page.page || pageIndex + 1}</b>
                    <span>{text(page.title, `第 ${page.page || pageIndex + 1} 部分`)}</span>
                    {page.duration_seconds ? <small>{formatDuration(page.duration_seconds)}</small> : null}
                  </a>
                ))}
              </div>
            </details>
          ) : null}
          <details className="bili-video-proof">
            <summary>{video.watch_candidate ? "为什么判断为可看的完整动画内容" : "分类与核验依据"}</summary>
            {list<string>(video.content_evidence).map((row, j) => <p key={`content-${j}`}>{row}</p>)}
            {list<string>(video.identity_evidence).map((row, j) => <p key={`identity-${j}`}>{row}</p>)}
            <p>{text(video.match_reason, "标题与作品别名通过一致性检查")}</p>
            <p>{text(video.caution, "打开后请核对内容")}</p>
          </details>
        </div>
      </article>
    );
  };
  return (
    <>
      <Panel title="动画观看中心" subtitle={`${text(subject.name)} · ${text(lifecycle.label, "状态待确认")}`}>
        <div className="evidence-row">
          <Badge tone={lifecycle.state === "airing" ? "good" : lifecycle.state === "upcoming" ? "warn" : "dim"}>{text(lifecycle.label)}</Badge>
          {subject.platform ? <Badge tone="dim">{text(subject.platform)}</Badge> : null}
          {subject.eps ? <Badge tone="dim">{subject.eps} 集</Badge> : null}
          {data.bilibili ? <Badge tone={playableVideos.length ? "warn" : "dim"}>B站普通投稿可看 {playableVideos.length}</Badge> : null}
        </div>
        <p className="evidence-copy">{text(lifecycle.strategy)}</p>
        <div className="watch-hub-summary">
          {list<string>(data.status_summary).map((row, i) => <span key={i}>{row}</span>)}
        </div>
        {hubNotice ? <div className="inline-notice">{hubNotice}</div> : null}
        <div className="panel-actions subject-hub-actions">
          {onPrepareWrite ? <>
            <button type="button" className={viewer.collection_type === 1 ? "button-primary" : "button-secondary"} onClick={() => onPrepareWrite(Number(subject.id), text(subject.name), 1)}>想看</button>
            <button type="button" className={viewer.collection_type === 3 ? "button-primary" : "button-secondary"} onClick={() => onPrepareWrite(Number(subject.id), text(subject.name), 3)}>在看</button>
            <button type="button" className={viewer.collection_type === 2 ? "button-primary" : "button-secondary"} onClick={() => onPrepareWrite(Number(subject.id), text(subject.name), 2)}>看过</button>
          </> : null}
          {onPrepareProgress && Number(viewer.ep_status || 0) < Number(subject.eps || Infinity) ? <button type="button" className="button-secondary" onClick={() => onPrepareProgress(Number(subject.id), text(subject.name), Number(viewer.ep_status || 0) + 1)}>看到第 {Number(viewer.ep_status || 0) + 1} 集</button> : null}
          {onAddWatchPlan ? <button type="button" className="button-secondary" onClick={async () => { try { await onAddWatchPlan(); setHubNotice("已加入 Otomo 本地计划板。"); } catch (error) { setHubNotice(`加入计划失败：${String(error)}`); } }}>加入本地计划</button> : null}
          {onToggleFollow ? <button type="button" className={isFollowing ? "button-primary" : "button-secondary"} onClick={async () => { try { await onToggleFollow(); setHubNotice(isFollowing ? "已停止整部作品的长期提醒。" : "已关注正版、RSS、续作、视频和进度变化。"); } catch (error) { setHubNotice(`关注设置失败：${String(error)}`); } }}>{isFollowing ? "已关注作品 · 点击取消" : "关注整部作品"}</button> : null}
        </div>
      </Panel>
      <nav className="dossier-tabs anime-hub-tabs" aria-label="动画作品中心模块">
        {[
          ["overview", "概览"], ["online", "在线观看"], ["releases", "RSS / 离线"],
          ["videos", "视频与漫评"], ["series", "系列顺序"], ["reputation", "口碑与分集"], ["music", "音乐"],
        ].map(([value, label]) => <button type="button" className={activeTab === value ? "active" : ""} onClick={() => selectTab(value)} key={value}>{label}</button>)}
      </nav>
      {activeTab === "overview" ? <Panel title={`${text(overview.verdict, "适合度仍待判断")} · ${text(subject.name)}`} subtitle="无剧透个性化判断">
        <p className="evidence-copy">{text(overview.fit_summary, "个性化证据仍在加载；不会用通用热度强行断言适合你。")}</p>
        <div className="fit-evidence-grid">
          <div><strong>可能适合你的地方</strong>{list<string>(overview.why_for_me).length ? list<string>(overview.why_for_me).map((row, i) => <span key={i}>{row}</span>) : <span>还没有足够的明确偏好证据</span>}</div>
          <div><strong>需要留意</strong>{list<string>(overview.risk_for_me).length ? list<string>(overview.risk_for_me).map((row, i) => <span key={i}>{row}</span>) : <span>暂未命中你的明确雷区</span>}</div>
        </div>
        {overview.general_consensus ? <p className="card-note">通用口碑：{text(overview.general_consensus)}</p> : null}
        {list(overview.friend_feedback).length ? <div className="compact-list inline">{list(overview.friend_feedback).map((row, i) => <span key={`${row.username}-${i}`}>@{text(row.username)} · {text(row.collection_label)}{row.rate ? ` · ${row.rate}分` : ""}</span>)}</div> : null}
      </Panel> : null}
      {activeTab === "series" && data.series_progress ? <SeriesProgressPanel data={data.series_progress} onPrepareWrite={onPrepareWrite} /> : null}
      {activeTab === "online" && data.online?.title ? <WhereToWatchPanel data={data.online} /> : null}
      {activeTab === "videos" && data.bilibili ? <div id="watch-bilibili" className="watch-hub-anchor">
        <Panel
          title={`B站普通投稿与延伸内容 · ${text(subject.name)}`}
          subtitle={`${playableVideos.length} 个可看正片候选 · ${editorialVideos.length} 个PV/漫评/回顾 · ${uncertainVideos.length} 个疑似候选`}
        >
        <div className="evidence-row">
          <Badge tone={bili.account_mode === "cookie" ? "good" : "dim"}>{bili.account_mode === "cookie" ? "B站登录态已接入" : "B站公开模式"}</Badge>
          {bili.cache_hit ? <Badge tone="good">已复用核验缓存</Badge> : <Badge tone="dim">本轮实时核验</Badge>}
          {bili.search_partial ? <Badge tone="warn">部分搜索源降级</Badge> : null}
          {bili.rate_limited ? <Badge tone="warn">B站限流 · 缓存兜底</Badge> : null}
          {bili.last_verified ? <Badge tone="dim">最近核验 {String(bili.last_verified).slice(0, 10)}</Badge> : null}
        </div>
        <div className="inline-notice watch-source-boundary">
          番剧库页面才是正版平台入口。下方“公开视频正片”来自B站普通投稿：可以打开观看，但Otomo未核验版权或上传授权，不会把它写成正版。
        </div>
        {playableVideos.length ? (
          <>
            <div className="section-title">普通投稿中的可看正片</div>
            <div className="section-copy">依据作品匹配、总时长、分P及正片/字幕格式信号进入；UP主是否像Staff不是必要条件。</div>
            <div className="bili-video-grid">
              {playableVideos.map(renderVideoCard)}
            </div>
          </>
        ) : <EmptyHint text="暂时没有普通投稿通过作品一致性、时长和正片内容门槛；不会用短片或标题党填充观看入口。" />}
        {editorialVideos.length ? (
          <>
            <div className="section-title">PV、漫评与回顾</div>
          <div className="bili-video-grid">
              {editorialVideos.map(renderVideoCard)}
          </div>
          </>
        ) : null}
        {uncertainVideos.length ? (
          <details className="pending-guide-sources">
            <summary>疑似正片，但证据不足 {uncertainVideos.length}</summary>
            <div className="section-copy">这些稿件不会进入默认观看入口；打开前请自行确认是否完整、是否同一季以及上传来源。</div>
            <div className="bili-video-grid compact">{uncertainVideos.map(renderVideoCard)}</div>
          </details>
        ) : null}
        {versionConflicts.length ? (
          <details className="pending-guide-sources">
            <summary>不是当前篇章的视频 {versionConflicts.length}</summary>
            <div className="section-copy">它们不会进入当前作品的观看入口；只有关联条目能够唯一对齐时，Otomo 才会给出“可能属于”的跳转。</div>
            <div className="compact-list">
              {versionConflicts.map((item, index) => (
                <span key={`${item.bvid || item.aid || item.title}-${index}`}>
                  <a href={item.url} target="_blank" rel="noreferrer">{text(item.title)}</a>
                  {` · ${text(item.reason)}`}
                  {item.suggested_subject_id ? <> · 可能属于 <a href={`/subject/${item.suggested_subject_id}`}>{text(item.suggested_subject_title)}</a>{item.suggested_relation ? `（${text(item.suggested_relation)}）` : ""}{item.suggested_collection_label ? ` · 你的状态：${text(item.suggested_collection_label)}` : ""}</> : " · 暂不能可靠映射到具体关联条目"}
                </span>
              ))}
            </div>
          </details>
        ) : null}
        {bili.navigation_url ? <div className="panel-actions"><a className="button-secondary" href={bili.navigation_url} target="_blank" rel="noreferrer">在B站继续搜索</a></div> : null}
        <Meta notes={list<string>(bili.warnings)} />
        </Panel>
      </div> : null}
      {activeTab === "releases" && data.releases?.title ? <>
        {onUpdatePreferences ? <Panel title="资源偏好" subtitle="只影响这部作品，不会擅自泛化成你的全局口味">
          <div className="settings-grid">
            <label className="setting-field wide"><span>优先字幕组（逗号分隔）</span><input defaultValue={list<string>(preferences.preferred_subgroups).join("，")} placeholder="例如：喵萌奶茶屋，北宇治字幕组" onBlur={(event) => void applyPreference({ preferred_subgroups: event.target.value.split(/[，,]/).map((value) => value.trim()).filter(Boolean) }, "字幕组优先级已保存。") } /></label>
            <label className="setting-field"><span>优先画质</span><select defaultValue={text(preferences.preferred_quality, "")} onChange={(event) => void applyPreference({ preferred_quality: event.target.value }, "画质偏好已保存。") }><option value="">不过滤</option><option value="2160p">2160p</option><option value="1080p">1080p</option><option value="720p">720p</option></select></label>
            <label className="setting-field"><span>字幕偏好</span><select defaultValue={text(preferences.preferred_subtitle, "")} onChange={(event) => void applyPreference({ preferred_subtitle: event.target.value }, "字幕偏好已保存。") }><option value="">不过滤</option><option value="简中">简中</option><option value="繁中">繁中</option><option value="简繁">简繁双语</option></select></label>
          </div>
          <div className="settings-options">{["mikan", "dmhy", "acgnx", "vcb"].map((source) => <label className="settings-check" key={source}><input type="checkbox" checked={!list<string>(preferences.disabled_sources).includes(source)} onChange={(event) => { const disabled = new Set(list<string>(preferences.disabled_sources)); if (event.target.checked) disabled.delete(source); else disabled.add(source); void applyPreference({ disabled_sources: Array.from(disabled) }, "资源来源设置已保存。"); }} /><span>显示 {source.toUpperCase()}</span></label>)}</div>
        </Panel> : null}
        <ReleaseFeedsPanel data={data.releases} onPrepareDownloaderPush={onPrepareDownloaderPush} onCreateRssFollow={onCreateRssFollow} />
      </> : null}
      {activeTab === "reputation" ? <>
        {data.reputation?.title ? <ReviewEvidencePanel data={data.reputation} /> : null}
        {data.episode_radar?.subject_id ? <EpisodeRadarPanel data={data.episode_radar} /> : null}
        {data.trend?.subject_id ? <SubjectTrendPanel data={data.trend} /> : null}
      </> : null}
      {activeTab === "music" && data.music?.subject ? <AnimeMusicThemesPanel data={data.music} /> : null}
      <Meta notes={list<string>(data.caveats)} />
    </>
  );
}

export function SeriesProgressPanel({
  data,
  onPrepareWrite,
}: {
  data: AnyRecord;
  onPrepareWrite?: PrepareWriteHandler;
}) {
  const mainline = list(data.mainline);
  const optional = list(data.optional);
  const alternates = list(data.alternates);
  const next = data.next_unwatched || null;
  const current = data.current || null;
  const names = new Map(mainline.map((item) => [Number(item.id), text(item.name)]));
  const statusTone = (state: string) => state === "watched"
    ? "good" : state === "watching" ? "good" : state === "on_hold" || state === "dropped" ? "warn" : "dim";
  const statusGlyph = (item: AnyRecord) => item.completed
    ? "✓" : item.is_next ? "▶" : list(item.blocked_by).length ? "锁" : "○";
  const canStart = next && onPrepareWrite && data.personalized && !next.completed;
  return (
    <Panel
      title="系列追番进度"
      subtitle={data.personalized ? `@${text(data.username)} · 主线 ${data.completed_required ?? 0}/${data.total_required ?? 0}` : "客观顺序 · 登录后合并收藏"}
    >
      <div className="series-progress-head">
        <div className="series-progress-track" aria-label={`系列主线完成 ${data.progress_percent ?? 0}%`}>
          <span style={{ width: `${Math.max(0, Math.min(100, Number(data.progress_percent || 0)))}%` }} />
        </div>
        <strong>{data.progress_percent ?? 0}%</strong>
      </div>
      <p className="evidence-copy">{text(data.summary)}</p>
      <div className="series-progress-grid">
        {mainline.map((item, index) => (
          <article className={`series-progress-card${item.is_current ? " current" : ""}${item.is_next ? " next" : ""}`} key={item.id}>
            <span className="series-order">{statusGlyph(item)}</span>
            {item.image ? <img src={item.image} alt="" loading="lazy" /> : <div className="series-cover-placeholder">{index + 1}</div>}
            <div>
              <a className="card-title title-link" href={`/subject/${item.id}`}>{text(item.name)}</a>
              <div className="evidence-row tight">
                <Badge tone={statusTone(String(item.collection_state))}>{text(item.collection_label)}</Badge>
                {item.is_current ? <Badge tone="dim">当前页面</Badge> : null}
                {item.is_next ? <Badge tone="good">下一步</Badge> : null}
                {item.necessity !== "required" ? <Badge tone="dim">{item.necessity === "skip" ? "可跳过" : "可选"}</Badge> : null}
              </div>
              {item.ep_status ? <small>分集进度 {item.ep_status}/{item.eps || "?"}</small> : null}
              {list(item.blocked_by).length ? (
                <small>需先完成：{list<number>(item.blocked_by).map((id) => names.get(Number(id)) || `subject ${id}`).join("、")}</small>
              ) : null}
              {item.is_next ? <p className="card-note">{text(item.action)}</p> : null}
            </div>
          </article>
        ))}
      </div>
      {onPrepareWrite && data.personalized ? (
        <div className="panel-actions series-actions">
          {current && !current.completed && current.collection_state === "watching" ? (
            <button className="button-secondary" type="button" onClick={() => onPrepareWrite(Number(current.id), text(current.name), 2)}>
              看完整季后标为看过
            </button>
          ) : null}
          {current && current.completed && current.collection_state === "watching" ? (
            <button className="button-secondary" type="button" onClick={() => onPrepareWrite(Number(current.id), text(current.name), 2)}>
              将本季状态同步为看过
            </button>
          ) : null}
          {canStart ? (
            <button className="button-primary" type="button" onClick={() => onPrepareWrite(Number(next.id), text(next.name), 3)}>
              {next.collection_state === "watching" ? "继续下一部" : next.collection_state === "on_hold" || next.collection_state === "dropped" ? "恢复下一部为在看" : "开始下一部"}
            </button>
          ) : null}
        </div>
      ) : null}
      {optional.length || alternates.length ? (
        <details className="series-optional">
          <summary>可选旁支与替代路线 {optional.length + alternates.length}</summary>
          <div className="compact-list">
            {[...optional, ...alternates].map((item) => (
              <span key={`${item.role}-${item.id}`}><a href={`/subject/${item.id}`}>{text(item.name)}</a> · {text(item.relation || item.action)} · {text(item.collection_label)}</span>
            ))}
          </div>
        </details>
      ) : null}
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}

export function BangumiIndexPanel({ data, onPrepareWrite }: { data: AnyRecord; onPrepareWrite?: PrepareWriteHandler }) {
  const items = list(data.items);
  return (
    <Panel
      title={`Bangumi 目录 · ${text(data.title)}`}
      subtitle={`${text(data.creator, "站友")} 整理 · ${items.length} 部`}
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
  const rememberIdentity = (item: AnyRecord) => {
    if (!item.subject_id || typeof window === "undefined") return;
    sessionStorage.setItem(`otomo:subject:${item.subject_id}`, JSON.stringify({
      subject: {
        id: item.subject_id,
        name: item.title,
        name_jp: item.title_jp,
        image: item.image,
        date: item.air_date || item.broadcast,
        platform: item.platform || "TV",
        type_name: "anime",
      },
      identity: { subject_id: item.subject_id, canonical_title: item.title, aliases: [item.title, item.title_jp].filter(Boolean) },
      resolution: { status: "resolved", matched_by: "subject_id", reason: "复用新番导视已确认的作品身份" },
    }));
  };
  const fitLabel: Record<string, string> = {
    strong: "适合度：很匹配",
    maybe: "适合度：值得试试",
    wait: "适合度：建议观望",
    unknown: "适合度：尚未判断",
  };
  const heatLabel: Record<string, string> = {
    surge: "热度：快速上升",
    hot: "热度：热门",
    warm: "热度：有讨论",
    none: "热度：暂无趋势",
  };
  const single = Boolean(anchoredItem);
  const videoTypeLabel: Record<string, string> = {
    preseason_guide: "播前导视",
    airing_review: "热播漫评",
    season_recap: "季度复盘",
    general: "季度视频",
  };
  const sourceLabel: Record<string, string> = {
    preferred: "你的来源",
    whitelist: "可信来源",
    discovered: "全站发现",
  };
  const formatCount = (value: unknown) => {
    const number = Number(value || 0);
    return number ? new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(number) : "";
  };
  const formatVideoDate = (value: unknown) => {
    const timestamp = Number(value || 0);
    return timestamp ? new Date(timestamp * 1000).toLocaleDateString("zh-CN", { year: "numeric", month: "short", day: "numeric" }) : "";
  };
  const guideRows = (sources: AnyRecord[], limit = 8) => sources.flatMap((source) => (
    list(source.verified_hits).map((hit) => ({ source, hit }))
  )).slice(0, limit);
  const renderBiliVideoCard = ({ source, hit }: { source: AnyRecord; hit: AnyRecord }, idx: number) => {
    const href = hit.url || source.url || "";
    const upHref = source.up_url || (hit.mid ? `https://space.bilibili.com/${hit.mid}` : "");
    const discoverySource = String(hit.discovery_source || source.discovery_source || "whitelist");
    const contentType = String(hit.content_type || "general");
    return (
      <article className={`bili-video-card ${discoverySource}`} key={`${hit.bvid || hit.aid || href}-${idx}`}>
        <a className="bili-video-cover" href={href} target="_blank" rel="noreferrer" aria-label={`打开 B站视频：${text(hit.title)}`}>
          {hit.thumbnail_url ? (
            <img src={hit.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
          ) : (
            <span><b>BILI</b><small>{videoTypeLabel[contentType] || "季度视频"}</small></span>
          )}
          <i>{videoTypeLabel[contentType] || "季度视频"}</i>
        </a>
        <div className="bili-video-body">
          <div className="evidence-row tight">
            <Badge tone={discoverySource === "preferred" ? "good" : discoverySource === "discovered" ? "warn" : "dim"}>
              {sourceLabel[discoverySource] || "可信来源"}
            </Badge>
            <Badge tone={hit.content_verified ? "good" : "dim"}>
              {hit.content_verified ? `${hit.transcript_source === "asr" ? "ASR" : "字幕"}正文已核验` : "元数据已核验"}
            </Badge>
          </div>
          <a className="bili-video-title" href={href} target="_blank" rel="noreferrer">{text(hit.title)}</a>
          <div className="bili-video-byline">
            {upHref ? <a href={upHref} target="_blank" rel="noreferrer">{text(hit.author || source.up_name)}</a> : <span>{text(hit.author || source.up_name)}</span>}
            {formatVideoDate(hit.pubdate) ? <span>{formatVideoDate(hit.pubdate)}</span> : null}
          </div>
          <div className="bili-video-stats">
            {hit.play ? <span>播放 {formatCount(hit.play)}</span> : null}
            {hit.danmaku ? <span>弹幕 {formatCount(hit.danmaku)}</span> : null}
            <span>匹配 {pct(hit.match_confidence)}</span>
          </div>
          <details className="bili-video-proof">
            <summary>为什么进入结果</summary>
            <p>{text(hit.content_type_reason || source.positioning)}</p>
            <p>{text(hit.content_match_reason || hit.match_reason || source.verification_note)}</p>
          </details>
        </div>
      </article>
    );
  };
  return (
    <Panel
      title={single ? `季番导视 · ${text(anchoredItem?.title)}` : `季番导视 · ${text(data.season)}`}
      subtitle={`${data.personalized ? "按你的口味排序" : "通用视角"} · ${single ? "单部详情" : `${items.length} 部`}`}
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
        <Badge tone={data.mode === "hot" ? "warn" : data.mode === "preseason" ? "good" : "dim"}>{data.mode === "hot" ? "热播优先" : data.mode === "preseason" ? "播前导视" : "口味导视"}</Badge>
        {list<string>(data.profile_tags).slice(0, 8).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
        {list<string>(data.focus_tags).map((tag) => <Badge key={tag} tone="good">{tag}</Badge>)}
      </div>
      <div className="season-grid">
        {visibleItems.map((item, i) => (
          <div className="season-card" key={`${item.subject_id}-${i}`}>
            {item.image ? <img src={item.image} alt="" /> : <div className="season-noimg" />}
            <div className="season-main">
              <a className="card-title title-link" href={`/subject/${item.subject_id}`} onClick={() => rememberIdentity(item)}>{text(item.title)}</a>
              <div className="card-meta">
                {item.bangumi_score ? `Bangumi ${item.bangumi_score}` : "暂无评分"}
                {item.broadcast ? ` · ${item.broadcast}` : ""}
              </div>
              <div className="evidence-row tight">
                <Badge tone={clsBySignal(item.fit)}>{fitLabel[String(item.fit)] || fitLabel.unknown}</Badge>
                <Badge tone={item.bangumi_score >= 8 ? "good" : item.bangumi_score && item.bangumi_score < 6.5 ? "warn" : "dim"}>
                  {item.bangumi_score ? `口碑：${item.bangumi_score}` : "口碑：样本不足"}
                </Badge>
                <Badge tone={item.hotness_level === "surge" || item.hotness_level === "hot" ? "warn" : item.hotness_level === "warm" ? "dim" : "dim"}>
                  {heatLabel[String(item.hotness_level)] || heatLabel.none}
                </Badge>
                {item.pre_air_wish != null && <Badge tone="dim">播前期待 {item.pre_air_wish}</Badge>}
                {item.series_status ? <Badge tone={item.series_status.collection_state === "watched" || item.series_status.collection_state === "watching" ? "good" : item.series_status.collection_state === "on_hold" || item.series_status.collection_state === "dropped" ? "warn" : "dim"}>{text(item.series_status.collection_label)}</Badge> : null}
                {item.series_status?.is_sequel ? <Badge tone={item.series_status.prerequisites_satisfied ? "good" : "warn"}>{item.series_status.prerequisites_satisfied ? "前作已完成" : "缺少必要前作"}</Badge> : null}
              </div>
              {item.series_status && !item.series_status.prerequisites_satisfied ? (
                <div className="inline-notice season-series-warning">
                  <strong>暂不建议直接开这部</strong>
                  <span>{text(item.series_status.note)}</span>
                  {list(item.series_status.missing_predecessors).length ? <span>先看：{list(item.series_status.missing_predecessors).map((row) => text(row.name)).join("、")}</span> : null}
                  {item.series_status.next_subject_id ? <a href={`/subject/${item.series_status.next_subject_id}`}>去下一部必要主线：{text(item.series_status.next_subject_name)}</a> : null}
                </div>
              ) : null}
              {item.mapping_warning ? <p className="card-note season-mapping-warning">{text(item.mapping_warning)}</p> : null}
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
                {item.subject_id && <a href={`/subject/${item.subject_id}`} onClick={() => rememberIdentity(item)}>作品中心</a>}
                {item.subject_id && <a href={`/subject/${item.subject_id}#watch-online`} onClick={() => rememberIdentity(item)}>在线观看</a>}
                {item.subject_id && <a href={`/subject/${item.subject_id}#watch-bilibili`} onClick={() => rememberIdentity(item)}>B站内容</a>}
                {item.subject_id && <a href={`/subject/${item.subject_id}#watch-release`} onClick={() => rememberIdentity(item)}>RSS/下载</a>}
                {item.subject_id && onPrepareWrite && (!item.series_status || item.series_status.prerequisites_satisfied) && !["watched", "watching"].includes(String(item.series_status?.collection_state || "")) && (
                  <button
                    type="button"
                    className="inline-action card-action"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onPrepareWrite(
                        Number(item.subject_id),
                        text(item.title),
                        item.series_status?.collection_state === "wishlist" ? 3 : 1,
                      );
                    }}
                  >
                    {item.series_status?.collection_state === "wishlist" ? "开始追" : "想看"}
                  </button>
                )}
                {item.official_url && <a href={item.official_url} target="_blank" rel="noreferrer">官网</a>}
                {item.pv_url && <a href={item.pv_url} target="_blank" rel="noreferrer">PV</a>}
                {item.bili_url && <a href={item.bili_url} target="_blank" rel="noreferrer">B站正版</a>}
              </div>
              {list(item.guide_videos).length > 0 && (
                <details className="item-guide-details">
                  <summary>导视来源 {list(item.guide_videos).length}</summary>
                  <div className="bili-video-grid compact">
                    {guideRows(list(item.guide_videos), 3).map(renderBiliVideoCard)}
                  </div>
                </details>
              )}
            </div>
          </div>
        ))}
      </div>
      {!single && list(data.guide_videos).length > 0 && (
        <>
          <div className="section-title">B站季度视频</div>
          <div className="section-copy">偏好来源优先；全站发现使用更严格的标题、季度、发布时间和视频详情门槛。</div>
          <div className="bili-video-grid">
            {guideRows(list(data.guide_videos), 6).map(renderBiliVideoCard)}
          </div>
        </>
      )}
      {!single && list(data.pending_guide_sources).length > 0 && (
        <details className="pending-guide-sources">
          <summary>仍在关注、但尚未进入结果的来源 {list(data.pending_guide_sources).length}</summary>
          <div className="compact-list">
            {list(data.pending_guide_sources).map((source, index) => <span key={`${source.up_name}-${index}`}><strong>{text(source.up_name)}</strong> · {source.publication_status === "not_found" ? "尚未发现本季视频" : source.publication_status === "rejected" ? "视频正文不匹配本季" : source.publication_status === "unavailable" ? "本轮无法核验" : "等待核验"}</span>)}
          </div>
        </details>
      )}
      {!single && !list(data.guide_videos).length && <div className="inline-notice">目前还没有发现已发布且通过核验的本季导视视频。Otomo 不会用 UP 主页或搜索入口冒充具体导视。</div>}
      {!single && list<string>(data.guide_discovery_warnings).length > 0 && (
        <details className="pending-guide-sources">
          <summary>本轮 B站发现说明</summary>
          <div className="compact-list">{list<string>(data.guide_discovery_warnings).map((warning, index) => <span key={index}>{warning}</span>)}</div>
        </details>
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
    <Panel title="分集口碑雷达" subtitle={`共 ${data.total ?? curve.length} 集 · 每集讨论热度`}>
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


export function EpisodeBuzzScanPanel({ data }: { data: AnyRecord }) {
  const hits = list(data.hits);
  return (
    <Panel
      title="分集爆点雷达"
      subtitle={`扫描 ${data.checked_subjects ?? 0} 部在看番 · ${hits.length} 个爆点`}
    >
      {hits.length === 0 && <EmptyHint text="最近你追的番没有讨论量突增的集——岁月静好。" />}
      {hits.map((h, i) => (
        <a key={i} className="buzz-row" href={text(h.url, "#")} target="_blank" rel="noreferrer">
          <span className="buzz-flame">🔥</span>
          <span className="buzz-main">
            <b>{text(h.subject_name)}</b> 第 {h.sort} 集{h.ep_name ? `「${h.ep_name}」` : ""}
            <small> · {h.airdate}</small>
          </span>
          <span className="buzz-stats">
            {h.comments} 条讨论{h.ratio ? <Badge tone="warn">{h.ratio}× 平常</Badge> : <Badge tone="good">开播即热</Badge>}
          </span>
        </a>
      ))}
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}
