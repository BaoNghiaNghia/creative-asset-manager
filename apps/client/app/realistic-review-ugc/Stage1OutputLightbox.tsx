import { useEffect, useRef, useState } from "react";
import type { GenerationOutputVersion } from "./api";
import "./Stage1OutputLightbox.css";

/** Open the exact Stage 1 PNG in CAM, not in the browser's native image viewer. */
export function Stage1OutputLightbox({
  version, versions, onClose, onNavigate,
}: {
  version: GenerationOutputVersion;
  versions: GenerationOutputVersion[];
  onClose: () => void;
  onNavigate: (version: number) => void;
}) {
  const [zoom, setZoom] = useState(0); // 0 = fit canvas; otherwise natural-pixel scale
  const [checkerboard, setCheckerboard] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  const position = versions.findIndex(item => item.version === version.version);
  const name = version.output_name?.split(/[\\/]/).pop() || "Design " + version.version;

  useEffect(() => { setZoom(0); }, [version.version]);
  useEffect(() => { closeButton.current?.focus(); }, []);

  const original = version.url + (version.url.includes("?") ? "&" : "?") + "artwork=stage1-source-alpha-v2";
  const nativeWidth = version.width && version.width > 0 ? version.width : 2048;

  return <div className="rrugc-stage1-lightbox-backdrop" role="presentation"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="rrugc-stage1-lightbox" role="dialog" aria-modal="true"
      aria-label={"Full Stage 1 image: " + name}>
      <header className="rrugc-stage1-lightbox-header">
        <div>
          <small>STAGE 1 · ORIGINAL PNG</small>
          <strong>{name}</strong>
          <span>{version.width || "—"} × {version.height || "—"} · v{version.version} · {position + 1}/{versions.length}</span>
        </div>
        <button ref={closeButton} type="button" aria-label="Close full image" onClick={onClose}>×</button>
      </header>
      <div className="rrugc-stage1-lightbox-toolbar" role="toolbar" aria-label="Full image controls">
        <button type="button" aria-label="Previous full image" disabled={position <= 0}
          onClick={() => onNavigate(versions[position - 1].version)}>‹</button>
        <button type="button" aria-label="Next full image" disabled={position < 0 || position >= versions.length - 1}
          onClick={() => onNavigate(versions[position + 1].version)}>›</button>
        <span className="rrugc-stage1-lightbox-separator" />
        <button type="button" aria-label="Zoom out" disabled={zoom === 0}
          onClick={() => setZoom(value => value <= 0.5 ? 0 : value / 2)}>−</button>
        <button type="button" aria-label="Fit image to screen" aria-pressed={zoom === 0}
          onClick={() => setZoom(0)}>Fit</button>
        <button type="button" aria-label="Zoom in" disabled={zoom >= 2}
          onClick={() => setZoom(value => value === 0 ? 0.5 : Math.min(2, value * 2))}>+</button>
        <span className="rrugc-stage1-lightbox-zoom">{zoom === 0 ? "Fit" : Math.round(zoom * 100) + "%"}</span>
        <button type="button" className="rrugc-stage1-lightbox-background-toggle"
          aria-label="Toggle checkerboard background" aria-pressed={checkerboard}
          onClick={() => setCheckerboard(value => !value)}>{checkerboard ? "White background" : "Checkerboard"}</button>
        <a className="rrugc-stage1-lightbox-download"
          href={version.url + (version.url.includes("?") ? "&" : "?") + "download=true"} download>
          Download original
        </a>
      </div>
      <div className={"rrugc-stage1-lightbox-canvas" + (checkerboard ? " is-checkerboard" : "")}
        aria-label={"Image canvas for " + name}>
        <img key={version.url} src={original} alt={name} draggable={false}
          style={zoom > 0 ? {
            width: nativeWidth * zoom + "px", maxWidth: "none", maxHeight: "none",
          } : undefined} />
      </div>
      <footer className="rrugc-stage1-lightbox-footer">
        Full original PNG · no background removal or cropping · Esc to close · ←/→ to browse
      </footer>
    </section>
  </div>;
}
