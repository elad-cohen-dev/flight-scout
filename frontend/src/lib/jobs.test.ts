import { describe, expect, it } from "vitest";
import { forgetJob, loadActiveJob, rememberJob, rerunTarget, waitForServer, wakeProgress } from "./jobs";

function memoryStore() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

const broken = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceeded");
  },
  removeItem: () => {
    throw new Error("SecurityError");
  },
};

describe("active job memory", () => {
  it("remembers, reloads and forgets the followed job", () => {
    const store = memoryStore();
    rememberJob({ job_id: "a1", kind: "search", title: "Searching TLV → Rome" }, store);
    expect(loadActiveJob(store)).toEqual({ job_id: "a1", kind: "search", title: "Searching TLV → Rome" });
    forgetJob("other", store); // a different job finishing leaves the newer one alone
    expect(loadActiveJob(store)?.job_id).toBe("a1");
    forgetJob("a1", store);
    expect(loadActiveJob(store)).toBeNull();
  });

  it("ignores garbage and survives storage that throws", () => {
    const store = memoryStore();
    store.setItem("fs_active_job", "{not json");
    expect(loadActiveJob(store)).toBeNull();
    store.setItem("fs_active_job", JSON.stringify({ job_id: "x", kind: "nope" }));
    expect(loadActiveJob(store)).toBeNull();
    expect(() => rememberJob({ job_id: "a", kind: "search", title: "" }, broken)).not.toThrow();
    expect(loadActiveJob(broken)).toBeNull();
    expect(() => forgetJob(undefined, broken)).not.toThrow();
    expect(loadActiveJob(null)).toBeNull();
  });
});

describe("run again", () => {
  it("re-submits a search's stored request or re-checks the watch", () => {
    const request = { origin: "TLV", destination: "Rome", dates_hint: "November", cabin_bag: false, checked_bag: false };
    expect(rerunTarget({ kind: "search", request })).toEqual({ type: "search", request });
    expect(rerunTarget({ kind: "watch_run", request: { watch_id: 3 } })).toEqual({ type: "watch", watchId: 3 });
  });

  it("offers nothing for the scheduled sweep or legacy jobs", () => {
    expect(rerunTarget({ kind: "watch_run", request: { scheduled: true } })).toBeNull();
    expect(rerunTarget({ kind: "search", request: null })).toBeNull();
    expect(rerunTarget({ kind: "search", request: undefined })).toBeNull();
  });
});

describe("waiting for a sleeping server", () => {
  const instant = () => Promise.resolve();

  it("stays quiet when the server answers straight away", async () => {
    let slow = 0;
    const wasSlow = await waitForServer({ ping: async () => true, onSlow: () => slow++, sleep: instant });
    expect(wasSlow).toBe(false);
    expect(slow).toBe(0);
  });

  it("flags a failed first answer and retries until the server is up", async () => {
    const answers = [false, false, true];
    let pings = 0;
    let slow = 0;
    const wasSlow = await waitForServer({
      ping: async () => answers[pings++],
      onSlow: () => slow++,
      sleep: instant,
    });
    expect([wasSlow, pings, slow]).toEqual([true, 3, 1]);
  });

  it("treats a thrown ping as not ready yet", async () => {
    let pings = 0;
    const wasSlow = await waitForServer({
      ping: async () => {
        if (pings++ === 0) throw new TypeError("Failed to fetch");
        return true;
      },
      sleep: instant,
    });
    expect([wasSlow, pings]).toEqual([true, 2]);
  });

  it("flags a slow answer after the threshold", async () => {
    let slow = 0;
    const wasSlow = await waitForServer({
      ping: () => new Promise((r) => setTimeout(() => r(true), 40)),
      onSlow: () => slow++,
      slowAfterMs: 10,
      sleep: instant,
    });
    expect([wasSlow, slow]).toEqual([true, 1]);
  });

  it("estimates progress without ever claiming done", () => {
    expect(wakeProgress(0)).toBe(0);
    expect(wakeProgress(30_000)).toBeCloseTo(0.475);
    expect(wakeProgress(10 * 60_000)).toBe(0.95);
  });
});
