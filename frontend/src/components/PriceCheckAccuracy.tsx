// Trends tab: one line on how often our prices matched the sellers' checkout
// ("Price checks: 7 of 9 matched; Wizz Air bags off by ~€15 (more at checkout)").
// Fetches its own summary; renders nothing before the first check or on error.
import { useEffect, useState } from "react";
import { fetchPriceCheckSummary } from "../api";
import { accuracyLine } from "../lib/priceCheck";
import type { PriceCheckSummary } from "../types";
import Icon from "./Icon";
import "./bags.css";

export default function PriceCheckAccuracy() {
  const [summary, setSummary] = useState<PriceCheckSummary | null>(null);
  useEffect(() => {
    let live = true;
    fetchPriceCheckSummary()
      .then((s) => live && setSummary(s))
      .catch(() => {
        // an older server or a blip: the line is optional
      });
    return () => {
      live = false;
    };
  }, []);
  const line = accuracyLine(summary);
  if (!line) return null;
  const detail = summary!.airlines.map((a) => `${a.airline}: ${a.same} of ${a.checks} matched`).join(" · ");
  return (
    <p className="pc-accuracy muted small" title={detail}>
      <Icon name="check" size={13} /> {line}
    </p>
  );
}
