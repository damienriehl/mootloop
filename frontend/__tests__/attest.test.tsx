import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { AttestPanel } from "@/components/inbox/AttestPanel";
import { resetCsrfToken } from "@/lib/api/client";
import { renderWithClient } from "./utils/render";

const ORIGIN = "http://localhost:3000";

/** A gate we release manually so the test can observe the pre-response UI. */
let releaseAttest: (() => void) | null = null;
let attestGate: Promise<void>;

const attestation = {
  schema_version: "1.0",
  attestation_id: "att-1",
  run_id: "r1",
  master_sha256: "MASTER_HASH_ABC123",
  ledger_head_sha256: "LEDGER_HEAD_DEF456",
  reviewer: "attorney@example.com",
  attested_at: "2026-07-12T00:00:00Z",
  valid: true,
  reason: null,
};

const server = setupServer(
  http.get(`${ORIGIN}/api/csrf`, () => HttpResponse.json({ csrf_token: "test-token" })),
  http.post(`${ORIGIN}/api/matters/:m/runs/:r/attest`, async () => {
    await attestGate; // block until the test releases it
    return HttpResponse.json({ schema_version: "1.0", kind: "attested", attestation });
  }),
);

beforeAll(() => {
  (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__ = ORIGIN;
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
  resetCsrfToken();
  releaseAttest = null;
});
afterAll(() => {
  server.close();
  delete (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__;
});

describe("attestation is never optimistic", () => {
  it("shows the colophon ONLY after the server records the attestation", async () => {
    attestGate = new Promise<void>((resolve) => {
      releaseAttest = resolve;
    });

    const user = userEvent.setup();
    renderWithClient(<AttestPanel matterId="m1" runId="r1" blocked={false} />);

    // Open the deliberate certification screen.
    await user.click(screen.getByRole("button", { name: /attest this run/i }));
    expect(screen.getByRole("dialog", { name: /certify and release/i })).toBeInTheDocument();

    // Trigger the mutation; the server response is still gated.
    await user.click(screen.getByRole("button", { name: /certify & release/i }));

    // While the request is in flight, NOTHING reflects a completed attestation.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /recording…/i })).toBeInTheDocument(),
    );
    expect(screen.queryByText(/certified/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/MASTER_HASH_ABC123/)).not.toBeInTheDocument();

    // Now let the server respond — only now does the colophon appear.
    releaseAttest?.();
    await waitFor(() => expect(screen.getByText(/MASTER_HASH_ABC123/)).toBeInTheDocument());
    expect(screen.getByText(/^Certified$/)).toBeInTheDocument();
    expect(screen.getByText(/LEDGER_HEAD_DEF456/)).toBeInTheDocument();
  });
});
