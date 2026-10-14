// Bags for the search form and the watch editor: a trolley for everyone (Google
// adds those fees itself) and a checked-bag count for the whole party (fees
// added from the airline's fee table — Google has no checked-bag filter).
import { maxCheckedBags } from "../lib/search";
import type { Passengers } from "../types";
import Icon from "./Icon";
import "./bags.css";

export default function BagsPicker({
  cabinBag,
  checkedBags,
  passengers,
  onChange,
}: {
  cabinBag: boolean;
  checkedBags: number;
  passengers: Passengers;
  onChange: (patch: { cabin_bag?: boolean; checked_bags?: number; checked_bag?: boolean }) => void;
}) {
  const max = maxCheckedBags(passengers);
  const value = Math.min(checkedBags, max);
  const set = (n: number) => onChange({ checked_bags: n, checked_bag: n > 0 });
  return (
    <div className="sf-option">
      <span className="field-label">Bags in the price</span>
      <div className="bags-picker">
        <button
          type="button"
          className={`chip chip-toggle${cabinBag ? " chip-active" : ""}`}
          aria-pressed={cabinBag}
          title="Prices include a 10kg cabin trolley for every traveller with a seat"
          onClick={() => onChange({ cabin_bag: !cabinBag })}
        >
          <Icon name="bag" size={14} /> 10 kg trolley for everyone
        </button>
        <div className="bag-stepper" role="group" aria-label="Checked bags, whole party">
          <Icon name="luggage" size={14} />
          <span className="bag-stepper-label">Checked bags</span>
          <button type="button" className="icon-btn round" onClick={() => set(value - 1)} disabled={value <= 0} aria-label="Fewer checked bags">
            <Icon name="minus" size={14} />
          </button>
          <span className="stepper-value" aria-live="polite">
            {value}
          </span>
          <button type="button" className="icon-btn round" onClick={() => set(value + 1)} disabled={value >= max} aria-label="More checked bags">
            <Icon name="plus" size={14} />
          </button>
        </div>
      </div>
      {value > 0 && <span className="muted small">20 kg each, for the whole party — fees are added to every price</span>}
    </div>
  );
}
