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
  const [expanded, setExpanded] = useState(false);
  const [feedbackChoice, setFeedbackChoice] = useState<{ id: number; event: "more" | "less" } | null>(null);
  const [feedbackScope, setFeedbackScope] = useState<Record<number, string>>({});
  const [lastAction, setLastAction] = useState<{ item: AnyRecord; label: string } | null>(null);
  const [loadingNext, setLoadingNext] = useState(false);
  const reportedImpressions = useRef(new Set<string>());
  const gridRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    setCurrent(data);
    setDismissed([]);
    setExpanded(false);
    setFeedbackChoice(null);
    setLastAction(null);
  }, [data]);
  const items = list(current.items).filter((item) => !dismissed.includes(Number(item.id)));
  const shownItems = expanded ? items : items.slice(0, 3);
  const setId = String(current.recommendation_set_id || "");
  const aspectProfile = current.aspect_profile_summary || {};
  const mediaStrategy = current.media_strategy || {};
  const scenarioText = SCENARIO_LABEL[String(current.scenario || "general")] || "按你的口味";
  const fb = current.feedback_policy;
  const model = current.model_metadata || {};
  const performance = current.performance || {};
  const personalizationLabel = model.available && !model.stale
    ? "正式个性化"
    : model.available ? "个性化模型待更新" : "画像推荐";

  useEffect(() => {
    const root = gridRef.current;
    if (!root || !onFeedback || !setId || typeof IntersectionObserver === "undefined") return;
    const timers = new Map<Element, number>();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        const node = entry.target as HTMLElement;
        const subjectId = Number(node.dataset.recommendationId || 0);
        const key = `${setId}:${subjectId}`;
        const oldTimer = timers.get(entry.target);
        if (oldTimer) {
          window.clearTimeout(oldTimer);
          timers.delete(entry.target);
        }
        if (
          !entry.isIntersecting
          || entry.intersectionRatio < 0.6
          || !subjectId
          || reportedImpressions.current.has(key)
        ) return;
        const timer = window.setTimeout(() => {
          reportedImpressions.current.add(key);
          void onFeedback({
            recommendation_set_id: setId,
            subject_id: subjectId,
            event: "impression",
            note: "visible_1200ms",
          });
          timers.delete(entry.target);
        }, 1200);
        timers.set(entry.target, timer);
      });
    }, { threshold: [0.6] });
    root.querySelectorAll("[data-recommendation-id]").forEach((node) => observer.observe(node));
    return () => {
      observer.disconnect();
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [current, expanded, onFeedback, setId]);

  async function feedback(item: AnyRecord, event: string, reason?: string, aspect = "item") {
    if (!onFeedback || !setId) return false;
    const ok = await onFeedback({
      recommendation_set_id: setId,
      subject_id: Number(item.id),
      event,
      ...(reason ? { reason } : {}),
      aspect,
    });
    if (ok) {
      if (event === "dismiss") setDismissed((ids) => [...ids, Number(item.id)]);
      const label = event === "more"
        ? "已记住：多来这种"
        : event === "less" ? "已记住：少来这种" : "已从本批推荐移除";
      setLastAction({ item, label });
      setFeedbackChoice(null);
    }
    return ok;
  }

  async function undoLast() {
    if (!lastAction || !onFeedback || !setId) return;
    const ok = await onFeedback({
      recommendation_set_id: setId,
      subject_id: Number(lastAction.item.id),
      event: "undo",
    });
    if (!ok) return;
    setDismissed((ids) => ids.filter((id) => id !== Number(lastAction.item.id)));
    setLastAction(null);
  }

  async function nextBatch() {
    if (!onNextBatch || !setId || loadingNext) return;
    setLoadingNext(true);
    const next = await onNextBatch(setId);
    if (next) { setCurrent(next); setDismissed([]); setExpanded(false); setLastAction(null); }
    setLoadingNext(false);
  }
  function openItem(item: AnyRecord) {
    if (item.id && typeof window !== "undefined") {
      sessionStorage.setItem(`otomo:subject:${item.id}`, JSON.stringify({
        subject: {
          id: item.id,
          name: item.name,
          image: item.image,
          date: item.release_date,
          eps: item.episodes,
          type_name: String(current.subject_type || "anime"),
        },
        resolution: { status: "resolved", matched_by: "subject_id", reason: "复用推荐卡已确认的 Bangumi 条目" },
      }));
    }
    void feedback(item, "open");
  }
  return (
    <Panel
      title="为你推荐"
      subtitle={`${scenarioText} · 共 ${items.length} 部，先看最值得考虑的 3 部`}
    >
      <div className="recommend-status-row">
        <Badge tone={model.available && !model.stale ? "good" : "dim"}>{personalizationLabel}</Badge>
        {model.stale ? <span>协同数据较旧，已自动降权；画像和本轮偏好仍正常参与。</span> : null}
        {Number(performance.total_ms) > 0 ? (
          <span>
            本轮精筛 {(Number(performance.total_ms) / 1000).toFixed(1)} 秒
            {["full_finalist_pool", "verified_finalist_pool"].includes(performance.evidence_policy) ? " · 已核验最终候选池" : ""}
          </span>
        ) : null}
      </div>
      {lastAction ? (
        <div className="rec-feedback-notice" role="status">
          <span>{lastAction.label} · 《{text(lastAction.item.name)}》</span>
          <button type="button" className="inline-action" onClick={() => void undoLast()}>撤销</button>
        </div>
      ) : null}
      {(list(aspectProfile.likes).length > 0 || list(aspectProfile.dislikes).length > 0 || list<string>(current.based_on_tags).length > 0) && (
        <div className="evidence-row">
          {list<string>(current.based_on_tags).slice(0, 6).map((tag) => <Badge key={tag} tone="dim">{tag}</Badge>)}
          {list(aspectProfile.likes).slice(0, 3).map((x) => <Badge key={`like-${x.aspect}`} tone="good">你吃 {text(x.label || x.aspect)}</Badge>)}
          {list(aspectProfile.dislikes).slice(0, 3).map((x) => <Badge key={`dislike-${x.aspect}`} tone="warn">避 {text(x.label || x.aspect)}</Badge>)}
        </div>
      )}
      <div className="rec-grid" ref={gridRef}>
        {shownItems.map((item, i) => {
          const integrityVerified = item.integrity_verified !== false;
          const claims = integrityVerified ? list(item.claims) : [];
          const fitClaims = claims.filter((claim) => claim.kind === "fit");
          const riskClaims = claims.filter((claim) => claim.kind === "risk");
          const qualityClaims = claims.filter((claim) => claim.kind === "quality");
          const provenanceClaims = claims.filter((claim) => claim.kind === "provenance");
          const fit = integrityVerified ? text(fitClaims[0]?.text || list<string>(item.fit_points)[0], "") : "";
          const risk = integrityVerified ? list<string>(item.risks)[0] || list<string>(item.aspect_warnings)[0] || "" : "";
          const nextStep = list<string>(item.next_step)[0] || "";
          const choiceOpen = feedbackChoice?.id === Number(item.id);
          return (
            <article className="rec-card rec-card-explained" key={`${item.id}-${i}`} data-recommendation-id={Number(item.id)}>
              <a href={`/subject/${item.id}`} onClick={() => openItem(item)}>
                {item.image ? <img src={item.image} alt="" /> : <div className="rec-noimg" />}
              </a>
              <div className="rec-body">
                <a className="card-title" href={`/subject/${item.id}`} onClick={() => openItem(item)}>{text(item.name)}</a>
                <div className="card-meta">
                  {item.bangumi_score ? `Bangumi ${item.bangumi_score}` : "评分暂无"}
                  {item.rank ? ` · 全站 #${item.rank}` : ""}
                  {item.release_date ? ` · ${text(item.release_date).slice(0, 10)}` : ""}
                  {item.episodes ? ` · ${item.episodes} 集` : ""}
                </div>
                {(item.media_subtype || list<string>(item.media_notes).length) ? <div className="evidence-row tight rec-media-meta">{item.media_subtype ? <Badge tone="dim">{text(item.media_subtype)}</Badge> : null}{list<string>(item.media_notes).slice(0, 2).map((note) => <span key={note}>{note}</span>)}</div> : null}
                {item.series_status?.continued_from ? <div className="evidence-row tight"><Badge tone="good">已回到下一部必要主线</Badge><span>原候选：{text(item.series_status.continued_from)}</span></div> : null}
                {item.series_status?.has_predecessor && item.series_status?.prerequisites_satisfied ? <div className="evidence-row tight"><Badge tone="good">必要前作已完成</Badge></div> : null}
                {item.series_status?.has_predecessor && !item.series_status?.prerequisites_satisfied ? <p className="card-note rec-risk"><strong>续作前置未完成</strong>{list(item.series_status.missing_predecessors).map((row) => text(row.name)).join("、")}</p> : null}
                {!integrityVerified ? <p className="card-note rec-risk"><strong>推荐解释已收起</strong>理由与证据未能完全对齐；请先查看作品资料。</p> : fit ? <p className="card-note rec-fit"><strong>为什么适合你</strong>{fit}</p> : null}
                {risk ? <p className="card-note rec-risk"><strong>需要注意</strong>{risk}</p> : null}
                <div className="evidence-row tight">
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
                  {onFeedback && setId && <button type="button" className="inline-action card-action" onClick={() => setFeedbackChoice({ id: Number(item.id), event: "more" })}>多来这种</button>}
                  {onFeedback && setId && <button type="button" className="inline-action card-action" onClick={() => setFeedbackChoice({ id: Number(item.id), event: "less" })}>少来这种</button>}
                </div>
                {choiceOpen ? (
                  <div className="rec-feedback-scope">
                    <label>
                      <span>{feedbackChoice?.event === "more" ? "希望多来哪一方面？" : "希望少来哪一方面？"}</span>
                      <select
                        value={feedbackScope[Number(item.id)] || "item"}
                        onChange={(event) => setFeedbackScope((prev) => ({ ...prev, [Number(item.id)]: event.target.value }))}
                      >
                        <option value="item">只针对这部作品</option>
                        <option value="genre">类似题材</option>
                        <option value="visual">类似画风 / 视觉</option>
                        <option value="pace">类似节奏</option>
                        <option value="length">类似篇幅</option>
                      </select>
                    </label>
                    <button type="button" className="inline-action" onClick={() => void feedback(item, feedbackChoice?.event || "less", undefined, feedbackScope[Number(item.id)] || "item")}>确认</button>
                    <button type="button" className="inline-action" onClick={() => setFeedbackChoice(null)}>取消</button>
                  </div>
                ) : null}
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
                    <button
                      type="button"
                      className="inline-action card-action"
                      onClick={() => {
                        const reason = reasons[Number(item.id)] || "not_interested";
                        const aspect = ["genre", "visual", "pace", "length"].includes(reason) ? reason : "item";
                        void feedback(item, "dismiss", reason, aspect);
                      }}
                    >移除</button>
                  </div>
                )}
                {integrityVerified && (claims.length > 0 || item.review_consensus || list<string>(item.quality_badges).length > 0) ? (
                  <details className="rec-evidence-details">
                    <summary>查看口碑与推荐依据</summary>
                    {fitClaims.slice(0, 4).map((claim, idx) => (
                      <div className="rec-claim" key={`fit-${idx}`}>
                        <strong>{text(claim.text)}</strong>
                        {list<string>(claim.support).length ? <small>依据：{list<string>(claim.support).join("；")}</small> : null}
                      </div>
                    ))}
                    {riskClaims.slice(0, 3).map((claim, idx) => (
                      <div className="rec-claim" key={`risk-${idx}`}>
                        <strong>{text(claim.text)}</strong>
                        {list<string>(claim.support).length ? <small>依据：{list<string>(claim.support).join("；")}</small> : null}
                      </div>
                    ))}
                    {(qualityClaims.length ? qualityClaims : item.review_consensus ? [{ text: item.review_consensus }] : []).slice(0, 2).map((claim, idx) => (
                      <div className="rec-claim rec-quality" key={`quality-${idx}`}><strong>口碑概况</strong><span>{text(claim.text)}</span></div>
                    ))}
                    {list<string>(item.quality_badges).length ? (
                      <div className="evidence-row tight">{list<string>(item.quality_badges).slice(0, 3).map((badge) => <Badge key={badge} tone="warn">{badge}</Badge>)}</div>
                    ) : null}
                    {list(item.external_mappings).length ? <div className="rec-provenance"><small>外站评分对齐</small>{list(item.external_mappings).slice(0, 3).map((mapping, idx) => <span key={`${mapping.source}-${idx}`}>{text(mapping.source)} · {text(mapping.external_title)} · 可信度 {Math.round(Number(mapping.mapping_confidence || 0) * 100)}%</span>)}</div> : null}
                    {(provenanceClaims.length || list<string>(item.why_recalled).length) ? (
                      <div className="rec-provenance">
                        <small>候选是怎么被找到的（不等于适合你的证据）</small>
                        {(provenanceClaims.length ? provenanceClaims.map((claim) => text(claim.text)) : list<string>(item.why_recalled)).slice(0, 4).map((value, idx) => <span key={idx}>{value}</span>)}
                      </div>
                    ) : null}
                  </details>
                ) : null}
                {nextStep && <div className="compact-list inline next-step"><span>{nextStep}</span></div>}
              </div>
            </article>
          );
        })}
      </div>
      {items.length > 3 ? (
        <div className="panel-actions">
          <button type="button" className="ghost" onClick={() => setExpanded((value) => !value)}>{expanded ? "收起其他候选" : `查看另外 ${items.length - 3} 部`}</button>
        </div>
      ) : null}
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
    ["下一季可开", "continue_series"],
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


