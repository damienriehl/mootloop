import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RunControls } from "@/components/cockpit/RunControls";
import { resetCsrfToken } from "@/lib/api/client";
import { renderWithClient } from "./utils/render";

const ORIGIN = "http://localhost:3000";
const reopenBodies: unknown[] = [];

const server = setupServer(
  http.get(`${ORIGIN}/api/csrf`, () => HttpResponse.json({ csrf_token: "test-token" })),
  http.post(`${ORIGIN}/api/matters/m1/runs/r1/reopen`, async ({ request }) => {
    reopenBodies.push(await request.json());
    return HttpResponse.json({
      schema_version: "1.0",
      kind: "run_reopened",
      run_id: "r1",
      status: "running",
    });
  }),
);

beforeAll(() => {
  (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__ = ORIGIN;
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
  resetCsrfToken();
  reopenBodies.length = 0;
});
afterAll(() => {
  server.close();
  delete (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__;
});

describe("needs-attention recovery", () => {
  it("shows the blocker and records the repair reason plus attempt grant before queue repair", async () => {
    const user = userEvent.setup();
    renderWithClient(
      <RunControls
        matterId="m1"
        runId="r1"
        status="needs_attention"
        attentionBlockers={[
          {
            kind: "counter_capped_turn",
            ref: "associate:RFP-1",
            detail: "3 discarded attempts against a ceiling of 3; no completed turn recorded",
          },
        ]}
      />,
    );

    expect(screen.getByText(/3 discarded attempts against a ceiling of 3/i)).toBeInTheDocument();
    const reopen = screen.getByRole("button", { name: "Reopen and queue" });
    expect(reopen).toBeDisabled();

    await user.type(screen.getByLabelText("Repair performed"), "Corrected the persona prompt");
    await user.clear(screen.getByLabelText("Extra attempts"));
    await user.type(screen.getByLabelText("Extra attempts"), "2");
    await user.click(reopen);

    expect(
      await screen.findByText("Run reopened and its canonical queue work item is ready."),
    ).toBeInTheDocument();
    expect(reopenBodies).toEqual([
      { reason: "Corrected the persona prompt", grant_attempts: 2 },
    ]);
  });
});
