import { useMemo, useState } from "react";
import { rankDeals, type Sort } from "../lib/ranking";
import type { StoredDeal } from "../types";
import DealCard from "./DealCard";
import Segmented from "./Segmented";

/** Deals of one run, sortable. Confirmed fares come first and the cheapest of
 *  them gets the "Cheapest" ribbon; unconfirmed estimates (Google Explore,
 *  Aviasales cache) follow in their own group, each offering a live check. */
export default function DealList({
  deals,
  sortable = true,
  onCheckLive,
}: {
  deals: StoredDeal[];
  sortable?: boolean;
  onCheckLive?: (deal: StoredDeal) => void; // re-run that exact date pair live (1 search)
}) {
  const [sort, setSort] = useState<Sort>("price");
  const { confirmed, estimates, cheapestId } = useMemo(() => rankDeals(deals, sort), [deals, sort]);
  const card = (d: StoredDeal) => <DealCard key={d.id} deal={d} highlight={d.id === cheapestId} onCheckLive={onCheckLive} />;
  return (
    <div className="deal-list">
      {sortable && deals.length > 1 && (
        <div className="deal-list-bar">
          <span className="muted small">Sort by</span>
          <Segmented
            label="Sort deals"
            size="sm"
            value={sort}
            onChange={setSort}
            options={[
              { value: "price", label: "Cheapest" },
              { value: "duration", label: "Fastest" },
              { value: "depart", label: "Earliest" },
            ]}
          />
        </div>
      )}
      {confirmed.map(card)}
      {estimates.length > 0 && confirmed.length > 0 && (
        <div className="deal-group-head">
          <h3>Other dates (estimated, not confirmed)</h3>
          <p className="muted small">Prices Google Explore or the fare cache showed for other dates — check one live before booking.</p>
        </div>
      )}
      {estimates.map(card)}
    </div>
  );
}
