import { useEffect, useMemo, useRef } from "react";
import { useStore } from "../state/store";

const SPEEDS = [0.5, 1, 2, 4, 12];

export function Playback() {
  const replay = useStore((s) => s.replay);
  const trace = useStore((s) => s.trace);
  const step = useStore((s) => s.step);
  const playing = useStore((s) => s.playing);
  const speed = useStore((s) => s.speed);
  const setStep = useStore((s) => s.setStep);
  const stepBy = useStore((s) => s.stepBy);
  const setPlaying = useStore((s) => s.setPlaying);
  const setSpeed = useStore((s) => s.setSpeed);

  const frame = useRef<number>(0);
  const carry = useRef(0);
  const last = useRef(0);

  // A time-accumulating rAF loop rather than setInterval: changing speed must
  // not restart the timer, and it pauses automatically in a hidden tab.
  useEffect(() => {
    if (!playing || !replay) return;
    last.current = performance.now();
    carry.current = 0;

    const tick = (now: number) => {
      const elapsed = (now - last.current) / 1000;
      last.current = now;
      carry.current += elapsed * 12 * speed;
      const advance = Math.floor(carry.current);
      if (advance > 0) {
        carry.current -= advance;
        const next = useStore.getState().step + advance;
        if (next >= replay.length - 1) {
          setStep(replay.length - 1);
          setPlaying(false);
          return;
        }
        setStep(next);
      }
      frame.current = requestAnimationFrame(tick);
    };

    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [playing, speed, replay, setStep, setPlaying]);

  const markers = useMemo(() => replay?.markers() ?? [], [replay]);

  if (!replay || !trace) return null;
  const total = replay.length;
  const progress = ((step + 1) / Math.max(total, 1)) * 100;

  return (
    <div className="playback">
      <div className="transport">
        <button onClick={() => setStep(-1)} title="Restart">⏮</button>
        <button onClick={() => stepBy(-1)} title="Step back">◀</button>
        <button className="play" onClick={() => setPlaying(!playing)}>
          {playing ? "⏸" : "▶"}
        </button>
        <button onClick={() => stepBy(1)} title="Step forward">▶</button>
        <button onClick={() => setStep(total - 1)} title="Jump to end">⏭</button>
        <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
          {SPEEDS.map((value) => <option key={value} value={value}>{value}×</option>)}
        </select>
      </div>

      <div className="track">
        <div className="track-fill" style={{ width: `${progress}%` }} />
        {markers.map((marker, index) => (
          <span key={index} className={`marker marker-${marker.kind}`} title={marker.label}
            style={{ left: `${((marker.index + 1) / Math.max(total, 1)) * 100}%` }} />
        ))}
        <input type="range" min={-1} max={total - 1} value={step}
          onChange={(event) => { setStep(Number(event.target.value)); setPlaying(false); }} />
      </div>

      <div className="counter">
        <b>{step + 1}</b> / {total} events
        {trace.truncated && (
          <span className="warn" title={`${trace.dropped} events were not recorded`}>
            truncated
          </span>
        )}
      </div>
    </div>
  );
}
