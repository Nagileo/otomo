"use client";

// 视觉域面板：图像/插画/视频类证据。
// 既有面板（VisualText/VisualStyle/ImageSource/RouteImageSource/BiliVideoContent/VideoFrame）
// 后续从 evidence-panels.tsx 搬迁至此；新视觉面板一律写在本文件。

import { Badge, Panel, list, text, type AnyRecord } from "./shared";
import { useState } from "react";
import { pct, EmptyHint } from "./shared";

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

export function VisualCorrectionButton({
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

export function VisualTextPanel({ data }: { data: AnyRecord }) {
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

export function VisualStylePanel({ data }: { data: AnyRecord }) {
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

export function ImageSourcePanel({ data }: { data: AnyRecord }) {
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

export function RouteImageSourcePanel({
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

export function BiliVideoContentPanel({ data }: { data: AnyRecord }) {
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

export function VideoFramePanel({ data }: { data: AnyRecord }) {
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


