import { useCallback, useEffect, useState } from "react";

export const TABS = ["search", "explore", "history", "watches", "trips", "trends"] as const;
export type Tab = (typeof TABS)[number];

function fromHash(): Tab {
  // "#watches/12" is the Watches tab with a sub-path the tab itself reads
  const h = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  return (TABS as readonly string[]).includes(h) ? (h as Tab) : "search";
}

/** Active tab mirrored in the URL hash, so refresh, back/forward and links work. */
export function useHashTab(): [Tab, (t: Tab) => void] {
  const [tab, setTab] = useState<Tab>(fromHash);
  useEffect(() => {
    const onChange = () => setTab(fromHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const go = useCallback((t: Tab) => {
    if (fromHash() !== t) window.location.hash = t; // pushes a history entry; hashchange updates state
    window.scrollTo({ top: 0 });
  }, []);
  return [tab, go];
}
