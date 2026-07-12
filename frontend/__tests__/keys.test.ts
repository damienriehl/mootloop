import { describe, expect, it } from "vitest";
import { keys } from "@/lib/api/keys";

describe("query key factories", () => {
  it("builds the matter/run hierarchy so parent keys prefix child keys", () => {
    expect(keys.all).toEqual(["mootloop"]);
    expect(keys.matters()).toEqual(["mootloop", "matters"]);

    const matter = keys.matter("fence-litigation");
    expect(matter.key).toEqual(["mootloop", "matters", "fence-litigation"]);
    expect(matter.runs()).toEqual(["mootloop", "matters", "fence-litigation", "runs"]);

    const run = matter.run("run-001");
    expect(run.key).toEqual(["mootloop", "matters", "fence-litigation", "runs", "run-001"]);
    expect(run.gates()).toEqual([
      "mootloop",
      "matters",
      "fence-litigation",
      "runs",
      "run-001",
      "gates",
    ]);
    expect(run.decisions().at(-1)).toBe("decisions");
    expect(run.requests().at(-1)).toBe("requests");
    expect(run.detail().at(-1)).toBe("detail");
  });

  it("nests every run key under its matter key (invalidation containment)", () => {
    const matter = keys.matter("m1");
    const run = matter.run("r1");
    for (const key of [run.key, run.gates(), run.decisions(), run.requests(), run.detail()]) {
      expect(key.slice(0, matter.key.length)).toEqual([...matter.key]);
    }
  });

  it("keeps distinct matters/runs on separate branches", () => {
    expect(keys.matter("a").key).not.toEqual(keys.matter("b").key);
    const m = keys.matter("m");
    expect(m.run("r1").key).not.toEqual(m.run("r2").key);
  });
});
