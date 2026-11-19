import { useEffect, useMemo, useRef, useState } from "react";
import { deleteGuide, putGuide } from "../guideApi";
import {
  dateRange,
  dayLabel,
  dayParts,
  defaultDayIndex,
  directionsUrl,
  groupByArea,
  GUIDE_VIEWS,
  GuideError,
  KIND_LABELS,
  loadTicks,
  mapsUrl,
  nextBooking,
  normalizeGuide,
  nowLocal,
  phaseLabel,
  pickGuide,
  placeKinds,
  plannedDays,
  saveTicks,
  todayISO,
  tripPhase,
  viewFromHash,
} from "../lib/guide";
import type { BookingKind, GuideBooking, GuideDay, GuidePlace, GuideStop, GuideView, PlaceKind, StopKind, TripGuide } from "../lib/guide";
import Icon from "./Icon";
import type { IconName } from "./Icon";
import Segmented from "./Segmented";
import { useToast } from "./Toasts";
import "./guide.css";

const KIND_ICONS: Record<StopKind, IconName> = {
  coffee: "coffee",
  food: "utensils",
  shop: "shop",
  sight: "camera",
  market: "shop",
  festive: "tree",
  show: "ticket",
  stay: "bed",
  transport: "train",
  flight: "plane",
  walk: "navigation",
  rest: "moon",
  note: "info",
};

const BOOKING_ICONS: Record<BookingKind, IconName> = {
  flight: "plane",
  stay: "bed",
  show: "ticket",
  tour: "sparkles",
  other: "calendar",
};

const VIEW_LABELS: Record<GuideView, string> = { plan: "Plan", places: "Places", around: "Tips", bookings: "Bookings" };

function KindDot({ kind, icon, size = 15 }: { kind: string; icon: IconName; size?: number }) {
  return (
    <span className={`guide-dot guide-kind-${kind}`}>
      <Icon name={icon} size={size} />
    </span>
  );
}

/** Text with blank-line / newline breaks kept as paragraphs. */
function Paragraphs({ text, className }: { text: string; className?: string }) {
  return (
    <>
      {text
        .split(/\n+/)
        .filter(Boolean)
        .map((p, i) => (
          <p key={i} className={className}>
            {p}
          </p>
        ))}
    </>
  );
}

function PlaceLinks({ place, primary = false }: { place: GuidePlace; primary?: boolean }) {
  return (
    <div className="guide-links">
      <a className={`btn btn-sm ${primary ? "btn-secondary" : "btn-ghost"}`} href={directionsUrl(place)} target="_blank" rel="noreferrer">
        <Icon name="navigation" size={13} /> Directions
      </a>
      <a className="btn btn-ghost btn-sm" href={mapsUrl(place)} target="_blank" rel="noreferrer">
        <Icon name="pin" size={13} /> Map
      </a>
      {place.url && (
        <a className="btn btn-ghost btn-sm" href={place.url} target="_blank" rel="noreferrer">
          Site <Icon name="external" size={12} />
        </a>
      )}
    </div>
  );
}

// --- plan ---------------------------------------------------------------------------------

