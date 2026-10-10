import { useEffect, useRef, useState, type ImgHTMLAttributes } from "react";

const RRUGC_IMAGE_CONCURRENCY = 8;

type QueueTicket = {
  cancel: () => void;
  release: () => void;
};

type QueueTask = {
  active: boolean;
  done: boolean;
  start: () => void;
};

function createImageQueue(limit = RRUGC_IMAGE_CONCURRENCY) {
  let active = 0;
  const pending: QueueTask[] = [];

  function drain() {
    while (active < limit && pending.length) {
      const task = pending.shift();
      if (!task || task.done) continue;
      task.active = true;
      active += 1;
      task.start();
    }
  }

  function finish(task: QueueTask) {
    if (task.done) return;
    task.done = true;
    if (task.active) active -= 1;
    else {
      const index = pending.indexOf(task);
      if (index >= 0) pending.splice(index, 1);
    }
    drain();
  }

  return {
    acquire(start: () => void): QueueTicket {
      const task: QueueTask = { active: false, done: false, start };
      pending.push(task);
      drain();
      return {
        cancel: () => finish(task),
        release: () => finish(task),
      };
    },
  };
}

const imageQueue = createImageQueue();

type DeferredImageProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  src: string;
  rootMargin?: string;
};

export function DeferredImage({
  src,
  rootMargin = "220px",
  className = "",
  onLoad,
  onError,
  ...props
}: DeferredImageProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const ticketRef = useRef<QueueTicket | null>(null);
  const settledSourceRef = useRef<string | null>(null);
  const [nearViewport, setNearViewport] = useState(false);
  const [grantedSource, setGrantedSource] = useState<string | null>(null);
  const [loadedSource, setLoadedSource] = useState<string | null>(null);
  const [failedSource, setFailedSource] = useState<string | null>(null);

  useEffect(() => {
    ticketRef.current?.cancel();
    ticketRef.current = null;
    settledSourceRef.current = null;
    setNearViewport(false);
    setGrantedSource(null);
    setLoadedSource(null);
    setFailedSource(null);
  }, [src]);

  useEffect(() => {
    const target = imageRef.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(entries => {
      setNearViewport(entries.some(entry => entry.isIntersecting));
    }, { rootMargin });
    observer.observe(target);
    return () => observer.disconnect();
  }, [rootMargin, src]);

  useEffect(() => {
    if (!nearViewport || !src) {
      // A settled image must stay mounted when scrolled offscreen. Dropping
      // its src here causes a new load, skeleton flash and gallery reflow
      // whenever the user scrolls back up.
      if (settledSourceRef.current === src) return;
      ticketRef.current?.cancel();
      ticketRef.current = null;
      setGrantedSource(current => current === null ? current : null);
      setLoadedSource(current => current === null ? current : null);
      return;
    }
    if (settledSourceRef.current === src) return;
    const ticket = imageQueue.acquire(() => setGrantedSource(src));
    ticketRef.current = ticket;
    return () => {
      ticket.cancel();
      if (ticketRef.current === ticket) ticketRef.current = null;
    };
  }, [nearViewport, src]);

  function release() {
    ticketRef.current?.release();
    ticketRef.current = null;
  }

  const renderedSource = grantedSource === src ? grantedSource : undefined;
  const loaded = loadedSource === src;
  const failed = failedSource === src;

  return <><img
    {...props}
    ref={imageRef}
    src={renderedSource}
    className={(className + " rrugc-deferred-img" + (loaded ? " is-loaded" : "") + (failed ? " is-failed" : "")).trim()}
    decoding={props.decoding ?? "async"}
    loading={props.loading ?? "lazy"}
    onLoad={event => {
      settledSourceRef.current = src;
      setLoadedSource(src);
      setFailedSource(null);
      release();
      onLoad?.(event);
    }}
    onError={event => {
      settledSourceRef.current = src;
      setFailedSource(src);
      release();
      onError?.(event);
    }}
  />{failed && <span className="rrugc-deferred-error" role="status">Image unavailable</span>}</>;
}
