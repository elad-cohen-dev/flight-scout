// "Details & book direct" for live Google Flights deals: the return flight, every
// seller with its exact price and bag fees, and "Book with <seller>" links.
// Looking at cached details is free; fetching them spends 1–2 live searches,
// so the exact cost is shown and confirmed first.
import { useState } from "react";
import { fetchDealDetails, loadDealDetails } from "../api";
import { bagSummary, canFetch, costHint, costLine, errorText, linkLabel } from "../lib/dealDetails";
import { formatPrice, formatTripDate, stopsLabel, timeAgo } from "../lib/format";
import type { DealDetails, DetailsLeg, Seller, StoredDeal } from "../types";
import Icon from "./Icon";
import "./direct-booking.css";

type Phase = "idle" | "quoting" | "fetching";

function LegLine({ label, leg }: { label: string; leg: DetailsLeg }) {
  return (
    <div className="db-leg">
      <span className="db-leg-label">{label}</span>
      <span>
        {leg.depart_date && <span className="muted">{formatTripDate(leg.depart_date)} · </span>}
        <strong>{leg.depart_time || "?"}</strong> {leg.origin} → <strong>{leg.arrive_time || "?"}</strong> {leg.destination}
        {leg.arrive_date && leg.depart_date && leg.arrive_date !== leg.depart_date && <sup className="db-plus">+1</sup>}
      </span>
      <span className="muted small">
        {[leg.flight_numbers, leg.duration, stopsLabel(leg.stops)].filter(Boolean).join(" · ")}
      </span>
    </div>
  );
}

function SellerRow({ s, currency, bagsRequested }: { s: Seller; currency: string; bagsRequested: boolean }) {
  const bags = bagSummary(s, currency);
  return (
    <li className={`db-seller${s.airline ? " db-seller-direct" : ""}`}>
      <div className="db-seller-head">
        <span className="db-seller-name">
          {s.book_with}
          {s.airline && <span className="db-tag db-tag-direct">Airline</span>}
          {s.separate_tickets && <span className="db-tag">2 tickets</span>}
        </span>
        <span className="db-seller-price">{s.price != null ? formatPrice(s.price, currency) : "—"}</span>
      </div>
      {(s.option_title || bags.length > 0) && (
        <div className="db-seller-meta small">
          {s.option_title && <span>{s.option_title}</span>}
          {bags.map((b) => (
            <span key={b}>
              <Icon name={b.startsWith("Carry") ? "bag" : "luggage"} size={12} /> {b}
            </span>
          ))}
        </div>
      )}
      {bagsRequested && (
        <div className="db-seller-meta small">
          {s.bags_verified && s.price_incl_bags != null ? (
            <span className="db-ok">
              <Icon name="check" size={12} /> {formatPrice(s.price_incl_bags, currency)} incl. your bags
            </span>
          ) : s.price_incl_bags_max != null ? (
            <span className="db-warn">Up to {formatPrice(s.price_incl_bags_max, currency)} incl. your bags — bag fee not exact</span>
          ) : (
            <span className="db-warn">Bag fee not stated — check at checkout</span>
          )}
        </div>
      )}
      <div className="db-seller-links">
        {s.links.filter((l) => l.url).map((l) => (
          <a key={l.part} href={l.url} target="_blank" rel="noopener noreferrer" className={s.airline ? "db-book db-book-direct" : "db-book"}>
            {linkLabel(s, l)} <span aria-hidden="true">↗</span>
          </a>
        ))}
        {s.phone && <span className="muted small">or call {s.phone}</span>}
      </div>
    </li>
  );
}

export default function DirectBooking({
  deal,
  details,
  onDetails,
}: {
  deal: StoredDeal;
  details: DealDetails | null;
  onDetails: (d: DealDetails) => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(true);
  const maxCost = deal.details_cost ?? 0;
  if (deal.source !== "google_flights" || maxCost <= 0) return null;

  const run = async (live: boolean) => {
    setPhase(live ? "fetching" : "quoting");
    setError("");
    try {
      onDetails(await (live ? loadDealDetails(deal.id) : fetchDealDetails(deal.id)));
      setOpen(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPhase("idle");
    }
  };

  if (!details) {
    return (
      <div className="db">
        <button type="button" className="db-start" onClick={() => run(false)} disabled={phase !== "idle"}>
          <Icon name="sparkles" size={14} />
          {phase === "quoting" ? "Checking…" : "Details & book direct"}
          <span className="db-start-cost">{costHint(maxCost)}</span>
        </button>
        {error && <p className="db-error" role="alert">{error}</p>}
      </div>
    );
  }

  if (details.status === "needs_fetch") {
    const ok = canFetch(details);
    return (
      <div className="db db-panel" aria-live="polite">
        <p className="db-quote">
          Exact sellers, bag fees and the return flight.
          {!details.message && (
            <>
              {" "}
              <strong>{costLine(details)}</strong>
              {details.budget > 0 && <span className="muted"> (monthly budget {details.budget})</span>}
            </>
          )}
        </p>
        {details.message && <p className="db-warn small">{details.message}</p>}
        <div className="db-actions">
          <button type="button" className="db-fetch" onClick={() => run(true)} disabled={!ok || phase !== "idle"}>
            {phase === "fetching" ? "Asking Google Flights…" : details.cost > 0 ? `Fetch (${details.cost})` : "Show"}
          </button>
        </div>
        {error && <p className="db-error" role="alert">{error}</p>}
      </div>
    );
  }

  if (details.status !== "ready") {
    return (
      <div className="db db-panel">
        <p className="db-warn small">{details.message || "Booking details aren't available for this deal."}</p>
        {details.return_leg && <LegLine label="Back" leg={details.return_leg} />}
      </div>
    );
  }

  const currency = details.currency || deal.currency;
  const bagsRequested = details.bags_requested.length > 0;
  return (
    <div className="db db-panel">
      <button type="button" className="db-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span>
          {details.sellers.length} seller{details.sellers.length === 1 ? "" : "s"}
          {bagsRequested && <span className="muted small">· ranked by price incl. your bags</span>}
        </span>
        <span className="muted small">
          {details.fetched_at && `checked ${timeAgo(details.fetched_at)}`}
          <Icon name="chevronDown" size={14} />
        </span>
      </button>
      {open && (
        <>
          {(details.outbound || details.return_leg) && (
            <div className="db-legs">
              {details.outbound && <LegLine label="Out" leg={details.outbound} />}
              {details.return_leg && <LegLine label="Back" leg={details.return_leg} />}
            </div>
          )}
          {details.sellers.length > 0 ? (
            <ul className="db-sellers">
              {details.sellers.map((s) => (
                <SellerRow key={s.index} s={s} currency={currency} bagsRequested={bagsRequested} />
              ))}
            </ul>
          ) : (
            <p className="muted small">Google listed no sellers for this itinerary.</p>
          )}
          <p className="muted small db-foot">
            Prices are totals for your party from Google Flights; the seller's checkout has the final word.
          </p>
        </>
      )}
    </div>
  );
}
