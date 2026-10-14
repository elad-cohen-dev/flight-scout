// After a Book click and a return to the tab: "Was it the same price?" on that
// deal's card. Non-blocking — answering or dismissing are both one tap.
import { useState } from "react";
import { addPriceCheck } from "../api";
import { formatPrice } from "../lib/format";
import { parseTotal } from "../lib/priceCheck";
import type { PendingCheck } from "../lib/priceCheck";
import Icon from "./Icon";
import "./bags.css";

type Phase = "ask" | "type" | "saving" | "done" | "error";

export default function PriceCheckPrompt({ check, onClose }: { check: PendingCheck; onClose: () => void }) {
  const [phase, setPhase] = useState<Phase>("ask");
  const [text, setText] = useState("");
  const shown = formatPrice(check.shownTotal, check.currency);

  async function save(same: boolean, actual: number | null) {
    setPhase("saving");
    try {
      await addPriceCheck({
        deal_id: check.dealId,
        seller: check.seller,
        shown_total: check.shownTotal,
        actual_total: actual,
        same,
        currency: check.currency,
      });
      setPhase("done");
      window.setTimeout(onClose, 2500);
    } catch {
      setPhase("error");
    }
  }

  const actual = parseTotal(text);
  return (
    <div className="pc-prompt" role="status" aria-live="polite">
      {phase === "done" ? (
        <span className="small">
          <Icon name="check" size={12} /> Thanks — noted. It sharpens future prices.
        </span>
      ) : phase === "error" ? (
        <span className="small">Couldn't save that just now.</span>
      ) : phase === "ask" || phase === "saving" ? (
        <>
          <span className="small">
            Was it the same price on {check.seller || "the site"}? We showed <strong>{shown}</strong>.
          </span>
          <span className="pc-actions">
            <button type="button" className="btn btn-secondary btn-sm" disabled={phase === "saving"} onClick={() => save(true, null)}>
              Yes
            </button>
            <button type="button" className="btn btn-secondary btn-sm" disabled={phase === "saving"} onClick={() => setPhase("type")}>
              No
            </button>
          </span>
        </>
      ) : (
        <form
          className="pc-form"
          onSubmit={(e) => {
            e.preventDefault();
            save(false, actual);
          }}
        >
          <label className="small" htmlFor={`pc-${check.dealId}`}>
            Total you saw ({check.currency})
          </label>
          <input
            id={`pc-${check.dealId}`}
            className="input pc-input"
            inputMode="decimal"
            autoFocus
            placeholder={String(Math.round(check.shownTotal))}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" className="btn btn-primary btn-sm">
            {actual == null ? "Skip amount" : "Save"}
          </button>
        </form>
      )}
      {phase !== "done" && (
        <button type="button" className="icon-btn pc-close" aria-label="Dismiss" onClick={onClose}>
          <Icon name="x" size={14} />
        </button>
      )}
    </div>
  );
}
