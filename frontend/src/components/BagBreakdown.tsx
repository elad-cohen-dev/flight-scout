// The "how did we get ≈ ₪2,980?" popover under a deal's price with bags: the
// fare, each bag line with its count, directions, fee range and where the fee
// came from, and the total's range.
import { useEffect, useId, useRef, useState } from "react";
import { basisLabel, lineAmount, lineLabel, totalRange } from "../lib/bags";
import { formatPrice } from "../lib/format";
import type { BagPricing } from "../types";
import Icon from "./Icon";
import "./bags.css";

export default function BagBreakdown({ pricing }: { pricing: BagPricing }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  const b = pricing;

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("touchstart", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div className="popover-anchor bag-bd" ref={ref}>
      <button
        type="button"
        className="bag-bd-toggle small"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        title="How this price with bags is made up"
      >
        fare {formatPrice(b.fare, b.currency)}
        <Icon name="info" size={12} />
      </button>
      {open && (
        <div className="popover bag-bd-pop" role="dialog" aria-label="Price with bags" id={id}>
          <table className="bag-bd-table">
            <tbody>
              <tr>
                <th scope="row">Fare</th>
                <td className="bag-bd-basis muted">as listed</td>
                <td className="bag-bd-amt">{formatPrice(b.fare, b.currency)}</td>
              </tr>
              {b.lines.map((l) => (
                <tr key={l.kind}>
                  <th scope="row">{lineLabel(l)}</th>
                  <td className={`bag-bd-basis bag-bd-${l.basis}`}>{basisLabel(l, b.as_of)}</td>
                  <td className="bag-bd-amt">{lineAmount(l, b.currency)}</td>
                </tr>
              ))}
              <tr className="bag-bd-total">
                <th scope="row">With bags</th>
                <td />
                <td className="bag-bd-amt">{totalRange(b)}</td>
              </tr>
            </tbody>
          </table>
          {b.added_at_checkout && (
            <p className="muted small bag-bd-note">The airline's site shows the fare first; bags are added during checkout.</p>
          )}
          {b.lines.some((l) => l.basis === "table" || l.basis === "typical") && (
            <p className="muted small bag-bd-note">Bag fees vary by date and route — the checkout has the final word.</p>
          )}
        </div>
      )}
    </div>
  );
}
