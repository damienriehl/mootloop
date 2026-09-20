import { readFileSync } from "node:fs";
import path from "node:path";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

it("keeps source markup inert and ignores late results after navigation", async () => {
  document.body.innerHTML =
    '<a href="#content">Skip to content</a><main id="content" tabindex="-1"></main>';
  window.history.replaceState(null, "", "/demos/");
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  const descriptor = (id: string) => ({
    demo_id: id,
    title: `Example ${id}`,
    introduction: "Prepared example",
    collection: "synthetic",
    work_product: "Complaint",
    jurisdiction: "Test",
    court_level: "state-trial",
    practice_area: "Test",
  });
  const malicious =
    '<img src="https://unrequested.invalid/a" onerror="alert(1)">';
  const strategy = (id: string) => ({
    strategy_id: id,
    title: `Strategy ${id}`,
    input_summary: malicious,
    assumptions: ["Fictional"],
    stages: ["initial", "critique", "revised", "assessment"].map((kind) => ({
      kind,
      text: `${kind}\n\n**Prepared draft**\n\n**${malicious}**`,
    })),
    gate_state: {
      export_ready: false,
      blockers: ["attestation"],
      run_status: "finished",
      results: { attestation: "not_evaluated" },
    },
  });
  const snapshot = (id: string) => ({
    descriptor: descriptor(id),
    revision: "r1",
    provenance: {
      authorship: "Agent scripts",
      preparation: "scripted-replay",
      provider_calls: 0,
      editorial_changes: "Reviewed",
      limitations: ["No approval"],
    },
    strategies: [strategy("a"), strategy("b")],
    sources: [
      {
        source_id: "bad",
        title: "Unsafe source",
        url: "javascript:alert(1)",
        classification: "record",
        available_on: "2020-01-01",
        locator: "Test",
        redistribution_basis: "Test",
        retrieved_on: "2020-01-01",
        sha256: "a".repeat(64),
      },
    ],
    claims: [],
    local_instructions: "Use a local vault.",
  });
  let rejectFirst: (reason: Error) => void = () => {};
  const late = new Promise<Response>((_, reject) => {
    rejectFirst = reject;
  });
  const calls: string[] = [];
  const fetcher = vi.fn(async (url: string) => {
    calls.push(url);
    if (url === "/api/demos")
      return {
        ok: true,
        json: async () => ({
          entries: ["first", "second"].map((id) => ({
            descriptor: descriptor(id),
            revision: "r1",
          })),
        }),
      };
    if (url === "/api/demos/first/r1") return late;
    return { ok: true, json: async () => snapshot("second") };
  });
  vi.stubGlobal("fetch", fetcher);
  window.eval(
    readFileSync(
      path.resolve(process.cwd(), "../src/mootloop/web/static/library.js"),
      "utf8",
    ),
  );
  await screen.findByText("2 of 2 demos");
  fireEvent.input(screen.getByLabelText("Search demos"), {
    target: { value: "absent" },
  });
  await screen.findByText("0 of 2 demos");
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  fireEvent.click(screen.getByRole("link", { name: "Example first" }));
  await waitFor(() => expect(calls).toContain("/api/demos/first/r1"));
  window.history.pushState(null, "", "/demos/second?revision=r1");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await screen.findByRole("heading", { name: "Example second", level: 1 });
  rejectFirst(new Error("Old request failed"));
  await Promise.resolve();
  expect(screen.queryByText("Old request failed")).toBeNull();
  expect(
    screen.getByRole("heading", { name: "Example second", level: 1 }),
  ).toBeVisible();
  expect(document.querySelector("img")).toBeNull();
  expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
  expect(document.body.textContent).toContain(malicious);
  expect(document.querySelector(".stage strong")?.textContent).toBe("Prepared draft");
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  const callsBeforeAnchor = calls.length;
  fireEvent.click(screen.getByRole("link", { name: "Initial draft" }));
  expect(document.querySelector<HTMLDetailsElement>("#initial")?.open).toBe(true);
  expect(document.activeElement).toBe(document.querySelector("#initial summary"));
  fireEvent.click(screen.getByRole("link", { name: "Adversarial critique" }));
  expect(document.querySelector<HTMLDetailsElement>("#critique")?.open).toBe(true);
  expect(document.querySelector<HTMLDetailsElement>("#initial")?.open).toBe(true);
  window.history.pushState(null, "", "#initial");
  window.dispatchEvent(new PopStateEvent("popstate"));
  expect(document.querySelector<HTMLDetailsElement>("#critique")?.open).toBe(true);
  expect(calls).toHaveLength(callsBeforeAnchor);
  fireEvent.click(screen.getByRole("link", { name: "Skip to content" }));
  expect(document.activeElement).toBe(document.querySelector("#content"));
  document.querySelector<HTMLDetailsElement>("#initial")!.open = false;
  fireEvent.click(screen.getByRole("link", { name: "Initial draft" }));
  expect(document.querySelector<HTMLDetailsElement>("#initial")?.open).toBe(true);
  fireEvent.change(screen.getByLabelText("Choose a strategy"), {
    target: { value: "b" },
  });
  expect(window.location.search).toContain("strategy=b");
  expect(screen.getByRole("heading", { name: "Strategy b" })).toBeVisible();
  window.history.pushState(null, "", "/demos/second?revision=r1&strategy=a#critique");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await screen.findByRole("heading", { name: "Strategy a" });
  expect(document.querySelector<HTMLDetailsElement>("#critique")?.open).toBe(true);
  expect(calls.every((url) => url.startsWith("/api/demos"))).toBe(true);
  Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
