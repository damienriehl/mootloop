import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";

// next/navigation is mocked (the page reads matterId from useParams and navigates via
// useRouter on a successful start); `push` is captured so the confirm flow can assert it.
const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useParams: () => ({ id: "m1" }),
}));

// The real begin-draft store is Zustand+persist(localStorage); jsdom's localStorage is
// not a functional Storage here, so swap in a plain in-memory store with the same shape.
// (Draft persistence is exercised by the store's own contract, not this page test.)
vi.mock("@/lib/stores/beginDraft", async () => {
  const { create } = await vi.importActual<typeof import("zustand")>("zustand");
  const useBeginDraftStore = create<{
    intents: Record<string, string>;
    setIntent: (matterId: string, text: string) => void;
    clearIntent: (matterId: string) => void;
  }>((set) => ({
    intents: {},
    setIntent: (matterId, text) =>
      set((s) => ({ intents: { ...s.intents, [matterId]: text } })),
    clearIntent: (matterId) =>
      set((s) => {
        const { [matterId]: _removed, ...rest } = s.intents;
        return { intents: rest };
      }),
  }));
  return { useBeginDraftStore };
});

import BeginTaskPage from "@/app/matters/[id]/begin/page";
import { resetCsrfToken } from "@/lib/api/client";
import { useBeginDraftStore } from "@/lib/stores/beginDraft";
import type { TaskSpec } from "@/lib/api/types";
import { renderWithClient } from "./utils/render";

const ORIGIN = "http://localhost:3000";

/** The freeform mutation body the page POSTed (asserted for the resolved intent). */
let freeformIntent: string | null = null;
/** The start-run body the confirm click POSTed (asserted to carry the task_spec_id). */
let startBody: { task?: string; task_spec_id?: string } | null = null;

function taskSpec(over: Partial<TaskSpec> = {}): TaskSpec {
  return {
    schema_version: "1.0",
    task_spec_id: "taskspec-20260712-abc123",
    matter_id: "m1",
    task: "discovery-responses",
    source_lane: "freeform",
    intent_text: "answer the discovery served on us",
    folio_iri: null,
    folio_label: null,
    utbms: null,
    request_set_refs: [],
    created_at: "2026-07-12T00:00:00+00:00",
    ...over,
  };
}

const server = setupServer(
  http.get(`${ORIGIN}/api/csrf`, () => HttpResponse.json({ csrf_token: "test-token" })),
  http.post(`${ORIGIN}/api/matters/:m/tasks/freeform`, async ({ request }) => {
    const body = (await request.json()) as { intent_text: string };
    freeformIntent = body.intent_text;
    // Mirror the backend's deterministic resolver: "discovery" resolves, else unmapped.
    const resolved = /discovery|interrogator|rfp|rfa/i.test(body.intent_text);
    const spec = resolved
      ? taskSpec({ intent_text: body.intent_text })
      : taskSpec({ intent_text: body.intent_text, task: null });
    return HttpResponse.json({ schema_version: "1.0", kind: "task_spec", task_spec: spec, runnable: resolved });
  }),
  http.post(`${ORIGIN}/api/matters/:m/runs`, async ({ request }) => {
    startBody = (await request.json()) as { task?: string; task_spec_id?: string };
    return HttpResponse.json({
      schema_version: "1.0",
      kind: "run_status",
      run_id: "discovery-responses-20260712",
      status: "running",
      mode: "autonomous",
      current_stage: null,
      task: "discovery-responses",
      total_spend_usd: 0,
      hard_cap_usd: null,
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
  push.mockReset();
  freeformIntent = null;
  startBody = null;
  useBeginDraftStore.setState({ intents: {} }); // the omnibox draft is per-matter, module-global
});
afterAll(() => {
  server.close();
  delete (globalThis as { __MOOTLOOP_API_BASE__?: string }).__MOOTLOOP_API_BASE__;
});

describe("begin-task omnibox lane states", () => {
  it("resolves a sentence to a RUNNABLE slip with a start button", async () => {
    const user = userEvent.setup();
    renderWithClient(<BeginTaskPage />);

    await user.type(screen.getByTestId("omnibox-input"), "answer the discovery served on us");
    await user.click(screen.getByTestId("omnibox-submit"));

    const slip = await screen.findByTestId("task-slip");
    expect(slip).toBeInTheDocument();
    expect(screen.getByTestId("slip-lane-state")).toHaveTextContent(/runnable/i);
    expect(screen.getByTestId("slip-task")).toHaveTextContent("discovery-responses");
    // Runnable slip offers Start run; there is no honest-block panel.
    expect(screen.getByTestId("slip-start")).toBeInTheDocument();
    expect(screen.queryByTestId("slip-unmapped")).not.toBeInTheDocument();
    expect(freeformIntent).toBe("answer the discovery served on us");
  });

  it("records an UNMAPPED intent as an honest block with NO start button", async () => {
    const user = userEvent.setup();
    renderWithClient(<BeginTaskPage />);

    await user.type(screen.getByTestId("omnibox-input"), "draft an appellate brief about nothing");
    await user.click(screen.getByTestId("omnibox-submit"));

    await screen.findByTestId("task-slip");
    expect(screen.getByTestId("slip-lane-state")).toHaveTextContent(/unmapped/i);
    expect(screen.getByTestId("slip-task")).toHaveTextContent(/unmapped/i);
    // The honest block is shown; NO start button (no run can start from an unmapped slip).
    expect(screen.getByTestId("slip-unmapped")).toBeInTheDocument();
    expect(screen.queryByTestId("slip-start")).not.toBeInTheDocument();
  });
});

describe("begin-task confirm flow", () => {
  it("starts the run with the task_spec_id and navigates to the cockpit", async () => {
    const user = userEvent.setup();
    renderWithClient(<BeginTaskPage />);

    await user.type(screen.getByTestId("omnibox-input"), "answer the discovery served on us");
    await user.click(screen.getByTestId("omnibox-submit"));
    await user.click(await screen.findByTestId("slip-start"));

    // startRun POSTed the resolved slip's task_spec_id, then routed to that run's cockpit.
    await waitFor(() => expect(push).toHaveBeenCalledWith("/matters/m1/runs/discovery-responses-20260712"));
    expect(startBody).toEqual({ task: "discovery-responses", task_spec_id: "taskspec-20260712-abc123" });
  });
});
