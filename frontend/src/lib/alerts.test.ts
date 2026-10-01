import { describe, expect, it } from "vitest";
import { ALERT_LIMITS, alertSettingsOf, clampRule, DEFAULT_ALERTS, describeAlertRules, ruleChips, ruleHeadline, ruleWords, scoreTone } from "./watches";

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
