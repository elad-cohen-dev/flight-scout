import { useEffect, useRef, useState } from "react";
import type { AgentEvent } from "../api";
import { searchTally } from "../lib/providers";
import Icon from "./Icon";

/** Live "agent console": streams the agent's research prose, web-search calls
 *  and phase changes while a search runs — the LLM-chat feel instead of a
 *  frozen "searching…" label. */
export default function AgentConsole({
  title,
  events,
  onStop,
  stopping,
}: {
  title: string;
  events: AgentEvent[];
  onStop: () => void;
  stopping: boolean;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const t = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  // Merge consecutive text deltas into paragraphs; phases/searches break them.
  const items: { type: "phase" | "search" | "text"; text: string }[] = [];
  for (const e of events) {
    if (e.kind === "delta") {
      const last = items[items.length - 1];
      if (last && last.type === "text") last.text += e.text;
      else items.push({ type: "text", text: e.text });
    } else if (e.kind === "phase" || e.kind === "search") {
      items.push({ type: e.kind, text: e.text });
    }
  }
  const phase = [...items].reverse().find((i) => i.type === "phase")?.text;

  return (
    <section className="agent-console card" aria-label="Live agent progress">
      <div className="agent-console-head">
        <span className="agent-dot" aria-hidden="true" />
        <div className="agent-console-title">
          <strong>{title}</strong>
          <span className="muted">
            {phase ?? "Starting"} · {searchTally(events)} · {Math.floor(elapsed / 60)}:
            {String(elapsed % 60).padStart(2, "0")}
          </span>
        </div>
        <button className="btn btn-ghost btn-sm btn-stop" onClick={onStop} disabled={stopping}>
          <Icon name="stop" size={14} /> {stopping ? "Stopping…" : "Stop"}
        </button>
      </div>
      <div className="agent-console-body" ref={bodyRef} aria-live="off">
        {items.length === 0 && <div className="agent-line agent-phase">Spinning up the agent…</div>}
        {items.map((item, i) =>
          item.type === "text" ? (
            <p key={i} className="agent-text">
              {item.text}
              {i === items.length - 1 && <span className="agent-cursor">▋</span>}
            </p>
          ) : (
            <div key={i} className={`agent-line agent-${item.type}`}>
              <Icon name={item.type === "search" ? "search" : "chevronRight"} size={13} />
              <span>{item.text}</span>
            </div>
          )
        )}
      </div>
    </section>
  );
}
