"use client";

// 视觉域面板：图像/插画/视频类证据。
// 既有面板（VisualText/VisualStyle/ImageSource/RouteImageSource/BiliVideoContent/VideoFrame）
// 后续从 evidence-panels.tsx 搬迁至此；新视觉面板一律写在本文件。

import { Badge, Panel, list, text, type AnyRecord } from "../evidence-panels";

export function PixivPanel({ data }: { data: AnyRecord }) {
  const results = list(data.results);
  const subtitle = [
    data.mode ? `榜单 ${text(data.mode)}` : "",
    data.query ? `检索「${text(data.query)}」` : "",
    `${text(data.count, "0")} 张`,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <Panel title="Pixiv 插画" subtitle={subtitle}>
      <div className="evidence-row">
        <Badge tone="warn">source: pixiv（话语/创作源）</Badge>
        <Badge tone="good">R18 已硬过滤</Badge>
      </div>
      {results.length === 0 && <div className="empty-hint">未检索到插画；可能是 Pixiv 未启用或网络不可达。</div>}
      <div className="pixiv-grid">
        {results.map((it, i) => (
          <a key={i} className="pixiv-card" href={text(it.url, "#")} target="_blank" rel="noreferrer">
            {it.thumb_url ? (
              // pixiv 图床防盗链：no-referrer 直连，失败则隐藏图片仅留文字卡
              <img
                src={it.thumb_url}
                alt={text(it.title, "插画")}
                referrerPolicy="no-referrer"
                loading="lazy"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = "none";
                }}
              />
            ) : null}
            <div className="pixiv-meta">
              <div className="pixiv-title">{text(it.title, "无题")}</div>
              <div className="pixiv-artist">{text(it.artist, "未知画师")}</div>
              {list<string>(it.tags).length > 0 && (
                <div className="pixiv-tags">
                  {list<string>(it.tags)
                    .slice(0, 4)
                    .map((t, j) => (
                      <Badge key={j} tone="dim">
                        {t}
                      </Badge>
                    ))}
                </div>
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
