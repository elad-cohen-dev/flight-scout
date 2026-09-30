import { useEffect, useId, useMemo, useRef, useState } from "react";
import { isPicked, loadAirports, searchAirports, splitLastSegment } from "../lib/airports";
import type { AirportIndex, Place, Suggestion } from "../lib/airports";
import "./airport-input.css";

const RECENTS_KEY = "flight-scout.airport-recents";
const MAX_RECENTS = 5;

function readRecents(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function saveRecent(value: string) {
  try {
    const next = [value, ...readRecents().filter((v) => v !== value)].slice(0, MAX_RECENTS);
    localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: recents are a convenience */
  }
}

const GLYPHS: Record<Place["kind"], string> = {
  airport:
    "M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z",
  metro: "M3 21h18M5 21V7l6-4v18M11 21V9l8 4v8M8 9v.01M8 12v.01M8 15v.01M15 15v.01M15 18v.01",
  country: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20",
  region: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20",
};

function Glyph({ kind }: { kind: Place["kind"] }) {
  return (
    <svg className="ai-glyph" width={18} height={18} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={GLYPHS[kind]} />
    </svg>
  );
}

function describe({ place: p, child }: Suggestion): { main: string; sub: string } {
  if (child) return { main: p.name, sub: "" };
  if (p.kind === "metro") return { main: `${p.city} — all airports`, sub: p.country };
  if (p.kind === "airport") return { main: p.city, sub: p.name === p.city ? p.country : `${p.name} · ${p.country}` };
  return { main: p.city, sub: p.name };
}

/**
 * City / airport combobox (WAI-ARIA 1.2 combobox with a listbox popup). Free
 * text always stays valid: suggestions only complete the segment being typed
 * (the last one in "Prague, Vie…"), and nothing is forced on blur or submit.
 */
export default function AirportInput({
  label,
  value,
  onChange,
  placeholder,
  required,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  required?: boolean;
}) {
  const id = useId();
  const listId = `${id}-list`;
  const inputRef = useRef<HTMLInputElement>(null);
  const [index, setIndex] = useState<AirportIndex | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [recents, setRecents] = useState<string[]>([]);

  const { head, term } = splitLastSegment(value);
  const showRecents = !term.trim();

  const items: Suggestion[] = useMemo(() => {
    if (!index || isPicked(term)) return [];
    if (showRecents) {
      return recents.flatMap((v) => {
        const place = index.byValue.get(v);
        return place ? [{ place, child: false }] : [];
      });
    }
    return searchAirports(index, term);
  }, [index, term, showRecents, recents]);

  const expanded = open && items.length > 0;

  const ensureIndex = () => {
    if (!index) loadAirports().then(setIndex, () => {});
  };

  useEffect(() => {
    if (active < 0) return;
    document.getElementById(`${id}-opt-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [active, id]);

  const openList = () => {
    setRecents(readRecents());
    setActive(-1);
    setOpen(true);
  };

  const pick = (place: Place) => {
    onChange(head + place.value);
    saveRecent(place.value);
    setOpen(false);
    setActive(-1);
    inputRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const n = items.length;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        if (!expanded) {
          openList();
          if (n) setActive(0);
        } else setActive((a) => (a + 1) % n);
        break;
      case "ArrowUp":
        if (!expanded) return;
        e.preventDefault();
        setActive((a) => (a <= 0 ? n - 1 : a - 1));
        break;
      case "Enter":
        // With nothing highlighted, Enter submits the form with the free text as typed.
        if (expanded && active >= 0) {
          e.preventDefault();
          pick(items[active].place);
        }
        break;
      case "Escape":
        if (expanded) {
          e.preventDefault();
          e.stopPropagation();
          setOpen(false);
          setActive(-1);
        }
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  };

  return (
    <div className="field ai-field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="ai-wrap">
        <input
          ref={inputRef}
          id={id}
          role="combobox"
          aria-expanded={expanded}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={expanded && active >= 0 ? `${id}-opt-${active}` : undefined}
          value={value}
          placeholder={placeholder}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          enterKeyHint="next"
          required={required}
          onFocus={() => {
            ensureIndex();
            // Don't pop the list over a value that is already filled in; recents only for an empty segment.
            if (!term.trim()) openList();
          }}
          onClick={() => {
            if (!open && !term.trim()) openList();
          }}
          onBlur={() => {
            setOpen(false);
            setActive(-1);
          }}
          onChange={(e) => {
            ensureIndex();
            onChange(e.target.value);
            setRecents(readRecents());
            setActive(-1);
            setOpen(true);
          }}
          onKeyDown={onKeyDown}
        />
        <ul id={listId} role="listbox" aria-label={`${label} suggestions`} className="ai-list" hidden={!expanded}>
          {expanded && showRecents && (
            <li role="presentation" className="ai-head">
              Recent
            </li>
          )}
          {expanded &&
            items.map((s, i) => {
              const { main, sub } = describe(s);
              return (
                <li
                  key={`${s.place.kind}-${s.place.value}-${s.child ? "c" : ""}`}
                  id={`${id}-opt-${i}`}
                  role="option"
                  aria-selected={i === active}
                  aria-label={s.place.code ? `${s.child ? `${s.place.city} ` : ""}${main}, ${s.place.code}` : main}
                  className={`ai-opt${s.child ? " ai-child" : ""}${i === active ? " is-active" : ""}`}
                  data-kind={s.place.kind}
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseMove={() => i !== active && setActive(i)}
                  onClick={() => pick(s.place)}
                >
                  <Glyph kind={s.place.kind} />
                  <span className="ai-text">
                    <span className="ai-main">{main}</span>
                    {sub && <span className="ai-sub">{sub}</span>}
                  </span>
                  {s.place.code && <span className="ai-code">{s.place.code}</span>}
                </li>
              );
            })}
        </ul>
        <span className="sr-only" role="status" aria-live="polite">
          {expanded ? `${items.length} suggestion${items.length === 1 ? "" : "s"}${showRecents ? " from recent picks" : ""}` : ""}
        </span>
      </div>
    </div>
  );
}
