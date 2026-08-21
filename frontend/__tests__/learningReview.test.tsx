import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { screen } from "@testing-library/react";
import { LearningReviewQueue } from "@/components/cockpit/LearningReviewQueue";
import { resetCsrfToken } from "@/lib/api/client";
import { renderWithClient } from "./utils/render";

const ORIGIN = "http://localhost:3000";
const ROOT = `${ORIGIN}/api/matters/m1/learnings/proposals`;
let status: "needs_review" | "accepted" = "needs_review";
let activeTiers: string[] = [];
let imports: unknown[] = [];
const bodies: unknown[] = [];

const proposal = {
  proposal_id: "learning-0123456789abcdef",
  import_id: "learning-import-0123456789abcdef",
  source_matter_id: "m1",
  run_id: "r1",
  task: "discovery-responses",
  anchor_id: "resp-ROG-1",
  baseline_text: "The inspection occurred in April.",
  edited_text: "The inspection occurred in May.",
  baseline_sha256: "a".repeat(64),
  edited_sha256: "b".repeat(64),
  critic_markup: "The inspection occurred in {~~April.~>May.~~}",
  word_changes: 1,
  proposed_tier: "matter",
  proposed_text: "Prefer the attorney-reviewed timing formulation.",
  created_at: "2026-08-21T21:00:00+00:00",
};

function view() {
  return {
    ...proposal,
    status,
    accepted_text:
      status === "accepted" ? "Prefer the attorney-reviewed timing formulation." : null,
    active_tiers: activeTiers,
    review_history: status === "accepted" ? [{ review_id: "review" }] : [],
  };
}

const server = setupServer(
  http.get(`${ORIGIN}/api/csrf`, () => HttpResponse.json({ csrf_token: "test-token" })),
  http.get(ROOT, () =>
    HttpResponse.json({
      schema_version: "1.0",
      kind: "learning_proposals",
      imports,
      proposals: [view()],
    }),
  ),
  http.post(`${ROOT}/:proposalId/accept`, async ({ request }) => {
    bodies.push(await request.json());
    status = "accepted";
    activeTiers = ["matter"];
    return HttpResponse.json({
      schema_version: "1.0",
      kind: "learning_proposal",
      proposal: view(),
    });
  }),
  http.post(`${ROOT}/:proposalId/scrub`, async ({ request }) => {
    bodies.push(await request.json());
    return HttpResponse.json({
      schema_version: "1.0",
      kind: "learning_scrub_preview",
      rendered_diff: "Prefer the attorney-reviewed timing formulation.",
      rendered_diff_sha256: "c".repeat(64),
    });
  }),
  http.post(`${ROOT}/:proposalId/promote`, async ({ request }) => {
    const body = await request.json();
    bodies.push(body);
    activeTiers = ["matter", "area"];
    return HttpResponse.json({
      schema_version: "1.0",
      kind: "learning_proposal",
      proposal: view(),
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
  status = "needs_review";
  activeTiers = [];
  bodies.length = 0;
  imports = [];
});
afterAll(() => {
  server.close();
  delete (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__;
});

describe("edit-learning keeps acceptance and shared promotion human-controlled", () => {
  it("accepts only for the next run, then requires scrub preview and public confirmation", async () => {
    const user = userEvent.setup();
    renderWithClient(<LearningReviewQueue matterId="m1" runId="r1" />);

    expect(await screen.findByRole("heading", { name: "resp-ROG-1" })).toBeInTheDocument();
    expect(screen.getByText(/accepted learning affects the next run/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept for matter" }));
    expect(await screen.findByText(/matter learning accepted for the next run/i)).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({
      reviewed_text: "Prefer the attorney-reviewed timing formulation.",
    });

    await user.click(screen.getByRole("button", { name: "Preview sharing scrub" }));
    expect(await screen.findByText("Rendered scrub diff")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stage area candidate" })).toBeDisabled();
    await user.type(
      screen.getByLabelText(/ethical-wall exclusions/i),
      "2026-08-21-conflict-matter",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Stage area candidate" }));
    expect(bodies[2]).toMatchObject({
      target_tier: "area",
      confirm_scrub_diff: true,
      scrub_diff_sha256: "c".repeat(64),
      excluded_matter_ids: ["2026-08-21-conflict-matter"],
    });
  });

  it("keeps an ambiguous import visible for human anchor review after reload", async () => {
    imports = [
      {
        import_id: "learning-import-fedcba9876543210",
        source_matter_id: "m1",
        run_id: "r1",
        source_name: "ambiguous.docx",
        source_sha256: "d".repeat(64),
        imported_at: "2026-08-21T21:00:00+00:00",
        auto_routable: false,
        blockers: ["anchor 'resp-ROG-1' occurs more than once"],
        anchors: [],
      },
    ];
    renderWithClient(<LearningReviewQueue matterId="m1" runId="r1" />);

    expect(await screen.findByLabelText("Blocked DOCX imports")).toHaveTextContent(
      "ambiguous.docx",
    );
    expect(screen.getByLabelText("Blocked DOCX imports")).toHaveTextContent(
      "occurs more than once",
    );
  });
});
