import { formatPrice } from "../lib/format";
import Icon from "./Icon";

/** "▼ $24" vs the previous run; green when cheaper. */
export default function PriceTrend({ current, previous, currency }: { current: number; previous: number | null; currency: string }) {
  if (previous == null) return null;
  const delta = Math.round(current - previous);
  if (delta === 0)
    return (
      <span className="trend trend-flat" title="Same best price as the previous run">
        = same
      </span>
    );
  const down = delta < 0;
  return (
    <span
      className={`trend ${down ? "trend-down" : "trend-up"}`}
      title={`${down ? "Cheaper" : "Pricier"} than the previous run (${formatPrice(previous, currency)})`}
    >
      <Icon name={down ? "arrowDown" : "arrowUp"} size={12} />
      {formatPrice(Math.abs(delta), currency)}
    </span>
  );
}
