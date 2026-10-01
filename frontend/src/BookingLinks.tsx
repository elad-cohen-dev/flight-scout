// Owned by the booking workstream: renders a deal's pre-filled purchase links.
import { useId, useState } from "react";
import type { BookingLink, SearchParams, StoredDeal } from "./types";
import "./booking.css";

const CABIN: Record<SearchParams["cabin_class"], string> = {
  economy: "economy",
  premium_economy: "premium economy",
  business: "business",
  first: "first",
};

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

function paxLabel(p: SearchParams["passengers"]) {
  const parts = [plural(p.adults, "adult", "adults")];
  if (p.children) parts.push(plural(p.children, "child", "children"));
  if (p.infants) parts.push(plural(p.infants, "infant", "infants"));
  return parts.join(", ");
}

function bagsLabel(c: Criteria) {
  return [c.cabin_bag && "trolley", c.checked_bag && "20kg bag"].filter(Boolean).join(" + ");
}

type Criteria = Pick<SearchParams, "passengers" | "cabin_class" | "max_stops" | "cabin_bag" | "checked_bag">;

// Legacy rows have no search_params: the backend assumed 1 adult, economy.
const DEFAULTS: Criteria = {
  passengers: { adults: 1, children: 0, infants: 0 },
  cabin_class: "economy",
  max_stops: null,
  cabin_bag: false,
  checked_bag: false,
};

/** What the link carries, phrased for the traveller ("2 adults · business · nonstop"). */
function prefilledHint(link: BookingLink, c: Criteria) {
  const has = (k: string) => link.prefilled.includes(k);
  const parts: string[] = [];
  if (has("dates")) parts.push("dates");
  if (has("passengers")) parts.push(paxLabel(c.passengers));
  if (has("cabin")) parts.push(CABIN[c.cabin_class]);
  if (has("stops") && c.max_stops === 0) parts.push("nonstop");
  if (has("bags") && bagsLabel(c)) parts.push(bagsLabel(c));
  return parts.join(" · ");
}

/** What the traveller still has to set by hand on the primary link's site. */
function todo(link: BookingLink, c: Criteria, others: BookingLink[]) {
  const has = (k: string) => link.prefilled.includes(k);
  const out: string[] = [];
  // An agent-found fare page may already be specific; we can't tell what it carries.
  const unknown = link.kind === "other" && !link.prefilled.length;
  if (!link.prefilled.length && !unknown) out.push("Search this route on the site");
  else if (!unknown) {
    if (!has("dates")) out.push("Pick your dates on the site");
    const pax = c.passengers.adults + c.passengers.children + c.passengers.infants;
    if (!has("passengers") && pax > 1) out.push(`Set ${paxLabel(c.passengers)}`);
    if (!has("cabin") && c.cabin_class !== "economy") out.push(`Choose ${CABIN[c.cabin_class]}`);
  }
  const bags = bagsLabel(c);
  if (bags && !has("bags")) {
    const withBags = others.find((l) => l.prefilled.includes("bags"));
    out.push(`Add ${bags} at checkout${withBags ? ` (${withBags.label} shows prices with bags)` : ""}`);
  }
  return out;
}

function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** An airline seller fetched by "Details & book direct" (DirectBooking): it
 *  becomes the primary button, ahead of the pre-filled search links. */
export interface DirectLink {
  label: string;
  url: string;
}

export default function BookingLinks({ deal, direct }: { deal: StoredDeal; direct?: DirectLink }) {
  const [open, setOpen] = useState(false);
  const moreId = useId();
  const found: BookingLink[] = deal.booking_links?.length
    ? deal.booking_links
    : deal.booking_url
      ? [{ label: hostOf(deal.booking_url) || "Book", url: deal.booking_url, kind: "other", prefilled: [] }]
      : [];
  // The seller's own booking page carries the exact itinerary and party.
  const links: BookingLink[] = direct
    ? [{ label: direct.label, url: direct.url, kind: "airline", prefilled: ["route", "dates", "passengers", "cabin"] }, ...found]
    : found;
  if (!links.length) return null;

  const [primary, ...others] = links;
  const c: Criteria = deal.search_params ?? DEFAULTS;
  const hint = prefilledHint(primary, c);
  const steps = todo(primary, c, others);
  const verb = direct ? "Book with" : primary.kind === "airline" ? "Book on" : primary.kind === "other" ? "Open" : "Search on";

  return (
    <div className="bl">
      <div className="bl-row">
        <a
          href={primary.url}
          target="_blank"
          rel={direct ? "noopener noreferrer" : "noreferrer"}
          className="bl-primary"
          title={hint ? `Opens with ${hint}` : undefined}
        >
          {verb} {primary.label}
          <span aria-hidden="true" className="bl-ext">↗</span>
        </a>
        {others.length > 0 && (
          <button
            type="button"
            className="bl-more"
            aria-expanded={open}
            aria-controls={moreId}
            onClick={() => setOpen((v) => !v)}
          >
            More
            <span aria-hidden="true" className={`bl-caret${open ? " bl-caret-open" : ""}`}>▾</span>
          </button>
        )}
      </div>

      {hint && (
        <p className="bl-hint">
          <span className="bl-check" aria-hidden="true">✓</span> Pre-filled: {hint}
        </p>
      )}
      {steps.map((s) => (
        <p key={s} className="bl-warn">
          <span aria-hidden="true">!</span> {s}
        </p>
      ))}

      {others.length > 0 && (
        <ul id={moreId} className="bl-list" hidden={!open}>
          {others.map((l) => {
            const h = prefilledHint(l, c);
            return (
              <li key={l.url}>
                <a href={l.url} target="_blank" rel="noreferrer" className="bl-chip" title={h ? `Pre-filled: ${h}` : "Not pre-filled"}>
                  <span className="bl-chip-label">{l.label}</span>
                  <span className="bl-chip-meta">
                    {l.prefilled.includes("bags") ? "incl. bags" : l.prefilled.includes("dates") ? "dated" : l.prefilled.length ? "route" : "home"}
                  </span>
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
