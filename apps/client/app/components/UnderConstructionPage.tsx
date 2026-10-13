import "./UnderConstructionPage.css";

export function UnderConstructionPage() {
  return (
    <main className="cam-under-construction">
      <a className="cam-under-brand" href="/" aria-label="Creative assets - Asset Explorer">
        <img src="/app-icon-192.png" alt="" width="38" height="38" />
        <span>Creative assets</span>
      </a>

      <section className="cam-under-body" aria-labelledby="cam-under-title">
        <div className="cam-under-scene">
          <video
            className="cam-under-video"
            autoPlay
            muted
            loop
            playsInline
            preload="metadata"
            poster="/under-construction/poster.jpg"
            aria-label="Animated construction crane assembling a website"
          >
            <source src="/under-construction/animation.mp4" type="video/mp4" />
          </video>
          <img
            className="cam-under-still"
            src="/under-construction/poster.jpg"
            alt="Construction crane building a website"
            width="960"
            height="658"
          />
        </div>

        <div className="cam-under-eyebrow"><span aria-hidden="true" className="cam-under-dot" /> WORK IN PROGRESS</div>
        <h1 id="cam-under-title">Under Construction<span>.</span></h1>
        <p className="cam-under-detail">
          We're building something better. This workspace is being prepared and will be available soon.
        </p>
        <a className="cam-under-button" href="/">
          Back to Asset Explorer <span aria-hidden="true">↗</span>
        </a>
      </section>

      <footer className="cam-under-footer">Creative Asset Management · Made for what comes next</footer>
    </main>
  );
}
