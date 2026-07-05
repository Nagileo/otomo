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
