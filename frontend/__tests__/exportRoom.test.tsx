import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { screen, within } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "m1", runId: "r1" }),
}));

import ExportRoomPage from "@/app/matters/[id]/runs/[runId]/export/page";
import { resetCsrfToken } from "@/lib/api/client";
import type { DeliverableInfo } from "@/lib/api/types";
import { renderWithClient } from "./utils/render";

const ORIGIN = "http://localhost:3000";

/** export_ready is flipped per test; the deliverable list + gate reflect it. */
let exportReady = false;
/** The mint endpoint returns either a signed link or the typed 403 export_not_ready. */
let mintOutcome: "ok" | "not_ready" = "ok";

const CLEAN: DeliverableInfo = { name: "responses.docx", size_bytes: 20480, is_draft: false, requires_export_ready: true, downloadable: false };
const DRAFT: DeliverableInfo = { name: "responses.DRAFT.docx", size_bytes: 18000, is_draft: true, requires_export_ready: false, downloadable: true };
// Informational: clean (NOT draft) but never gated — the server marks it downloadable.
const INFO: DeliverableInfo = { name: "master.md", size_bytes: 41, is_draft: false, requires_export_ready: false, downloadable: true };

const server = setupServer(
  http.get(`${ORIGIN}/api/csrf`, () => HttpResponse.json({ csrf_token: "test-token" })),
  http.get(`${ORIGIN}/api/matters/:m/runs/:r/deliverables`, () =>
    HttpResponse.json({
      schema_version: "1.0",
      kind: "deliverables",
      run_id: "r1",
      export_ready: exportReady,
      deliverables: [
        { ...CLEAN, downloadable: exportReady },
        DRAFT,
        INFO,
      ],
    }),
  ),
  http.get(`${ORIGIN}/api/matters/:m/runs/:r`, () =>
    HttpResponse.json({
      schema_version: "1.0",
      kind: "run_status",
      run_id: "r1",
      status: "finished",
      mode: "autonomous",
      current_stage: null,
      task: "discovery-responses",
      total_spend_usd: 1.5,
      hard_cap_usd: null,
    }),
  ),
  http.post(`${ORIGIN}/api/matters/:m/runs/:r/deliverables/:name/link`, () => {
    if (mintOutcome === "not_ready") {
      return HttpResponse.json(
        { error: "export_not_ready", detail: "not ready", blockers: ["attestation", "citations"] },
        { status: 403 },
      );
    }
    return HttpResponse.json({
      schema_version: "1.0",
      kind: "signed_link",
      run_id: "r1",
      doc: "responses.DRAFT.docx",
      url: "/api/download?token=signed.tok",
      token: "signed.tok",
      is_draft: true,
      expires_at: "2026-07-12T00:10:00+00:00",
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
  exportReady = false;
  mintOutcome = "ok";
});
afterAll(() => {
  server.close();
  delete (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__;
});

function rowFor(name: string): HTMLElement {
  const rows = screen.getAllByTestId("deliverable-row");
  const row = rows.find((r) => within(r).queryByText(name));
  if (!row) throw new Error(`no deliverable row for ${name}`);
  return row;
}

describe("export room colophon gating (run NOT export-ready)", () => {
  it("gates the clean .docx and lets the DRAFT be released", async () => {
    renderWithClient(<ExportRoomPage />);

    await screen.findByText("responses.docx");
    // The gate banner reflects not-ready.
    expect(screen.getByTestId("export-gate")).toHaveTextContent(/not export-ready/i);

    // Clean file: gated reason shown, release toggle disabled.
    const clean = rowFor("responses.docx");
    expect(within(clean).getByTestId("gated-reason")).toBeInTheDocument();
    expect(within(clean).getByTestId("release-toggle")).toBeDisabled();

    // DRAFT file: chip present, releasable (toggle enabled, no gated reason).
    const draft = rowFor("responses.DRAFT.docx");
    expect(within(draft).getByTestId("draft-chip")).toBeInTheDocument();
    expect(within(draft).getByTestId("release-toggle")).toBeEnabled();
    expect(within(draft).queryByTestId("gated-reason")).not.toBeInTheDocument();

    // Informational file (master.md): clean but NOT draft, never gated — the server
    // marks it downloadable, so it must be releasable even when the run isn't ready.
    const info = rowFor("master.md");
    expect(within(info).getByTestId("release-toggle")).toBeEnabled();
    expect(within(info).queryByTestId("gated-reason")).not.toBeInTheDocument();
  });

  it("surfaces the 403 blockers and NEVER shows a signed url on a blocked mint", async () => {
    mintOutcome = "not_ready";
    exportReady = true; // list says ready so the clean row is releasable, but the mint still 403s
    const user = userEvent.setup();
    renderWithClient(<ExportRoomPage />);

    await screen.findByText("responses.docx");
    const clean = rowFor("responses.docx");
    await user.click(within(clean).getByTestId("release-toggle"));
    await user.click(within(clean).getByTestId("mint-link"));

    // The blockers list is rendered verbatim; no optimistic link ever appears.
    const blockers = await within(clean).findByTestId("mint-blockers");
    expect(blockers).toHaveTextContent("attestation");
    expect(blockers).toHaveTextContent("citations");
    expect(within(clean).queryByTestId("signed-url")).not.toBeInTheDocument();
    expect(within(clean).queryByTestId("mint-result")).not.toBeInTheDocument();
  });
});

describe("export room successful release", () => {
  it("shows the signed url and the access-audit confirmation only after the server responds", async () => {
    const user = userEvent.setup();
    renderWithClient(<ExportRoomPage />);

    await screen.findByText("responses.DRAFT.docx");
    const draft = rowFor("responses.DRAFT.docx");
    await user.click(within(draft).getByTestId("release-toggle"));

    // The colophon is shown before minting; the signed link is not yet present.
    expect(within(draft).getByTestId("colophon")).toBeInTheDocument();
    expect(within(draft).queryByTestId("signed-url")).not.toBeInTheDocument();

    await user.click(within(draft).getByTestId("mint-link"));

    // Only after the server responds: the result block, the signed url, and the audit note.
    const result = await within(draft).findByTestId("mint-result");
    expect(within(result).getByTestId("signed-url")).toHaveAttribute("href", "/api/download?token=signed.tok");
    expect(result).toHaveTextContent(/logged to access audit/i);
  });
});
