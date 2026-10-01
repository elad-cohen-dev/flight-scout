import { describe, expect, it } from "vitest";
import type { WatchStats } from "../types";
import {
  ALERT_LIMITS,
  alertSettingsOf,
  basisNote,
  clampRule,
  DEFAULT_ALERTS,
  describeAlertRules,
  legacyNote,
  legacyOnly,
  priceRange,
  ruleChips,
  ruleHeadline,
  ruleWords,
  scoreTone,
} from "./watches";

describe("alert settings", () => {
  it("fills defaults for watches without settings and keeps what is set", () => {
    expect(alertSettingsOf({})).toEqual(DEFAULT_ALERTS);
    expect(alertSettingsOf({ alerts: { ...DEFAULT_ALERTS, new_low: false, big_drop_pct: 25 } })).toMatchObject({
      new_low: false,
      big_drop_pct: 25,
      under_typical: true,
    });
  });

  it("describes the smart rules in one sentence", () => {
    expect(describeAlertRules(DEFAULT_ALERTS)).toBe(
      "Also on the lowest price in 30 days, 20% under the typical price or a 15% drop since the last check."
    );
    expect(describeAlertRules({ ...DEFAULT_ALERTS, threshold: false, new_low: false, under_typical: false })).toBe(
      "Alerts on a 15% drop since the last check."
    );
    expect(describeAlertRules({ ...DEFAULT_ALERTS, new_low: false, under_typical: false, big_drop: false })).toBe("");
  });

  it("clamps rule numbers to the backend's bounds", () => {
    expect(clampRule("3", ALERT_LIMITS.days, 30)).toBe(7);
    expect(clampRule("400", ALERT_LIMITS.days, 30)).toBe(365);
    expect(clampRule("", ALERT_LIMITS.pct, 20)).toBe(20);
    expect(clampRule("abc", ALERT_LIMITS.pct, 20)).toBe(20);
    expect(clampRule("12.6", ALERT_LIMITS.pct, 20)).toBe(13);
  });
});

describe("score + rule labels", () => {
  it("maps scores to tones", () => {
    expect(scoreTone(92)).toEqual({ tone: "great", label: "Great deal" });
    expect(scoreTone(60).tone).toBe("good");
    expect(scoreTone(50).tone).toBe("fair");
    expect(scoreTone(10).tone).toBe("poor");
  });

  it("lists enabled rules as chips", () => {
    expect(ruleChips(DEFAULT_ALERTS)).toEqual(["Lowest in 30 days", "20% under typical", "15% drop"]);
    expect(ruleChips({ ...DEFAULT_ALERTS, new_low: false, big_drop: false })).toEqual(["20% under typical"]);
  });

  it("folds logged rules into a headline", () => {
    expect(ruleHeadline("threshold,new_low,under_typical,big_drop")).toBe("New low +3");
    expect(ruleHeadline("threshold")).toBe("Under target");
    expect(ruleHeadline("big_drop,under_typical")).toBe("Under typical +1");
  });

  it("names logged rules", () => {
    expect(ruleWords("threshold,new_low")).toBe("Under target · New low");
    expect(ruleWords("big_drop")).toBe("Big drop");
  });
});

describe("stats basis", () => {
  const base: WatchStats = {
    currency: "USD",
    runs: 39,
    last_best: 324,
    last_run_at: null,
    low_30d: 300,
    typical: 337.5,
    all_time_low: 300,
    score: 71,
  };

  it("says what the numbers rest on", () => {
    expect(basisNote({ ...base, basis: "verified", basis_runs: 12 })).toBe("Based on 12 verified checks");
    expect(basisNote({ ...base, basis: "google", basis_runs: 1 })).toBe("Based on Google's price history + 1 verified check");
    expect(basisNote({ ...base, basis: "google", basis_runs: 0 })).toBe("Based on Google's price history");
    expect(basisNote({ ...base, basis: "explore", basis_runs: 2 })).toBe("Based on 2 Google Explore checks — dates' prices, not confirmed fares yet");
    expect(basisNote({ ...base, basis: "cached", basis_runs: 2 })).toBe("Based on 2 cached-fare checks — not confirmed live yet");
    expect(basisNote({ ...base, basis: "legacy" })).toBe("");
    expect(basisNote(base)).toBe(""); // pre-2.1 server
  });

  it("hides legacy-only numbers behind a muted note", () => {
    const legacy = { ...base, basis: "legacy" as const, low_30d: 82, typical: 256 };
    expect(legacyOnly(legacy)).toBe(true);
    expect(legacyOnly(base)).toBe(false);
    expect(legacyNote(legacy, "USD")).toEqual({
      summary: "Only 39 older estimated checks so far — not verified",
      detail: "30-day low $82 · typical $256 — web-search estimates, often without bags.",
    });
    expect(legacyNote({ ...base, basis: "verified", legacy_runs: 38, legacy: { runs: 38, low_30d: 82, typical: 256, all_time_low: 69 } }, "USD")?.detail).toContain(
      "not used for the score or alerts"
    );
    expect(legacyNote({ ...base, basis: "google", legacy_runs: 38 }, "USD")).toEqual({ summary: "38 older estimated checks not counted", detail: "" });
    expect(legacyNote({ ...base, basis: "verified", legacy_runs: 0 }, "USD")).toBeNull();
  });

  it("formats Google's typical range compactly", () => {
    expect(priceRange(285, 390, "USD")).toBe("$285–390");
    expect(priceRange(950, 1300, "CZK")).toBe("950–1,300 CZK");
  });
});
