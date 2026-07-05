"use client";

// 作品/媒体域面板：条目发现、评价、导视、观看与资源类证据。
// 既有面板（ReviewEvidence/SeasonGuide/Recommend/BroadcastCalendar/AiringProgress/
// WhereToWatch/ReleaseFeed/BangumiIndex/Explorer/EpisodeRadar）后续搬迁至此；
// 新媒体域面板一律写在本文件。

import { Badge, Panel, list, text, type AnyRecord } from "../evidence-panels";

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

export function PilgrimageTripPanel({ data }: { data: AnyRecord }) {
  const entries = list(data.entries);
  return (
    <Panel
      title={`巡礼行程 · @${text(data.username)}`}
      subtitle={`${data.city_filter ? `目的地「${text(data.city_filter)}」 · ` : ""}检查 ${data.checked ?? 0} 部 → ${entries.length} 部有圣地数据`}
    >
      {entries.length === 0 && <div className="empty-hint">看过/在看里没有命中巡礼数据；可去掉城市过滤重查。</div>}
      <div className="trip-list">
        {entries.map((e, i) => (
          <a key={i} className="trip-card" href={text(e.map_url, "#")} target="_blank" rel="noreferrer">
            {e.cover ? <img src={e.cover} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
            <div className="trip-meta">
              <div className="trip-title">{text(e.title)}</div>
              <div className="trip-sub">
                <Badge tone="good">{e.point_count} 个取景点</Badge>
                {e.city && <Badge tone="dim">{text(e.city)}</Badge>}
              </div>
              {list<string>(e.sample_points).length > 0 && (
                <div className="trip-samples">{list<string>(e.sample_points).join(" · ")}</div>
              )}
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
