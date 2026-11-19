import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import Icon from "./Icon";
import type { IconName } from "./Icon";

/** A button that opens a small panel under it: the filter chips and the
 *  "When" field of the search bar. Closes on Escape and on a press outside.
 *  `open` / `onOpenChange` make it controlled (the form opens the price panel
 *  itself when a watch needs a price). */
export default function Dropdown({
  label,
  value,
  icon,
  variant = "chip",
  active = false,
  align = "left",
  open: controlled,
  onOpenChange,
  panelLabel,
  children,
}: {
  label: string; // what the control is ("Stops"); the caption of a field
  value?: string; // what it is set to ("Direct"); shown instead of the label on a chip
  icon?: IconName;
  variant?: "chip" | "field";
  active?: boolean; // set to something other than the default
  align?: "left" | "right";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  panelLabel?: string;
  children: ReactNode | ((close: () => void) => ReactNode);
}) {
  const [inner, setInner] = useState(false);
  const open = controlled ?? inner;
  const root = useRef<HTMLDivElement>(null);
  const id = useId();
  const setOpen = (v: boolean) => {
    setInner(v);
    onOpenChange?.(v);
  };

  useEffect(() => {
    if (!open) return;
    // On a phone the panel can open below the fold: bring all of it into view.
    root.current?.querySelector(".dd-panel")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const onDown = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      root.current?.querySelector<HTMLButtonElement>(".dd-btn")?.focus();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setOpen is stable enough for a listener
  }, [open]);

  return (
    <div className={`dd dd-${variant}${open ? " dd-open" : ""}${active ? " dd-active" : ""}`} ref={root}>
      <button
        type="button"
        className="dd-btn"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={variant === "chip" && value ? `${label}: ${value}` : undefined}
        onClick={() => setOpen(!open)}
      >
        {variant === "field" ? (
          <>
            <span className="dd-caption">{label}</span>
            <span className="dd-value">
              {icon && <Icon name={icon} size={16} />}
              <span className="dd-value-text">{value}</span>
            </span>
          </>
        ) : (
          <>
            {icon && <Icon name={icon} size={14} />}
            <span>{value ?? label}</span>
            <Icon name="chevronDown" size={13} className="dd-caret" />
          </>
        )}
      </button>
      {open && (
        <div className={`dd-panel dd-panel-${align}`} id={id} role="dialog" aria-label={panelLabel ?? label}>
          {typeof children === "function" ? children(() => setOpen(false)) : children}
        </div>
      )}
    </div>
  );
}
