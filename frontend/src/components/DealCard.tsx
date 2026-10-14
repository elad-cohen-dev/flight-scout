import { useEffect, useState } from "react";
import BookingLinks from "../BookingLinks";
import { approx, asQuoted, bagCountWords, bagsAdded, checkoutPreview, freshness } from "../lib/bags";
import { bagBadges, bagCheckoutNote, bagTotal, bagWords, directSeller } from "../lib/dealDetails";
import { comparable, formatPrice, formatTripDate, nightsBetween, shortDate, stopsLabel, timeAgo } from "../lib/format";
import { noteBookClick, takeFor } from "../lib/priceCheck";
import type { PendingCheck } from "../lib/priceCheck";
import { isUnconfirmed } from "../lib/ranking";
import { dealCriteria, totalPassengers } from "../lib/search";
import type { DealDetails, PriceConfidence, StoredDeal } from "../types";
import BagBreakdown from "./BagBreakdown";
import DirectBooking from "./DirectBooking";
import Icon from "./Icon";
import PriceCheckPrompt from "./PriceCheckPrompt";
import RecheckButton from "./RecheckButton";
import SourceBadge from "./SourceBadge";
import "./bags.css";

const CONFIDENCE: Record<PriceConfidence, { label: string; tip: string }> = {
  verified: { label: "Verified", tip: "Exact fare seen for these dates on a booking or fare site." },
  estimated: { label: "Estimate", tip: "Computed or approximate (e.g. fare + bag fees) — check the final price before booking." },
  from_price: { label: "From price", tip: "A \"from\" teaser price; the fare for your exact dates may be higher." },
};

// A live Google Flights fare is exact; it's only marked "estimated" because the
// requested checked bag isn't in Google's price. Calling that an "Estimate" next
// to "Google Flights · live" read as a contradiction. Since 3.0 the headline adds
// the bag fees (app/bags.py), so the badge says what's exact and what's estimated.
const FARE_EXCL_BAG = { label: "Fare excl. bag", tip: "Exact live fare — the checked bag you asked for isn't included. Open details for the bag fee." };
const FARE_PLUS_BAGS = {
  label: "Live fare + bag fees",
  tip: "Exact live fare plus the bags you asked for at the airline's typical fees — tap the fare under the price for the breakdown.",
};

function ConfidenceBadge({
  value,
  liveFareWithoutBag = false,
  withBagFees = false,
}: {
  value: PriceConfidence;
  liveFareWithoutBag?: boolean;
  withBagFees?: boolean; // the headline is the live fare + estimated bag fees
}) {
  const c = withBagFees ? FARE_PLUS_BAGS : liveFareWithoutBag ? FARE_EXCL_BAG : CONFIDENCE[value] ?? CONFIDENCE.estimated;
  return (
    <span className={`badge badge-conf-${value}`} title={c.tip} tabIndex={0} aria-label={`${c.label}: ${c.tip}`}>
      {value === "verified" && <Icon name="check" size={12} />}
      {c.label}
    </span>
  );
}

/** Two-letter mark tinted per airline, standing in for a logo. */
function AirlineMark({ code, name }: { code: string; name: string }) {
  const text = (code || name.replace(/[^A-Za-z]/g, "").slice(0, 2) || "✈").toUpperCase().slice(0, 3);
  let hash = 0;
  for (const ch of name || code) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  return (
    <span className="airline-mark" style={{ ["--hue" as string]: hash }} aria-hidden="true">
      {text}
    </span>
  );
}

function Leg({
  label,
  date,
  dep,
  arr,
  from,
  to,
  duration,
  stops,
  nextDay,
}: {
  label: string;
  date: string;
  dep: string;
  arr: string;
  from: string;
  to: string;
  duration?: string;
  stops?: number;
  nextDay?: boolean; // arrives the day after it departs
}) {
  return (
    <div className={`leg${label === "Back" ? " leg-return" : ""}`}>
      <div className="leg-label">
        <span className="muted">{label}</span>
        <span className="leg-date">{formatTripDate(date)}</span>
      </div>
      <div className="leg-times">
        <div className="leg-end">
          {dep ? <span className="leg-time">{dep}</span> : <span className="leg-time leg-time-na">{from}</span>}
          {dep && <span className="leg-iata">{from}</span>}
        </div>
        <div className="leg-line" aria-hidden="true">
          <span className="leg-line-meta">{duration}</span>
          <span className="leg-track">
            <Icon name="plane" size={14} />
          </span>
          {stops != null && <span className={`leg-line-meta${stops === 0 ? " text-good" : ""}`}>{stopsLabel(stops)}</span>}
        </div>
        <div className="leg-end leg-end-arr">
          {arr ? (
            <span className="leg-time">
              {arr}
              {nextDay && <sup className="leg-plus" title="Arrives the next day">+1</sup>}
            </span>
          ) : (
            <span className="leg-time leg-time-na">{to}</span>
          )}
          {arr && <span className="leg-iata">{to}</span>}
        </div>
      </div>
    </div>
  );
}

