import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { generateKeywordCreativeBrief, getKeywordCreativeBrief } from "./api";
import type { KeywordCreativeBriefDetail } from "./types";
import "./CreativeBriefDialog.css";

const MODE_LABEL: Record<string, string> = {
  safe: "Typography-First",
  creative_twist: "Creative Twist",
  explore: "Explore",
};

export function CreativeBriefDialog({ keywordId, keyword, onClose }: {
  keywordId: string;
  keyword: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<KeywordCreativeBriefDetail | null>(null);
  const [selected, setSelected] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    const load = async () => {
      try {
        const result = await getKeywordCreativeBrief(keywordId, controller.signal);
        if (alive) { setDetail(result); setError(""); }
      } catch (reason) {
        if (alive) setError(reason instanceof Error ? reason.message : "Could not load Creative Brief.");
      }
    };
    void load();
    const poll = window.setInterval(() => {
      if (!document.hidden && alive) void load();
    }, 5000);
    return () => { alive = false; controller.abort(); window.clearInterval(poll); };
  }, [keywordId, refreshToken]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const analyze = async () => {
    setBusy(true);
    setError("");
    try {
      await generateKeywordCreativeBrief(keywordId);
      setRefreshToken(value => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not queue analysis.");
    } finally {
      setBusy(false);
    }
  };

  const brief = detail?.brief;
  const concept = brief?.concepts[selected] || brief?.concepts[0];
  const generating = detail?.status === "queued" || detail?.status === "running";
  return createPortal(
    <div className="rrugc-creative-brief-backdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section role="dialog" aria-modal="true" aria-label={"Creative Brief for " + keyword}
        className="rrugc-creative-brief-dialog">
        <header className="rrugc-creative-brief-heading">
          <div>
            <small>CREATIVE BRIEF · KEYWORD</small>
            <h2>{keyword}</h2>
          </div>
          <button type="button" aria-label="Close Creative Brief" onClick={onClose}>×</button>
        </header>
        {error && <p className="rrugc-creative-brief-error" role="alert">{error}</p>}
        {!detail && !error && <p role="status">Loading Creative Brief…</p>}
        {detail && !brief && <div className="rrugc-creative-brief-pending">
          <p role="status">{detail.status === "not_analyzed" || detail.status === "stale"
            ? "Not analyzed yet."
            : detail.status === "failed" ? "Creative Brief analysis failed. You can retry."
              : detail.status === "queued" ? "Queued for text-only Codex analysis."
                : "Codex is analyzing the keyword…"}</p>
          {!generating && <button type="button" onClick={() => void analyze()} disabled={busy}>
            {busy ? "Queueing…" : detail.status === "failed" ? "Retry analysis" : "Analyze keyword"}
          </button>}
          <small>Analysis does not regenerate or modify existing Stage 1 images.</small>
        </div>}
        {brief && <>
          <div className="rrugc-creative-brief-facts">
            <div><strong>Meaning / Emotion</strong><p>{brief.meaning_emotion}</p></div>
            <div><strong>Target Audience</strong><p>{brief.target_audience}</p></div>
            <div><strong>Purchase Motivation</strong><p>{brief.purchase_motivation}</p></div>
            <div><strong>Creative Hook</strong><p>{brief.creative_hook}</p></div>
          </div>
          <div className="rrugc-creative-brief-proposals">
            <h3>Concept Proposals</h3>
            {brief.concepts.map((item, index) =>
              <button type="button" key={item.mode} onClick={() => setSelected(index)}
                className={selected === index ? "active" : ""} aria-pressed={selected === index}>
                <span><b aria-hidden="true">{selected === index ? "◉" : "○"}</b>
                  <strong>{index + 1}. {item.name}</strong>
                  <small>{MODE_LABEL[item.mode] || item.mode}</small>
                </span>
                <p>{item.description}</p>
              </button>)}
          </div>
          {concept && <div className="rrugc-creative-brief-concept">
            <h3>Concept Detail — {concept.name}</h3>
            <dl>
              <dt>Familiar Format</dt><dd>{concept.familiar_format}</dd>
              <dt>Creative Twist</dt><dd>{concept.creative_twist}</dd>
              <dt>Creative Hook</dt><dd>{concept.creative_hook}</dd>
              <dt>Typography</dt><dd>{concept.typography_plan}</dd>
              <dt>Embroidery</dt><dd>{concept.embroidery_plan}</dd>
              <dt>Hiểu trong 2 giây</dt><dd>{concept.clarity_assessment}</dd>
              <dt>Khả năng thêu</dt><dd>{concept.embroidery_fit}</dd>
              {concept.risks.length > 0 && <><dt>Risks</dt><dd>{concept.risks.join(" · ")}</dd></>}
            </dl>
            <p className="rrugc-creative-brief-note">Đánh giá sơ bộ của AI Planner, cần kiểm tra sau khi có artwork. Các concept chưa tự động thay thế generation skill hiện tại.</p>
          </div>}
        </>}
        <footer><span>{detail?.prompt_version || "Meaning-First V1"} · Text-only · No ImageGen</span>
          <button type="button" onClick={onClose}>Close</button></footer>
      </section>
    </div>, document.body,
  );
}
