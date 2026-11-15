import { Maximize2, Minimize2, Pause, Play, X } from "lucide-react";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { formatDuration } from "@/lib/format";
import { isAndroid } from "@/lib/platform";
import { toResult, toSource, type Cuts } from "@/lib/timeline";
import { cn } from "@/lib/utils";

/** A small player floating over the page, first at the bottom left just above the bottom
 * bar. Dragged anywhere in the window (by any part but its controls), it moves off the
 * options it hides. */
export function MiniPlayer({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  const { t } = useTranslation();
  // Where it was dragged to; left and top, not a transform, which would hold the full-screen view.
  const [spot, setSpot] = useState<{ left: number; top: number } | null>(null);

  function drag(e: ReactPointerEvent<HTMLDivElement>) {
    if (e.button !== 0 || (e.target as Element).closest("button, [role=slider], [data-expanded]")) return;
    const box = e.currentTarget;
    const rect = box.getBoundingClientRect();
    const [dx, dy] = [e.clientX - rect.left, e.clientY - rect.top];
    const clamp = (value: number, max: number) => Math.min(Math.max(value, 0), Math.max(max, 0));
    const move = (m: PointerEvent) =>
      setSpot({ left: clamp(m.clientX - dx, innerWidth - rect.width), top: clamp(m.clientY - dy, innerHeight - rect.height) });
    const drop = () => {
      box.removeEventListener("pointermove", move);
      box.removeEventListener("pointerup", drop);
      box.removeEventListener("pointercancel", drop);
    };
    box.setPointerCapture(e.pointerId);
    box.addEventListener("pointermove", move);
    box.addEventListener("pointerup", drop);
    box.addEventListener("pointercancel", drop);
  }

  return (
    <div
      onPointerDown={drag}
      style={spot ? { ...spot, bottom: "auto" } : undefined}
      className={cn(
        "fixed left-3 z-30 w-72 cursor-grab touch-none overflow-hidden rounded-lg border bg-background shadow-xl select-none active:cursor-grabbing sm:left-4 sm:w-80",
        isAndroid ? "bottom-[calc(8.75rem+1px+env(safe-area-inset-bottom))]" : "bottom-[4.75rem]",
      )}
    >
      {children}
      {/* Shows it can be dragged. */}
      <span aria-hidden className="pointer-events-none absolute top-1.5 left-1/2 h-1 w-8 -translate-x-1/2 rounded-full bg-muted-foreground/70" />
      <button
        type="button"
        aria-label={t("edit.closePreview")}
        onClick={onClose}
        className="absolute top-1.5 right-1.5 flex size-7 items-center justify-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/80"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}

/** Draws the video's current picture into `canvas` at `height` (short side) at most. */
function paint(video: HTMLVideoElement, canvas: HTMLCanvasElement, height: number | null) {
  if (!video.videoWidth) return;
  const scale = height ? Math.min(1, height / Math.min(video.videoWidth, video.videoHeight)) : 1;
  const [width, tall] = [Math.round(video.videoWidth * scale), Math.round(video.videoHeight * scale)];
  if (canvas.width !== width || canvas.height !== tall) [canvas.width, canvas.height] = [width, tall];
  const context = canvas.getContext("2d");
  if (!context) return;
  context.imageSmoothingQuality = "high";
  context.drawImage(video, 0, 0, width, tall);
}

/** Plays `src` as the result will be: from `start` to `end` without the `cuts`, at `speed`,
 * muted or not, its picture drawn at the chosen `height` and frame rate (the stream is
 * played under a canvas it is copied into), then back to `start`. When an edge moves, it
 * shows the picture there. `onUnplayable` when the webview can't play it. */
export function ExtractPlayer({
  src,
  video,
  start,
  end,
  cuts = [],
  speed = 1,
  muted = false,
  height = null,
  fps = null,
  sourceFps = null,
  onUnplayable,
}: {
  src: string;
  /** The result has a picture; otherwise only the sound plays. */
  video: boolean;
  start: number;
  end: number;
  cuts?: Cuts;
  speed?: number;
  muted?: boolean;
  /** The short side chosen (720 for 720p); `null` keeps the stream's. */
  height?: number | null;
  /** A lower frame rate chosen; `null` keeps the source's. */
  fps?: number | null;
  /** The source's frame rate: a speed-up keeps it rather than packing in more frames. */
  sourceFps?: number | null;
  onUnplayable: () => void;
}) {
  const { t } = useTranslation();
  const media = useRef<HTMLMediaElement | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [playing, setPlaying] = useState(false);
  // Seconds into the result.
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(Number.POSITIVE_INFINITY);
  const [expanded, setExpanded] = useState(false);
  const drawn = fps ?? (speed > 1 ? sourceFps : null);
  const stop = Math.min(end, duration);
  const total = toResult(stop, start, cuts, speed);

  // What the playback loop reads: it runs across renders.
  const latest = useRef({ start, stop, cuts, speed, height, drawn });
  useEffect(() => {
    latest.current = { start, stop, cuts, speed, height, drawn };
  });
  // When the next picture is due, in animation frame time.
  const due = useRef(0);
  // Where to seek once the running seek is done: a new seek cancels the running one, so a
  // drag would show no picture until it stops.
  const queued = useRef<number | null>(null);

  function seekTo(time: number) {
    const m = media.current;
    if (!m) return;
    if (m.seeking) queued.current = time;
    else m.currentTime = time;
  }

  function draw() {
    if (media.current instanceof HTMLVideoElement && canvas.current) paint(media.current, canvas.current, latest.current.height);
  }

  // Each animation frame while playing: skips the cuts, stops on the end (not up to 250 ms
  // later), and draws a picture as often as the result has one.
  function tick(time: number) {
    const m = media.current;
    if (!m || m.paused) return;
    const { start, stop, cuts, speed, drawn } = latest.current;
    const cut = cuts.find(([a, b]) => m.currentTime >= a && m.currentTime < b);
    if (cut) m.currentTime = cut[1];
    if (m.currentTime >= stop) {
      m.pause();
      m.currentTime = start;
      return;
    }
    if (!drawn) {
      draw();
    } else if (time >= due.current) {
      draw();
      const every = 1000 / drawn;
      due.current = time - due.current > every ? time + every : due.current + every;
    }
    setPosition(Math.round(toResult(m.currentTime, start, cuts, speed) * 10) / 10);
    requestAnimationFrame(tick);
  }

  const edges = useRef({ start, end });
  useEffect(() => {
    const m = media.current;
    const endMoved = edges.current.end !== end;
    edges.current = { start, end };
    if (!m || m.readyState < 1) return;
    m.pause();
    // Just before the end: the very end of a file shows no picture.
    seekTo(endMoved ? Math.max(end - 0.05, start) : start);
  }, [start, end]);

  useEffect(() => {
    if (media.current) media.current.defaultPlaybackRate = media.current.playbackRate = speed;
  }, [speed]);

  useEffect(() => {
    if (media.current instanceof HTMLVideoElement && canvas.current) paint(media.current, canvas.current, height);
  }, [height]);

  useEffect(() => {
    if (!expanded) return;
    const close = (e: KeyboardEvent) => e.key === "Escape" && setExpanded(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [expanded]);

  function seek(r: number) {
    seekTo(toSource(r, start, cuts, speed));
    setPosition(r);
  }

  const props = {
    ref: (element: HTMLMediaElement | null) => {
      media.current = element;
    },
    src,
    preload: "metadata",
    muted,
    className: "hidden",
    onError: onUnplayable,
    onLoadedMetadata: (e: SyntheticEvent<HTMLMediaElement>) => {
      const m = e.currentTarget;
      // A picture the webview can't decode (HEVC…) loads with no size.
      if (video && (m as HTMLVideoElement).videoWidth === 0) return onUnplayable();
      setDuration(m.duration);
      m.defaultPlaybackRate = m.playbackRate = speed;
      m.currentTime = edges.current.start;
    },
    onSeeked: () => {
      draw();
      const m = media.current;
      if (m && queued.current !== null) {
        m.currentTime = queued.current;
        queued.current = null;
      }
      if (m?.paused) setPosition(Math.round(toResult(m.currentTime, start, cuts, speed) * 10) / 10);
    },
    onPlay: (e: SyntheticEvent<HTMLMediaElement>) => {
      const m = e.currentTarget;
      if (m.currentTime < start || m.currentTime >= stop - 0.05) m.currentTime = start;
      setPlaying(true);
      due.current = 0;
      requestAnimationFrame(tick);
    },
    onPause: () => setPlaying(false),
    onEnded: (e: SyntheticEvent<HTMLMediaElement>) => {
      e.currentTarget.currentTime = start;
    },
  };

  const controls = (
    <div className={cn("flex items-center gap-2 px-1.5 py-1", !video && "pr-10")}>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t(playing ? "edit.pause" : "edit.play")}
        onClick={() => {
          const m = media.current;
          if (m?.paused) void m.play().catch(() => {});
          else m?.pause();
        }}
      >
        {playing ? <Pause className="fill-current" /> : <Play className="fill-current" />}
      </Button>
      <span className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">
        {formatDuration(position)} / {Number.isFinite(total) ? formatDuration(total) : "–"}
      </span>
      <Slider
        className="flex-1 py-2"
        min={0}
        max={Number.isFinite(total) ? Math.max(total, 0.1) : 0.1}
        step={0.1}
        value={[Math.min(position, Number.isFinite(total) ? total : 0)]}
        onValueChange={([r]) => seek(r)}
        thumbLabels={[t("edit.position")]}
      />
      {video && (
        <Button
          variant="ghost"
          size="icon"
          aria-label={t(expanded ? "edit.exitFullScreen" : "edit.fullScreen")}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? <Minimize2 /> : <Maximize2 />}
        </Button>
      )}
    </div>
  );

  if (!video) {
    return (
      <div>
        {/* The key reloads the player when another file opens. */}
        <audio key={src} {...props} />
        {controls}
      </div>
    );
  }
  return (
    <div
      data-expanded={expanded || undefined}
      className={cn(
        expanded &&
          "fixed inset-0 z-50 flex cursor-auto flex-col bg-background pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]",
      )}
    >
      <div className={cn("relative w-full bg-black", expanded ? "min-h-0 flex-1" : "aspect-video")}>
        {/* Shown under the canvas: Android's webview gives no pictures of a hidden video. */}
        <video key={src} {...props} playsInline className="absolute inset-0 size-full object-contain" />
        <canvas ref={canvas} role="img" aria-label={t("edit.preview")} className="absolute inset-0 size-full object-contain" />
      </div>
      {controls}
    </div>
  );
}
