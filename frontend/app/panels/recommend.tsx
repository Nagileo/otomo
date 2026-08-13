"use client";

// 推荐域面板：口味画像、推荐清单、观看副驾、补番顺序。

import { useEffect, useRef, useState } from "react";

import { type AnyRecord, type ShareSnapshotHandler, type PrepareWriteHandler, list, text, pct, Badge, Panel, EmptyHint, Meta, ShareSnapshotButton } from "./shared";

const SCENARIO_LABEL: Record<string, string> = {
  general: "按你的口味",
  tonight: "今晚就能看完",
  season: "本季新番",
  backlog: "清理想看列表",
  gal_intro: "galgame 入门",
  cross_media: "跨媒体延伸",
};

export function AspectProfilePanel({ data }: { data: AnyRecord }) {
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
      <Meta notes={list<string>(data.caveats)} />
    </Panel>
  );
}

export function RecommendPanel({
  data,
  onCritique,
  onPrepareWrite,
  onFeedback,
  onNextBatch,
}: {
  data: AnyRecord;
  onCritique?: (q: string) => void;
  onPrepareWrite?: PrepareWriteHandler;
  onFeedback?: (payload: AnyRecord) => Promise<boolean>;
  onNextBatch?: (setId: string) => Promise<AnyRecord | null>;
}) {
  const [current, setCurrent] = useState(data);
  const [dismissed, setDismissed] = useState<number[]>([]);
  const [reasons, setReasons] = useState<Record<number, string>>({});
  const [loadingNext, setLoadingNext] = useState(false);
  const reportedImpressions = useRef(new Set<string>());
  useEffect(() => { setCurrent(data); setDismissed([]); }, [data]);
  const items = list(current.items).filter((item) => !dismissed.includes(Number(item.id)));
  const setId = String(current.recommendation_set_id || "");
  const aspectProfile = current.aspect_profile_summary || {};
  const mediaStrategy = current.media_strategy || {};
  const scenarioText = SCENARIO_LABEL[String(current.scenario || "general")] || "按你的口味";
  const fb = current.feedback_policy;

  useEffect(() => {
    if (!onFeedback || !setId) return;
    list(current.items).forEach((item) => {
      const key = `${setId}:${Number(item.id)}`;
      if (reportedImpressions.current.has(key)) return;
      reportedImpressions.current.add(key);
      void onFeedback({ recommendation_set_id: setId, subject_id: Number(item.id), event: "impression" });
    });
  }, [current, onFeedback, setId]);

  async function feedback(item: AnyRecord, event: string, reason?: string) {
    if (!onFeedback || !setId) return false;
    const ok = await onFeedback({
      recommendation_set_id: setId,
      subject_id: Number(item.id),
      event,
      ...(reason ? { reason } : {}),
    });
    if (ok && event === "dismiss") setDismissed((ids) => [...ids, Number(item.id)]);
    return ok;
  }

  async function nextBatch() {
    if (!onNextBatch || !setId || loadingNext) return;
    setLoadingNext(true);
    const next = await onNextBatch(setId);
    if (next) { setCurrent(next); setDismissed([]); }
    setLoadingNext(false);
  }
  return (
    <Panel
      title="为你推荐"
      subtitle={`${scenarioText} · 挑了 ${items.length} 部`}
    >
      {(list(aspectProfile.likes).length > 0 || list(aspectProfile.dislikes).length > 0 || list<string>(data.based_on_tags).length > 0) && (
        <div className="evidence-row">
          {list<string>(data.based_on_tags).slice(0, 6).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
          {list(aspectProfile.likes).slice(0, 3).map((x) => <Badge key={`like-${x.aspect}`} tone="good">你吃 {text(x.label || x.aspect)}</Badge>)}
          {list(aspectProfile.dislikes).slice(0, 3).map((x) => <Badge key={`dislike-${x.aspect}`} tone="warn">避 {text(x.label || x.aspect)}</Badge>)}
        </div>
      )}
      <div className="rec-grid">
        {items.map((item, i) => {
          const fit = list<string>(item.fit_points)[0] || item.review_consensus || "";
          const risk = list<string>(item.risks)[0] || list<string>(item.aspect_warnings)[0] || "";
          const recall = list<string>(item.why_recalled)[0] || "";
          const nextStep = list<string>(item.next_step)[0] || "";
          return (
            <article className="rec-card" key={`${item.id}-${i}`}>
              <a href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" onClick={() => void feedback(item, "open")}>
                {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
              </a>
              <div className="rec-body">
                <a className="card-title" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" onClick={() => void feedback(item, "open")}>{text(item.name)}</a>
                <div className="card-meta">
                  {item.bangumi_score ? `Bangumi ${item.bangumi_score}` : "评分暂无"}
                  {item.rank ? ` · 全站 #${item.rank}` : ""}
                </div>
                {fit && <p className="card-note">{fit}</p>}
                {risk && <p className="card-note">⚠ {risk}</p>}
                <div className="evidence-row tight">
                  {recall && <Badge tone="good">{recall}</Badge>}
                  {list<string>(item.explicit_tag_matches).slice(0, 3).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
                  {list<string>(item.quality_badges).slice(0, 2).map((tag) => <Badge key={tag} tone="warn">{tag}</Badge>)}
                  {item.id && onPrepareWrite && (
                    <button
                      type="button"
                      className="inline-action card-action"
                      onClick={() => {
                        onPrepareWrite(Number(item.id), text(item.name), 1, {
                          recommendationSetId: setId,
                        });
                      }}
                    >
                      想看
                    </button>
                  )}
                  {onFeedback && setId && <button type="button" className="inline-action card-action" onClick={() => void feedback(item, "more")}>多来这种</button>}
                  {onFeedback && setId && <button type="button" className="inline-action card-action" onClick={() => void feedback(item, "less")}>少来这种</button>}
                </div>
                {onFeedback && setId && (
                  <div className="rec-dismiss-row">
                    <select
                      aria-label={`不推荐《${text(item.name)}》的原因`}
                      value={reasons[Number(item.id)] || "not_interested"}
                      onChange={(event) => setReasons((prev) => ({ ...prev, [Number(item.id)]: event.target.value }))}
                    >
                      <option value="not_interested">不感兴趣</option>
                      <option value="already_seen">其实看过了</option>
                      <option value="genre">题材不合</option>
                      <option value="visual">画风不合</option>
                      <option value="pace">节奏不合</option>
                      <option value="length">太长</option>
                      <option value="temporary">这次暂时不要</option>
                    </select>
                    <button type="button" className="inline-action card-action" onClick={() => void feedback(item, "dismiss", reasons[Number(item.id)] || "not_interested")}>移除</button>
                  </div>
                )}
                {nextStep && <div className="compact-list inline next-step"><span>{nextStep}</span></div>}
              </div>
            </article>
          );
        })}
      </div>
      {onNextBatch && setId && (
        <div className="panel-actions"><button type="button" className="ghost" disabled={loadingNext} onClick={() => void nextBatch()}>{loadingNext ? "正在重排…" : "换一批"}</button></div>
      )}
      {onCritique && (list<string>(data.critique_chips).length > 0 || list<string>(data.cold_start_questions).length > 0) && (
        <div className="followups">
          {[...list<string>(data.critique_chips), ...list<string>(data.cold_start_questions)].map((q, i) => (
            <button className="chip" key={i} onClick={() => onCritique(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
      <Meta
        notes={[
          mediaStrategy.policy,
          ...list<string>(current.applied_constraints).map((x) => `约束：${x}`),
          fb ? `反馈闭环：正向 ${fb.positive ?? 0} / 负向 ${fb.negative ?? 0}${list<string>(fb.negative_tags).length ? `（避雷 ${list<string>(fb.negative_tags).slice(0, 4).join("、")}）` : ""}` : null,
          current.diversity?.method ? `多样性：${current.diversity.method} · ILD ${current.diversity.intra_list_diversity ?? "-"} · 系列策略 ${current.diversity.series_policy}` : null,
          ...list<string>(current.mapping_warnings).map((w) => `映射告警：${w}`),
          ...list<string>(current.notes),
        ]}
      />
    </Panel>
  );
}

export function WatchCopilotPanel({ data }: { data: AnyRecord }) {
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
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}

export function WatchOrderPanel({ data, onShareSnapshot }: { data: AnyRecord; onShareSnapshot?: ShareSnapshotHandler }) {
  const main = list(data.watch_order);
  const sides = list(data.side_stories);
  const alternates = list(data.alternate_routes);
  const skips = list(data.skip_candidates);
  const tone = (necessity: any) => {
    const n = String(necessity || "");
    if (n === "required") return "good";
    if (n === "optional" || n === "skip") return "warn";
    return "dim";
  };
  const label = (necessity: any) => {
    const n = String(necessity || "");
    if (n === "required") return "必看";
    if (n === "optional") return "可选";
    if (n === "skip") return "可跳过";
    return "建议";
  };
  const renderItems = (items: AnyRecord[], compact = false) => (
    <div className={compact ? "watch-order-list compact" : "watch-order-list"}>
      {items.map((item, i) => (
        <a className="watch-order-item" href={`https://bgm.tv/subject/${item.id}`} target="_blank" rel="noreferrer" key={`${item.id}-${i}`}>
          <div className="watch-order-index">{item.order ?? i + 1}</div>
          <div className="watch-order-body">
            <div className="watch-order-top">
              <span className="card-title">{text(item.name)}</span>
              <Badge tone={tone(item.necessity)}>{label(item.necessity)}</Badge>
            </div>
            <div className="card-meta">
              {text(item.relation || item.watch_role, "主线")}
              {item.date ? ` · ${item.date}` : ""}
              {item.duration_hint ? ` · ${item.duration_hint}` : ""}
              {item.score ? ` · BGM ${item.score}` : ""}
            </div>
            {item.skip_advice ? <p className="card-note">{text(item.skip_advice)}</p> : null}
          </div>
        </a>
      ))}
    </div>
  );
  return (
    <Panel title={`补番路线 · ${text(data.ip)}`} subtitle="按 Bangumi 关系边、播出日期和必要性整理">
      <div className="panel-actions">
        <ShareSnapshotButton
          type="watch_order"
          title={`补番路线 · ${text(data.ip)}`}
          payload={data}
          onShareSnapshot={onShareSnapshot}
        />
      </div>
      <div className="evidence-row">
        <Badge tone="good">主线 {main.length}</Badge>
        <Badge tone="dim">旁支 {sides.length}</Badge>
        <Badge tone="dim">不同演绎 {alternates.length}</Badge>
        <Badge tone={skips.length ? "warn" : "good"}>可跳过 {skips.length}</Badge>
      </div>
      {main.length > 0 ? (
        <>
          <div className="section-title">主线顺序</div>
          {renderItems(main)}
        </>
      ) : <EmptyHint text="没有主线条目" />}
      {sides.length > 0 && (
        <>
          <div className="section-title">旁支 / OVA / 番外</div>
          {renderItems(sides, true)}
        </>
      )}
      {alternates.length > 0 && (
        <>
          <div className="section-title">不同演绎 / 重制 / 替代路线</div>
          {renderItems(alternates, true)}
        </>
      )}
      {skips.length > 0 && (
        <>
          <div className="section-title">可跳过候选</div>
          <div className="compact-list">
            {skips.map((item, i) => (
              <span key={`${item.id}-${i}`}>{text(item.name)} · {text(item.skip_advice)}</span>
            ))}
          </div>
        </>
      )}
      <Meta notes={list<string>(data.notes)} />
    </Panel>
  );
}


