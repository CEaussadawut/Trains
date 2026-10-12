import { useEffect, useState } from "react";

const MIN_WIDTH = 1240;
const DISMISSED = "metro:desktop-notice-dismissed";

const remembered = (): boolean => {
  try {
    return sessionStorage.getItem(DISMISSED) === "1";
  } catch {
    // Private windows and blocked site data throw on access.
    return false;
  }
};

/**
 * The layout is three fixed columns with a scrubbable timeline, and panning the
 * map assumes a mouse. Rather than pretend it adapts, say so plainly on a small
 * or touch screen and let the visitor through anyway.
 */
export function DesktopOnly() {
  const [cramped, setCramped] = useState(false);
  const [dismissed, setDismissed] = useState(remembered);

  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${MIN_WIDTH - 1}px), (pointer: coarse)`);
    const sync = () => setCramped(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  if (!cramped || dismissed) return null;

  const dismiss = () => {
    try {
      sessionStorage.setItem(DISMISSED, "1");
    } catch {
      // Not being able to remember the dismissal is not worth failing over.
    }
    setDismissed(true);
  };

  return (
    <div className="scrim" role="dialog" aria-modal="true" aria-labelledby="desktop-only-title">
      <div className="modal">
        <h2 id="desktop-only-title">Built for a desktop screen</h2>
        <p>
          This visualizer was designed for a wide window and a mouse. The network
          map, the playback timeline and the inspector panels sit side by side and
          need roughly <b>1240&nbsp;pixels</b> of width, and panning the map works
          by dragging with a pointer.
        </p>
        <p>
          On a phone or a narrow window the panels stack and the map gets small.
          Everything still works, but it will not look the way it was designed.
        </p>
        <button type="button" className="primary" onClick={dismiss}>
          Continue anyway
        </button>
      </div>
    </div>
  );
}
