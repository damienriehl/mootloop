import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { screen } from "@testing-library/react";
import { ProductionReviewQueue } from "@/components/cockpit/ProductionReviewQueue";
import { resetCsrfToken } from "@/lib/api/client";
import { renderWithClient } from "./utils/render";

const ORIGIN = "http://localhost:3000";
const ENDPOINT = `${ORIGIN}/api/matters/m1/runs/r1/production/suggestions`;

const suggestion = {
  suggestion_id: "prod-suggestion-0123456789abcdef",
  source_matter_id: "m1",
  run_id: "r1",
  request_id: "RFP-1",
  doc_id: "doc-responsive00001",
  original_name: "service-contract.md",
  source_locator: "corpus/normalized/doc-responsive00001.md",
  request_sha256: "a".repeat(64),
  document_sha256: "b".repeat(64),
  classification: "responsive",
  score: 0.75,
  reason: "Matched request terms: contract, service",
  created_at: "2026-08-21T20:00:00+00:00",
  review_status: "needs_review",
  production_disposition: null,
  review_history: [],
};

const reviewBodies: unknown[] = [];

const server = setupServer(
  http.get(`${ORIGIN}/api/csrf`, () => HttpResponse.json({ csrf_token: "test-token" })),
  http.get(ENDPOINT, () =>
    HttpResponse.json({
      schema_version: "1.0",
      kind: "production_suggestions",
      run_id: "r1",
      suggestions: [suggestion],
      exclusions: [],
    }),
  ),
  http.post(`${ENDPOINT}/:suggestionId/review`, async ({ request }) => {
    const body = (await request.json()) as {
      action: string;
      production_disposition: string | null;
    };
    reviewBodies.push(body);
    return HttpResponse.json({
      schema_version: "1.0",
      kind: "production_suggestion",
      suggestion: {
        ...suggestion,
        review_status: "accepted",
        production_disposition:
          body.action === "production_review" ? body.production_disposition : null,
        review_history: [],
      },
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
  reviewBodies.length = 0;
});
afterAll(() => {
  server.close();
  delete (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__;
});

describe("RFP production review separates classification from disclosure authority", () => {
  it("records acceptance without a production disposition, then records production separately", async () => {
    const user = userEvent.setup();
    renderWithClient(<ProductionReviewQueue matterId="m1" runId="r1" />);

    await screen.findByRole("heading", { name: "service-contract.md" });
    expect(screen.getByText(/accepting one never authorizes production/i)).toBeInTheDocument();
    expect(screen.getByText(/recorded: no production decision/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Accept classification" }));
    await screen.findByText("Human review recorded.");
    expect(reviewBodies[0]).toMatchObject({
      action: "accept",
      production_disposition: null,
    });
    expect(screen.getByText(/recorded: no production decision/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "produce" }));
    expect(await screen.findByText(/recorded: produce/i)).toBeInTheDocument();
    expect(reviewBodies[1]).toMatchObject({
      action: "production_review",
      production_disposition: "produce",
    });
  });
});
