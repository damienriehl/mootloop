import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { DecisionCard } from "@/components/inbox/DecisionCard";
import { resetCsrfToken } from "@/lib/api/client";
import { renderWithClient } from "./utils/render";
import type { Decision } from "@/lib/api/types";

const ORIGIN = "http://localhost:3000";
let resolveCalls = 0;

const decision: Decision = {
  schema_version: "1.0",
  decision_id: "d1",
  run_id: "r1",
  kind: "rfa_disposition",
  status: "open",
  proposal: {
    summary: "RFA No. 12 — the fence encroaches",
    reasoning: "The survey places the fence 0.8ft over the boundary.",
    recommended: "admit",
    options: [
      { key: "admit", label: "Admit", consequence: "Binds the encroachment as an established fact." },
      { key: "deny", label: "Deny", consequence: "Preserves the dispute for trial." },
    ],
  },
};

const server = setupServer(
  http.get(`${ORIGIN}/api/csrf`, () => HttpResponse.json({ csrf_token: "test-token" })),
  http.post(
    `${ORIGIN}/api/matters/:m/runs/:r/decisions/:d/resolve`,
    () => {
      resolveCalls += 1;
      return HttpResponse.json({
        schema_version: "1.0",
        kind: "decision_resolved",
        decision: {
          ...decision,
          status: "approved",
          resolution: {
            action: "approve",
            chosen_key: "admit",
            decided_by: "attorney@example.com",
            source: "human",
            decided_at: "2026-07-12T00:00:00Z",
            note: "",
          },
        },
      });
    },
  ),
);

beforeAll(() => {
  (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__ = ORIGIN;
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
  resetCsrfToken();
  resolveCalls = 0;
});
afterAll(() => {
  server.close();
  delete (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__;
});

describe("RFA so-ordered two-step ceremony", () => {
  it("does NOT fire the resolve mutation on the first click", async () => {
    const user = userEvent.setup();
    renderWithClient(<DecisionCard matterId="m1" runId="r1" decision={decision} />);

    await user.click(screen.getByRole("button", { name: /so order/i }));

    // The first click only ARMS confirmation — the consequence line appears, no request.
    expect(screen.getByText(/consequence:/i)).toBeInTheDocument();
    // Give any (erroneous) request a chance to fire, then assert none did.
    await new Promise((r) => setTimeout(r, 30));
    expect(resolveCalls).toBe(0);
  });

  it("fires the mutation only after the SECOND (confirm) click", async () => {
    const user = userEvent.setup();
    renderWithClient(<DecisionCard matterId="m1" runId="r1" decision={decision} />);

    await user.click(screen.getByRole("button", { name: /so order/i }));
    expect(resolveCalls).toBe(0);

    await user.click(screen.getByRole("button", { name: /confirm so-ordered/i }));
    await waitFor(() => expect(resolveCalls).toBe(1));
  });
});
