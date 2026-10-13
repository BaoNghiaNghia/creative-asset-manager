import { useState } from "react";
import "./UnderConstructionPage.css";
import { BrandIcon } from "./Icons";

export function UnderConstructionPage({ maintenance = false }: { maintenance?: boolean }) {
  const [videoFailed, setVideoFailed] = useState(false);
  return (
    <main className="cam-under-construction">
      <a className="cam-under-brand" href="/" aria-label="Creative assets - Asset Explorer">
        <span className="cam-under-brand-mark"><BrandIcon /></span>
        <span>Creative assets</span>
      </a>

      <section className="cam-under-body" aria-labelledby="cam-under-title">
        <div className={"cam-under-scene" + (videoFailed ? " is-fallback" : "")}>
          <video
            className="cam-under-video"
            autoPlay
            onError={() => setVideoFailed(true)}
            muted
            loop
            playsInline
            preload="metadata"
            poster="/construction-media/poster.jpg"
            aria-label="Animated construction crane assembling a website"
          >
            <source src="/construction-media/animation.mp4" type="video/mp4" />
          </video>
          <img
            className="cam-under-still"
            src="/construction-media/poster.jpg"
            alt="Construction crane building a website"
            width="960"
            height="658"
          />
        </div>

        <div className="cam-under-eyebrow"><span aria-hidden="true" className="cam-under-dot" /> WORK IN PROGRESS</div>
        <h1 id="cam-under-title">Under Construction<span>.</span></h1>
        <p className="cam-under-detail">
          {maintenance
            ? "We're updating Creative assets. The workspace will return automatically when the update is complete."
            : "We're building something better. This workspace is being prepared and will be available soon."}
        </p>
        {maintenance
          ? <button className="cam-under-button" type="button" onClick={() => window.location.reload()}>
              Try again <svg className="cam-under-back-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 11a8 8 0 1 0 2-5M4 4v5h5" /></svg>
            </button>
          : <a className="cam-under-button" href="/">
              Back to Asset Explorer <svg className="cam-under-back-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 15 15 5M6 5h9v9" /></svg>
            </a>}
      </section>

      <footer className="cam-under-footer">Creative Asset Management · Made for what comes next</footer>
    </main>
  );
}
