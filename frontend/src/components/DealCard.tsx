import BookingLinks from "../BookingLinks";
import { formatPrice, formatTripDate, nightsBetween, shortDate, stopsLabel, timeAgo } from "../lib/format";
import { dealCriteria, totalPassengers } from "../lib/search";
import type { PriceConfidence, StoredDeal } from "../types";
import Icon from "./Icon";

const CONFIDENCE: Record<PriceConfidence, { label: string; tip: string }> = {
  verified: { label: "Verified", tip: "Exact fare seen for these dates on a booking or fare site." },
  estimated: { label: "Estimate", tip: "Computed or approximate (e.g. fare + bag fees) — check the final price before booking." },
  from_price: { label: "From price", tip: "A \"from\" teaser price; the fare for your exact dates may be higher." },
};

function ConfidenceBadge({ value }: { value: PriceConfidence }) {
  const c = CONFIDENCE[value] ?? CONFIDENCE.estimated;
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
}: {
  label: string;
  date: string;
  dep: string;
  arr: string;
  from: string;
  to: string;
  duration?: string;
  stops?: number;
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
          {arr ? <span className="leg-time">{arr}</span> : <span className="leg-time leg-time-na">{to}</span>}
          {arr && <span className="leg-iata">{to}</span>}
        </div>
      </div>
    </div>
  );
}

export default function DealCard({ deal, highlight }: { deal: StoredDeal; highlight?: boolean }) {
  const criteria = dealCriteria(deal);
  const pax = criteria.passengers ? totalPassengers(criteria.passengers) : 1;
  const from = deal.origin_airport || deal.origin;
  const to = deal.destination_airport || deal.destination;
  const nights = nightsBetween(deal.depart_date, deal.return_date);
  const hasTimes = !!(deal.depart_time || deal.arrive_time);
  const showSource = deal.source_url && deal.source_url !== deal.booking_url;

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
          <div className="deal-price">{formatPrice(deal.price, deal.currency)}</div>
          {pax > 1 && deal.price_per_person != null ? (
            <div className="muted small" title={`Total for ${pax} travellers`}>
              {formatPrice(deal.price_per_person, deal.currency)} per person
            </div>
          ) : (
            <div className="muted small">{deal.return_date ? "round trip" : "one way"}{pax > 1 ? ` · ${pax} travellers` : ""}</div>
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
          />
          {deal.return_date && (
            <Leg label="Back" date={deal.return_date} dep={deal.return_depart_time} arr="" from={to} to={from} />
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
        <ConfidenceBadge value={deal.price_confidence} />
        {!hasTimes && !deal.origin_airport && (
          <span className={`badge${deal.stops === 0 ? " badge-good" : ""}`}>{stopsLabel(deal.stops)}</span>
        )}
        {nights != null && <span className="badge">{nights} night{nights > 1 ? "s" : ""}</span>}
        {criteria.cabin_bag && (
          <span className="badge" title="Price includes a 10kg cabin trolley">
            <Icon name="bag" size={12} /> Trolley incl.
          </span>
        )}
        {criteria.checked_bag && (
          <span className="badge" title="Price includes a 20kg+ checked bag">
            <Icon name="luggage" size={12} /> 20kg bag incl.
          </span>
        )}
        {deal.times_seen > 1 && deal.first_found_at && (
          <span className="badge badge-seen" title="This exact fare keeps appearing across runs — likely a stable price">
            Seen {deal.times_seen}× since {shortDate(deal.first_found_at)}
          </span>
        )}
      </div>

      <div className="deal-foot">
        <BookingLinks deal={deal} />
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
      </div>
    </article>
  );
}