function Todos({ guide, open }: { guide: TripGuide; open: boolean }) {
  const [ticks, setTicks] = useState<string[]>(() => loadTicks(guide.slug));
  if (!guide.todos.length) return null;
  const done = guide.todos.filter((t) => ticks.includes(t.id)).length;
  const toggle = (id: string) => {
    const next = ticks.includes(id) ? ticks.filter((t) => t !== id) : [...ticks, id];
    setTicks(next);
    saveTicks(guide.slug, next);
  };
  return (
    <details className="guide-todos card" open={open && done < guide.todos.length}>
      <summary>
        <Icon name="check" size={16} />
        <strong>Before you go</strong>
        <span className={`badge ${done === guide.todos.length ? "badge-good" : ""}`}>
          {done} of {guide.todos.length} done
        </span>
        <Icon name="chevronDown" size={16} className="guide-todos-chev" />
      </summary>
      <ul>
        {guide.todos.map((t) => {
          const checked = ticks.includes(t.id);
          return (
            <li key={t.id} className={checked ? "done" : ""}>
              <label>
                <input type="checkbox" checked={checked} onChange={() => toggle(t.id)} />
                <span>
                  <span className="guide-todo-text">{t.text}</span>
                  {t.due && <span className="badge guide-todo-due">{t.due}</span>}
                  {t.note && <span className="muted small guide-todo-note">{t.note}</span>}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      <p className="muted small">Ticks are saved on this device only.</p>
    </details>
  );
}

function NextUp({ booking, place }: { booking: GuideBooking; place?: GuidePlace }) {
  return (
    <div className="guide-next card">
      <KindDot kind={booking.kind} icon={BOOKING_ICONS[booking.kind]} size={17} />
      <div className="guide-next-body">
        <span className="guide-eyebrow">Next up</span>
        <strong>{booking.title}</strong>
        <span className="small">{booking.when}</span>
      </div>
      {place && (
        <a className="btn btn-secondary btn-sm" href={directionsUrl(place)} target="_blank" rel="noreferrer">
          <Icon name="navigation" size={13} /> Directions
        </a>
      )}
    </div>
  );
}

function Stop({ stop, place }: { stop: GuideStop; place?: GuidePlace }) {
  return (
    <li className={`guide-stop${stop.fixed ? " fixed" : ""}`}>
      <span className={`guide-stop-time${/^\d/.test(stop.time) ? "" : " loose"}`}>{stop.time}</span>
      <span className="guide-stop-rail">
        <KindDot kind={stop.kind} icon={KIND_ICONS[stop.kind]} />
      </span>
      <div className="guide-stop-body">
        {stop.travel && (
          <p className="guide-travel small">
            <Icon name="arrowDown" size={12} /> {stop.travel}
          </p>
        )}
        <div className="guide-stop-head">
          <strong>{stop.title}</strong>
          {stop.fixed && <span className="badge badge-accent">Booked</span>}
        </div>
        {stop.note && <Paragraphs text={stop.note} className="guide-stop-note" />}
        {place && (
          <>
            {(place.hours || place.address) && (
              <p className="muted small guide-stop-meta">{[place.hours, place.address].filter(Boolean).join(" · ")}</p>
            )}
            <PlaceLinks place={place} />
          </>
        )}
      </div>
    </li>
  );
}

function DayPicker({ days, index, today, onPick }: { days: GuideDay[]; index: number; today: string; onPick: (i: number) => void }) {
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // keep the chosen day in view on phones, where the row scrolls sideways
    row.current?.querySelector<HTMLElement>(".active")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [index]);
  return (
    <div className="guide-days" role="group" aria-label="Day" ref={row}>
      {days.map((d, i) => {
        const p = dayParts(d.date);
        return (
          <button
            key={d.date}
            type="button"
            aria-pressed={i === index}
            aria-label={`${dayLabel(d.date)}${d.date === today ? " (today)" : ""}`}
            className={`guide-day${i === index ? " active" : ""}${d.date === today ? " today" : ""}`}
            onClick={() => onPick(i)}
          >
            <span className="guide-day-wd">{d.date === today ? "Today" : p.weekday}</span>
            <span className="guide-day-num">{p.day}</span>
          </button>
        );
      })}
    </div>
  );
}

function PlanView({ guide, today, places }: { guide: TripGuide; today: string; places: Map<string, GuidePlace> }) {
  const [index, setIndex] = useState(() => defaultDayIndex(guide, today));
  const phase = tripPhase(guide, today);
  const next = nextBooking(guide, nowLocal());
  const day = guide.days[Math.min(index, guide.days.length - 1)];
  return (
    <>
      {next && <NextUp booking={next} place={places.get(next.place)} />}
      <Todos guide={guide} open={phase.phase === "before"} />
      {day && (
        <>
          <DayPicker days={guide.days} index={index} today={today} onPick={setIndex} />
          <section className="guide-day-plan" aria-live="polite">
            <div className="guide-day-head">
              <h3>
                {dayLabel(day.date)}
                {day.title && <span className="guide-day-title"> · {day.title}</span>}
              </h3>
              {day.summary && <p className="muted">{day.summary}</p>}
            </div>
            <ol className="guide-timeline">
              {day.stops.map((s, i) => (
                <Stop key={i} stop={s} place={places.get(s.place)} />
              ))}
            </ol>
            {day.extras.length > 0 && (
              <div className="guide-extras card">
                <span className="guide-eyebrow">If there's time</span>
                <ul>
                  {day.extras.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

// --- places -------------------------------------------------------------------------------

function PlaceCard({ place, planned }: { place: GuidePlace; planned?: string[] }) {
  return (
    <article className="guide-place card">
      <div className="guide-place-head">
        <KindDot kind={place.kind} icon={KIND_ICONS[place.kind]} />
        <div>
          <h4>
            {place.name}
            {place.pick && (
              <span className="guide-pick" title="Top pick">
                <Icon name="star" size={13} />
                <span className="sr-only">Top pick</span>
              </span>
            )}
          </h4>
          {place.address && <p className="muted small">{place.address}</p>}
        </div>
      </div>
      {(place.tags.length > 0 || planned) && (
        <div className="guide-tags">
          {planned && <span className="badge badge-accent">In plan · {planned.join(", ")}</span>}
          {place.tags.map((t) => (
            <span key={t} className="badge">
              {t}
            </span>
          ))}
        </div>
      )}
      {place.note && <Paragraphs text={place.note} className="guide-place-note" />}
      {place.hours && (
        <p className="small guide-hours">
          <Icon name="clock" size={13} /> {place.hours}
        </p>
      )}
      <PlaceLinks place={place} primary />
    </article>
  );
}

function PlacesView({ guide }: { guide: TripGuide }) {
  const kinds = useMemo(() => placeKinds(guide.places), [guide.places]);
  const [kind, setKind] = useState<PlaceKind | "all">("all");
  const planned = useMemo(() => plannedDays(guide), [guide]);
  const shown = kind === "all" ? guide.places : guide.places.filter((p) => p.kind === kind);
  if (!guide.places.length) return <p className="muted">This guide has no places.</p>;
  return (
    <>
      <div className="chip-row guide-filters" role="group" aria-label="Filter places">
        {(["all", ...kinds] as const).map((k) => (
          <button key={k} type="button" className={`chip${kind === k ? " chip-active" : ""}`} aria-pressed={kind === k} onClick={() => setKind(k)}>
            {k === "all" ? "All" : KIND_LABELS[k]}
            <span className="muted">{k === "all" ? guide.places.length : guide.places.filter((p) => p.kind === k).length}</span>
          </button>
        ))}
      </div>
      {groupByArea(shown).map((g) => (
        <section key={g.area}>
          <h3 className="section-title">
            {g.area} <span className="section-count">{g.places.length}</span>
          </h3>
          <div className="guide-place-grid">
            {g.places.map((p) => (
              <PlaceCard key={p.id} place={p} planned={planned.get(p.id)} />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

// --- tips, bookings ------------------------------------------------------------------------

function TipsView({ guide }: { guide: TripGuide }) {
  if (!guide.tips.length) return <p className="muted">This guide has no tips.</p>;
  return (
    <div className="guide-tips">
      {guide.tips.map((g) => (
        <section key={g.title} className="guide-tip-group card">
          <h3>
            <KindDot kind={g.kind} icon={KIND_ICONS[g.kind]} /> {g.title}
          </h3>
          <dl>
            {g.tips.map((t, i) => (
              <div key={i}>
                {t.title && <dt>{t.title}</dt>}
                <dd>
                  <Paragraphs text={t.body} />
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}

function BookingsView({ guide, places }: { guide: TripGuide; places: Map<string, GuidePlace> }) {
  const toast = useToast();
  if (!guide.bookings.length) return <p className="muted">This guide has no bookings.</p>;
  const copy = async (ref: string) => {
    try {
      await navigator.clipboard.writeText(ref);
      toast("success", "Copied.");
    } catch {
      toast("error", "Couldn't copy — press and hold the number instead.");
    }
  };
  return (
    <div className="guide-bookings">
      {guide.bookings.map((b, i) => {
        const place = places.get(b.place);
        return (
          <article key={i} className="guide-booking card">
            <div className="guide-place-head">
              <KindDot kind={b.kind} icon={BOOKING_ICONS[b.kind]} size={17} />
              <div>
                <h4>{b.title}</h4>
                {b.when && <p className="small guide-booking-when">{b.when}</p>}
              </div>
            </div>
            {b.lines.length > 0 && (
              <ul className="guide-booking-lines">
                {b.lines.map((l, j) => (
                  <li key={j}>{l}</li>
                ))}
              </ul>
            )}
            {b.ref && (
              <div className="guide-ref">
                <span className="field-label">Confirmation</span>
                <code>{b.ref}</code>
                <button type="button" className="icon-btn" onClick={() => copy(b.ref)} aria-label={`Copy confirmation ${b.ref}`} title="Copy">
                  <Icon name="copy" size={15} />
                </button>
              </div>
            )}
            {place && <PlaceLinks place={place} primary />}
          </article>
        );
      })}
    </div>
  );
}

// --- the tab -------------------------------------------------------------------------------

export default function GuideTab({
  guides,
  loaded,
  onReload,
  onError,
}: {
  guides: TripGuide[];
  /** False until the first answer from the server (cached guides may already show). */
  loaded: boolean;
  onReload: () => Promise<void>;
  onError: (e: unknown) => void;
}) {
  const toast = useToast();
  const today = todayISO();
  const fileInput = useRef<HTMLInputElement>(null);
  const [slug, setSlug] = useState<string | null>(null);
  const [view, setView] = useState<GuideView>(() => viewFromHash(window.location.hash));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onHash = () => setView(viewFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const guide = guides.find((g) => g.slug === slug) ?? pickGuide(guides, today);
  const places = useMemo(() => new Map((guide?.places ?? []).map((p) => [p.id, p])), [guide]);

  function show(next: GuideView) {
    setView(next);
    // the view is in the URL (refresh, share between phones) without a history entry per tap
    window.history.replaceState(null, "", next === "plan" ? "#guide" : `#guide/${next}`);
    window.scrollTo({ top: 0 });
  }

  async function importFile(file: File) {
    setBusy(true);
    try {
      let raw: unknown;
      try {
        raw = JSON.parse(await file.text());
      } catch {
        throw new GuideError("That file isn't valid JSON.");
      }
      const parsed = normalizeGuide(raw);
      await putGuide(parsed);
      await onReload();
      setSlug(parsed.slug);
      toast("success", `Imported “${parsed.title}” — it's stored behind your app password.`);
    } catch (e) {
      if (e instanceof GuideError) toast("error", e.message);
      else onError(e);
    } finally {
      setBusy(false);
    }
  }

  async function remove(g: TripGuide) {
    if (!window.confirm(`Remove the guide “${g.title}” from the server and this device? Keep the file if you want it back.`)) return;
    setBusy(true);
    try {
      await deleteGuide(g.slug);
      await onReload();
      setSlug(null);
      toast("info", `Removed “${g.title}”.`);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  function download(g: TripGuide) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(g, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${g.slug}.guide.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const picker = (
    <input
      ref={fileInput}
      type="file"
      accept="application/json,.json"
      className="sr-only"
      tabIndex={-1}
      aria-hidden="true"
      onChange={(e) => {
        const file = e.target.files?.[0];
        e.target.value = ""; // picking the same file again still fires
        if (file) importFile(file);
      }}
    />
  );

  if (!guide) {
    return (
      <div className="tab-guide">
        {picker}
        {!loaded ? (
          <>
            <div className="skeleton skeleton-bar" />
            <div className="skeleton skeleton-card tall" />
          </>
        ) : (
          <div className="empty card">
            <Icon name="pin" size={32} />
            <strong>No trip guide yet</strong>
            <span className="muted">
              A guide is one trip's day-by-day plan, places, bookings and practical tips. Import its file here: it is stored behind your app
              password — never in the app's code — and saved on each device for offline use.
            </span>
            <button className="btn btn-primary" disabled={busy} onClick={() => fileInput.current?.click()}>
              <Icon name="upload" size={16} /> Import a guide file
            </button>
          </div>
        )}
      </div>
    );
  }

  const phase = tripPhase(guide, today);
  return (
    <div className="tab-guide">
      {picker}
      <div className="page-head guide-head">
        <div>
          <h2>{guide.title}</h2>
          <p className="muted small">{[dateRange(guide.start, guide.end), guide.subtitle].filter(Boolean).join(" · ")}</p>
        </div>
        <span className={`badge guide-phase guide-phase-${phase.phase}`}>{phaseLabel(phase)}</span>
      </div>

      {guides.length > 1 && (
        <div className="chip-row" role="group" aria-label="Trip">
          {guides.map((g) => (
            <button key={g.slug} type="button" className={`chip${g.slug === guide.slug ? " chip-active" : ""}`} aria-pressed={g.slug === guide.slug} onClick={() => setSlug(g.slug)}>
              {g.title}
            </button>
          ))}
        </div>
      )}

      <div className="guide-views">
        <Segmented label="Guide section" value={view} onChange={show} options={GUIDE_VIEWS.map((v) => ({ value: v, label: VIEW_LABELS[v] }))} />
      </div>

      {view === "plan" && <PlanView key={guide.slug} guide={guide} today={today} places={places} />}
      {view === "places" && <PlacesView key={guide.slug} guide={guide} />}
      {view === "around" && <TipsView guide={guide} />}
      {view === "bookings" && <BookingsView guide={guide} places={places} />}

      <footer className="guide-foot">
        <p className="muted small">
          <Icon name="lock" size={12} /> Private: stored behind your app password and saved on this device for offline use.
          {guide.checked && ` ${guide.checked} — hours and prices change, so check anything that matters on the day.`}
        </p>
        <div className="guide-foot-actions">
          <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => fileInput.current?.click()}>
            <Icon name="upload" size={13} /> Import / replace
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => download(guide)}>
            <Icon name="download" size={13} /> Download
          </button>
          <button type="button" className="btn btn-ghost btn-sm btn-danger" disabled={busy} onClick={() => remove(guide)}>
            <Icon name="trash" size={13} /> Remove
          </button>
        </div>
      </footer>
    </div>
  );
}
