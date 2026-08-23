import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TournamentDetailPage from "../TournamentDetailPage";
import { http } from "@/shared/api";
import { renderWithProviders } from "@/test/render";
import type { PaymentIntentResponse, TournamentDetailResponse } from "@/shared/types";

vi.mock("@/shared/api", () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock("@/widgets/app-shell", () => ({
  AppShell: ({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) => (
    <main>
      <h1>{title}</h1>
      <div>{actions}</div>
      {children}
    </main>
  ),
}));

const tournament: TournamentDetailResponse = {
  id: "paid-invitational",
  name: "Paid Invitational",
  time_control_name: "5+0",
  tournament_type: "swiss",
  entry_fee_cents: 1500,
  status: "registration",
  current_round: 0,
  total_rounds: 5,
  player_count: 12,
  owner: { id: "owner-1", username: "Organizer", rating: 1800 },
  viewer_is_member: false,
  viewer_is_owner: false,
  created_at: "2026-08-01T10:00:00Z",
  started_at: null,
  finished_at: null,
  standings: [],
  rounds: [],
};

const entryPayment: PaymentIntentResponse = {
  id: "payment-1",
  user_id: "user-1",
  tournament_id: tournament.id,
  scheduled_match_id: null,
  subject_type: "tournament_entry",
  amount_cents: tournament.entry_fee_cents,
  currency: "COIN",
  status: "requires_payment",
  scenario: null,
  reserved_until: "2026-08-01T10:10:00Z",
  metadata: {},
  created_at: "2026-08-01T10:00:00Z",
  updated_at: null,
};

describe("TournamentDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("omits disputed from the public entry payment emulator and keeps supported scenarios", async () => {
    vi.mocked(http.get).mockImplementation((endpoint) => {
      if (endpoint === "/tournaments/paid-invitational") return Promise.resolve(tournament);
      throw new Error(`Unexpected GET ${endpoint}`);
    });
    vi.mocked(http.post).mockImplementation((endpoint) => {
      if (endpoint === "/tournaments/paid-invitational/entry-payment") return Promise.resolve(entryPayment);
      throw new Error(`Unexpected POST ${endpoint}`);
    });

    renderWithProviders(
      <Routes>
        <Route path="/tournaments/:tournamentId" element={<TournamentDetailPage />} />
      </Routes>,
      { route: "/tournaments/paid-invitational" },
    );

    await userEvent.click(await screen.findByRole("button", { name: /reserve entry 1,500 coins/i }));

    expect(http.post).toHaveBeenCalledWith("/tournaments/paid-invitational/entry-payment");
    expect(await screen.findByRole("heading", { name: "Entry Payment Emulator" })).toBeInTheDocument();

    for (const scenario of ["success", "pending", "failed", "cancelled", "expired"]) {
      expect(screen.getByRole("button", { name: scenario })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "disputed" })).not.toBeInTheDocument();
  });
});
