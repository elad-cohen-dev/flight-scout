import { useMemo, useState } from "react";
import { durationMinutes } from "../lib/format";
import type { StoredDeal } from "../types";
import DealCard from "./DealCard";
import Segmented from "./Segmented";

type Sort = "price" | "duration" | "depart";

function sortDeals(deals: StoredDeal[], sort: Sort): StoredDeal[] {
  const list = [...deals];
  if (sort === "price") list.sort((a, b) => a.price - b.price);
  if (sort === "duration")
    list.sort((a, b) => (durationMinutes(a.duration) ?? 1e9) - (durationMinutes(b.duration) ?? 1e9) || a.price - b.price);
  if (sort === "depart")
    list.sort((a, b) => `${a.depart_date} ${a.depart_time}`.localeCompare(`${b.depart_date} ${b.depart_time}`) || a.price - b.price);
  return list;
}

/** Deals of one run, sortable; the cheapest gets a highlight when sorted by price. */
export default function DealList({ deals, sortable = true }: { deals: StoredDeal[]; sortable?: boolean }) {
  const [sort, setSort] = useState<Sort>("price");
  const sorted = useMemo(() => sortDeals(deals, sort), [deals, sort]);
  const cheapest = Math.min(...deals.map((d) => d.price));
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
      {sorted.map((d) => (
        <DealCard key={d.id} deal={d} highlight={deals.length > 1 && d.price === cheapest && sorted[0] === d} />
      ))}
    </div>
  );
}
