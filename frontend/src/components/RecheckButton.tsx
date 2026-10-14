// "Recheck (1 credit)" on a live Google Flights deal: the same query again,
// fresh from Google. Always spends one live search, so it asks first.
import { useState } from "react";
import { recheckDeal } from "../api";
import { recheckText } from "../lib/bags";
import { errorText } from "../lib/dealDetails";
import type { RecheckResult, StoredDeal } from "../types";
import Icon from "./Icon";
import "./bags.css";

export default function RecheckButton({ deal, onResult }: { deal: StoredDeal; onResult: (r: RecheckResult) => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RecheckResult | null>(null);
  const [error, setError] = useState("");
  if (deal.source !== "google_flights") return null;

  async function run() {
    if (!window.confirm("Recheck this fare on Google Flights now? It uses 1 live search of this month's budget.")) return;
    setBusy(true);
    setError("");
    try {
      const r = await recheckDeal(deal.id);
      setResult(r);
      onResult(r);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const tone = result ? (result.status === "cheaper" ? "good" : result.status === "same" ? "same" : "bad") : "";
  return (
    <span className="recheck">
      <button type="button" className="recheck-btn small" onClick={run} disabled={busy} title="Ask Google Flights for this exact itinerary again — uses 1 live search">
        <Icon name="refresh" size={12} /> {busy ? "Rechecking…" : "Recheck (1 credit)"}
      </button>
      {result && (
        <span className={`recheck-result recheck-${tone} small`} role="status">
          {recheckText(result)}
        </span>
      )}
      {error && (
        <span className="recheck-result recheck-bad small" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}
