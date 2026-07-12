"use client";

// 匿名冷启动口味速配：未登录用户 3 步选完直接出个性化推荐（免注册质量线）。
// 产出一条结构化中文消息交给 agent → recommend_subjects 的 guest 模式（纯标签+冷启动召回）。

import { useState } from "react";

const GENRES = ["治愈日常", "热血战斗", "恋爱", "悬疑推理", "科幻", "奇幻冒险", "搞笑", "百合", "催泪", "运动竞技", "美食", "音乐偶像"];
const AVOIDS = ["致郁", "后宫", "恐怖猎奇", "长篇大坑", "低龄向", "机战", "卖肉"];
const TIMES = ["今晚 1-2 小时看完一部", "这个周末刷一部短篇", "慢慢追这季新番", "想补一部公认神作"];

export function TasteQuiz({ onDone, disabled }: { onDone: (q: string) => void; disabled?: boolean }) {
  const [step, setStep] = useState(0);
  const [likes, setLikes] = useState<string[]>([]);
  const [avoids, setAvoids] = useState<string[]>([]);

  const toggle = (list: string[], set: (v: string[]) => void, v: string, max: number) => {
    if (list.includes(v)) set(list.filter((x) => x !== v));
    else if (list.length < max) set([...list, v]);
  };

  const finish = (time: string) => {
    const like = likes.length ? likes.join("、") : "都行，看质量";
    const avoid = avoids.length ? `，避雷【${avoids.join("、")}】` : "";
    onDone(
      `我是没登录的新用户，帮我口味速配：喜欢【${like}】${avoid}，场景是「${time}」。` +
        "直接推荐几部适合我的动画，每部说明为什么适合，不需要我登录。"
    );
  };

  return (
    <div className="quiz">
      <div className="quiz-title">🎯 30 秒口味速配（不用登录）</div>
      {step === 0 && (
        <>
          <div className="quiz-q">你喜欢什么类型？（最多选 4 个）</div>
          <div className="welcome-chips">
            {GENRES.map((g) => (
              <button key={g} className={`chip ${likes.includes(g) ? "on" : ""}`} disabled={disabled}
                onClick={() => toggle(likes, setLikes, g, 4)}>{g}</button>
            ))}
          </div>
          <button className="quiz-next" disabled={disabled || likes.length === 0} onClick={() => setStep(1)}>下一步</button>
        </>
      )}
      {step === 1 && (
        <>
          <div className="quiz-q">有雷点吗？（可跳过）</div>
          <div className="welcome-chips">
            {AVOIDS.map((a) => (
              <button key={a} className={`chip ${avoids.includes(a) ? "on" : ""}`} disabled={disabled}
                onClick={() => toggle(avoids, setAvoids, a, 3)}>{a}</button>
            ))}
          </div>
          <button className="quiz-next" disabled={disabled} onClick={() => setStep(2)}>
            {avoids.length ? "下一步" : "没有雷点，下一步"}
          </button>
        </>
      )}
      {step === 2 && (
        <>
          <div className="quiz-q">今天想怎么看？</div>
          <div className="welcome-chips">
            {TIMES.map((t) => (
              <button key={t} className="chip" disabled={disabled} onClick={() => finish(t)}>{t}</button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