export default function DealCard({
  deal: listed,
  highlight,
  onCheckLive,
}: {
  deal: StoredDeal;
  highlight?: boolean;
  onCheckLive?: (deal: StoredDeal) => void;
}) {
  // A Recheck stores a new sighting; the card then shows it (the lists catch up on their next load).
  const [rechecked, setRechecked] = useState<StoredDeal | null>(null);
  const deal = rechecked ?? listed;
  const [ask, setAsk] = useState<PendingCheck | null>(null);
  const estimate = isUnconfirmed(deal);
  const criteria = dealCriteria(deal);
  const pax = criteria.passengers ? totalPassengers(criteria.passengers) : 1;
  const from = deal.origin_airport || deal.origin;
  const to = deal.destination_airport || deal.destination;
  const nights = nightsBetween(deal.depart_date, deal.return_date);
  const hasTimes = !!(deal.depart_time || deal.arrive_time);
  const showSource = deal.source_url && deal.source_url !== deal.booking_url;
  const [details, setDetails] = useState<DealDetails | null>(null);
  const ready = details?.status === "ready" ? details : null;
  const back = ready?.return_leg ?? (details?.status === "expired" ? details.return_leg : null);
  const direct = directSeller(ready);
  // When bags were asked for, the headline is the price with them (what lists
  // sort by: compare_total) and the bare fare sits under it with the
  // breakdown; an exact bag-inclusive total from the selected seller is shown under it.
  const withBags = bagTotal(ready);
  const pricing = deal.bags;
  const added = bagsAdded(pricing) ? pricing : null;
  const bagBadgeList = bagBadges(deal.source, criteria, ready, pricing);
  const bagsNote = direct && ready ? bagCheckoutNote(direct.seller, ready.bags_requested, ready.currency || deal.currency) : null;
  const preview = withBags == null ? checkoutPreview(deal) : null;
  const shownTotal = withBags ?? added?.total ?? deal.price;
  const out = ready?.outbound;
  const nextDay = (leg?: { depart_date: string; arrive_date: string } | null) =>
    !!leg?.arrive_date && !!leg.depart_date && leg.arrive_date !== leg.depart_date;
  const fresh = freshness(deal);

  // After a Book click: once the user is back on this tab, ask whether the seller's price matched.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const p = takeFor(deal.id);
      if (p) setAsk(p);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [deal.id]);
  const onBook = (seller: string, total?: number | null) =>
    noteBookClick({ dealId: deal.id, seller, shownTotal: total ?? shownTotal, currency: deal.currency });

  return (
    <article className={`deal card${highlight ? " deal-best" : ""}`}>
      {highlight && <span className="deal-ribbon">Cheapest</span>}
      <div className="deal-top">
        <div className="deal-carrier">
          <AirlineMark code={deal.airline_code} name={deal.airline} />
          <div className="deal-carrier-text">
            <div className="deal-airline">{deal.airline || "Airline n/a"}</div>
            <div className="muted small">
              {deal.flight_numbers || (deal.origin_airport ? `${from} → ${to}` : `${deal.origin} → ${deal.destination}`)}
            </div>
          </div>
        </div>
        <div className="deal-price-block">
          {added ? (
            <>
              <div className="deal-price" title={`Fare + ${bagCountWords(added)} — tap the fare below for the breakdown`}>
                <span className="deal-price-approx">{approx(added)}</span>
                {formatPrice(added.total, added.currency)}
              </div>
              <div className="deal-price-with small">with {bagCountWords(added)}</div>
              <BagBreakdown pricing={added} />
            </>
          ) : (
            <div className="deal-price">{formatPrice(deal.price, deal.currency)}</div>
          )}
          {!added && asQuoted(pricing) && (
            <div className="muted small" title="The web search quoted this price with your bags; it wasn't priced from a fee table">
              as quoted, incl. bags
            </div>
          )}
          {comparable(deal).currency !== deal.currency && (
            <div className="deal-price-converted small" title="Converted at today's rate, for comparing deals quoted in different currencies">
              ≈ {formatPrice(comparable(deal).price, comparable(deal).currency)}
            </div>
          )}
          {pax > 1 && deal.price_per_person != null && !added ? (
            <div className="muted small" title={`Total for ${pax} travellers`}>
              {formatPrice(deal.price_per_person, deal.currency)} per person
            </div>
          ) : (
            <div className="muted small">{deal.return_date ? "round trip" : "one way"}{pax > 1 ? ` · ${pax} travellers` : ""}</div>
          )}
          {withBags != null && ready && (
            <div className="deal-price-bags small" title={`Exact total incl. your ${bagWords(ready.bags_requested)}, from the seller's bag fees`}>
              {formatPrice(withBags, ready.currency || deal.currency)} with your {bagWords(ready.bags_requested)}
            </div>
          )}
        </div>
      </div>

      {hasTimes || deal.origin_airport ? (
        <div className="deal-legs">
          <Leg
            label="Out"
            date={deal.depart_date}
            dep={deal.depart_time}
            arr={deal.arrive_time}
            from={from}
            to={to}
            duration={deal.duration}
            stops={deal.stops}
            nextDay={nextDay(out)}
          />
          {deal.return_date && (
            <Leg
              label="Back"
              date={deal.return_date}
              dep={back?.depart_time || deal.return_depart_time}
              arr={back?.arrive_time ?? ""}
              from={to}
              to={from}
              duration={back?.duration}
              stops={back?.stops}
              nextDay={nextDay(back)}
            />
          )}
        </div>
      ) : (
        <div className="deal-dates">
          <Icon name="calendar" size={15} />
          <span>
            {formatTripDate(deal.depart_date)}
            {deal.return_date && <> – {formatTripDate(deal.return_date)}</>}
          </span>
          {deal.duration && <span className="muted">· {deal.duration}</span>}
        </div>
      )}

      <div className="deal-tags">
        {withBags == null && (
          <ConfidenceBadge
            value={deal.price_confidence}
            withBagFees={!!added && deal.source === "google_flights"}
            liveFareWithoutBag={
              !pricing && // pre-3.0 server: the headline is the bare fare
              deal.source === "google_flights" &&
              deal.price_confidence === "estimated" &&
              !!(deal.search_params?.checked_bag)
            }
          />
        )}
        {withBags != null && (
          <span className="badge badge-conf-verified" title="The seller's exact fare plus the exact fees for the bags you asked for">
            <Icon name="check" size={12} />
            Verified incl. bags
          </span>
        )}
        <SourceBadge deal={deal} />
        {!hasTimes && !deal.origin_airport && (
          <span className={`badge${deal.stops === 0 ? " badge-good" : ""}`}>{stopsLabel(deal.stops)}</span>
        )}
        {nights != null && <span className="badge">{nights} night{nights > 1 ? "s" : ""}</span>}
        {bagBadgeList.map((b) => (
          <span key={b.bag} className={`badge${b.included ? "" : " badge-bag-excl"}`} title={b.tip}>
            <Icon name={b.bag === "carry_on" ? "bag" : "luggage"} size={12} /> {b.label}
          </span>
        ))}
        {deal.times_seen > 1 && deal.first_found_at && (
          <span className="badge badge-seen" title="This exact fare keeps appearing across runs — likely a stable price">
            Seen {deal.times_seen}× since {shortDate(deal.first_found_at)}
          </span>
        )}
      </div>

      <div className="deal-foot">
        {estimate && onCheckLive && (
          <button
            type="button"
            className="btn btn-primary btn-sm deal-check-live"
            onClick={() => onCheckLive(deal)}
            title="Search Google Flights for exactly these dates — uses 1 live search"
          >
            <Icon name="refresh" size={14} /> Check this date live (1 search)
          </button>
        )}
        {(fresh || deal.source === "google_flights") && (
          <div className="deal-fresh muted small">
            {fresh && <span title={deal.seen_at ? "When the source saw this price" : "When this search ran"}>{fresh}</span>}
            <RecheckButton
              deal={deal}
              onResult={(r) => {
                if (r.deal) {
                  setRechecked(r.deal);
                  setDetails(null); // the new sighting has its own booking references
                }
              }}
            />
          </div>
        )}
        {preview && (
          <p className="bag-preview small">
            <Icon name="luggage" size={13} /> {preview}
          </p>
        )}
        <BookingLinks
          deal={deal}
          direct={direct ? { label: direct.seller.book_with, url: direct.link.url, bagsNote } : undefined}
          secondary={estimate}
          onBook={(label) => onBook(label, label === direct?.seller.book_with ? withBags : null)}
        />
        {ask && <PriceCheckPrompt check={ask} onClose={() => setAsk(null)} />}
        {(deal.notes || showSource) && (
          <details className="deal-details">
            <summary>
              Details <Icon name="chevronDown" size={14} />
            </summary>
            <div className="deal-details-body">
              {deal.notes && <p>{deal.notes}</p>}
              <p className="muted small">
                Found {timeAgo(deal.found_at)}
                {showSource && (
                  <>
                    {" · "}
                    <a href={deal.source_url} target="_blank" rel="noreferrer">
                      Source page <Icon name="external" size={12} />
                    </a>
                  </>
                )}
              </p>
            </div>
          </details>
        )}
        <DirectBooking deal={deal} details={details} onDetails={setDetails} onBook={onBook} />
      </div>
    </article>
  );
}
