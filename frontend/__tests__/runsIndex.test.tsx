import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { listRuns } = vi.hoisted(() => ({
  listRuns: vi.fn().mockResolvedValue([
    {
      run_id: "u17a-injection-final-20260823",
      status: "needs_attention",
      mode: "autonomous",
      current_stage: null,
      task: "discovery-responses",
      total_spend_usd: 0,
      hard_cap_usd: null,
    },
  ]),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "2026-08-22-synthetic-u17a" }),
}));

vi.mock("@/lib/api/runs", () => ({ listRuns }));

import RunsIndexPage from "@/app/matters/[id]/runs/page";
import { renderWithClient } from "./utils/render";

describe("runs index responsive layout", () => {
  it("stacks long run metadata at phone width instead of forcing horizontal overflow", async () => {
    renderWithClient(<RunsIndexPage />);

    const link = await screen.findByRole("link", { name: /u17a-injection-final-20260823/i });
    expect(link).toHaveClass("min-w-0", "flex-col", "sm:flex-row");
    expect(link.parentElement).toHaveClass("min-w-0");
    expect(screen.getByText("u17a-injection-final-20260823")).toHaveClass("break-all");
  });
});
