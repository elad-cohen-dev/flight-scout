import { useEffect, useState } from "react";
import { fetchDeals, runSearch } from "./api";
import type { StoredDeal } from "./types";

function formatPrice(deal: StoredDeal): string {
  const symbols: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", ILS: "₪" };
  const symbol = symbols[deal.currency];
  const amount = deal.price.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return symbol ? `${symbol}${amount}` : `${amount} ${deal.currency}`;
}

function DealCard({ deal }: { deal: StoredDeal }) {
  return (
    <div className="deal-card">
      <div className="deal-price">{formatPrice(deal)}</div>
      <div className="deal-main">
        <div className="deal-route">
          {deal.origin} → {deal.destination}
        </div>
        <div className="deal-meta">
          <span>{deal.airline}</span>
          {deal.duration && <span>{deal.duration}</span>}
          <span>{deal.stops === 0 ? "Direct" : `${deal.stops} stop${deal.stops > 1 ? "s" : ""}`}</span>
        </div>
        <div className="deal-dates">
          {deal.depart_date}
          {deal.return_date && ` — ${deal.return_date}`}
        </div>
        {deal.notes && <div className="deal-notes">{deal.notes}</div>}
      </div>
      <div className="deal-actions">
        {deal.booking_url && (
          <a href={deal.booking_url} target="_blank" rel="noreferrer" className="btn-book">
            Book
          </a>
        )}
        {deal.source_url && (
          <a href={deal.source_url} target="_blank" rel="noreferrer" className="btn-source">
            Source
          </a>
        )}
        <span className="deal-found">found {new Date(deal.found_at).toLocaleDateString()}</span>
      </div>
    </div>
  );
}

export default function App() {
  const [deals, setDeals] = useState<StoredDeal[]>([]);
  const [origin, setOrigin] = useState("TLV");
  const [destination, setDestination] = useState("");
  const [datesHint, setDatesHint] = useState("anytime in the next 3 months");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDeals().then(setDeals).catch((e) => setError(String(e)));
  }, []);

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!origin || !destination) return;
    setSearching(true);
    setError(null);
    try {
      const result = await runSearch({ origin, destination, dates_hint: datesHint });
      setDeals((prev) => [...result.deals, ...prev]);
    } catch (e) {
      setError(String(e));
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="container">
      <header>
        <h1>✈️ Flight Scout</h1>
        <p className="subtitle">An agent that hunts the web for cheap flights</p>
      </header>

      <form className="search-form" onSubmit={onSearch}>
        <input
          value={origin}
          onChange={(e) => setOrigin(e.target.value)}
          placeholder="From (e.g. TLV)"
          required
        />
        <input
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          placeholder="To (e.g. London)"
          required
        />
        <input
          className="dates-input"
          value={datesHint}
          onChange={(e) => setDatesHint(e.target.value)}
          placeholder="When? (free text)"
        />
        <button type="submit" disabled={searching}>
          {searching ? "Searching…" : "Find deals"}
        </button>
      </form>

      {searching && (
        <div className="status">
          The agent is searching the live web — this usually takes a minute or two ☕
        </div>
      )}
      {error && <div className="status error">{error}</div>}

      <main>
        {deals.length === 0 && !searching ? (
          <div className="empty">No deals yet. Run a search above to get started.</div>
        ) : (
          deals.map((deal) => <DealCard key={deal.id} deal={deal} />)
        )}
      </main>
    </div>
  );
}
