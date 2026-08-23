import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TournamentsPage from "../TournamentsPage";
import { http } from "@/shared/api";
import { renderWithProviders } from "@/test/render";
import type { TournamentSummaryResponse } from "@/shared/types";

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

const owner = { id: "owner-1", username: "Organizer", rating: 1800 };

function tournament(overrides: Partial<TournamentSummaryResponse>): TournamentSummaryResponse {
  return {
    id: "tournament-1",
    name: "Open Swiss",
    time_control_name: "5+0",
    tournament_type: "swiss",
    entry_fee_cents: 0,
    status: "registration",
    current_round: 0,
    total_rounds: 5,
    player_count: 8,
    owner,
    viewer_is_member: false,
    viewer_is_owner: false,
    created_at: "2026-08-01T10:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="current route">{location.pathname}</output>;
}

describe("TournamentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("joins free events directly but sends paid events to details without posting join", async () => {
    const tournaments = [
      tournament({ id: "free-open", name: "Free Open", entry_fee_cents: 0 }),
      tournament({ id: "paid-invitational", name: "Paid Invitational", entry_fee_cents: 1500 }),
    ];
    vi.mocked(http.get).mockResolvedValue(tournaments);
    vi.mocked(http.post).mockResolvedValue({});

    renderWithProviders(
      <>
        <TournamentsPage />
        <LocationProbe />
      </>,
      { route: "/tournaments" },
    );

    expect(await screen.findByText("Free Open")).toBeInTheDocument();
    expect(screen.getByText("Paid Invitational")).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: /^join$/i })[0]);

    await waitFor(() => {
      expect(http.post).toHaveBeenCalledWith("/tournaments/free-open/join");
    });

    vi.mocked(http.post).mockClear();

    const directPaidJoin = screen.queryAllByRole("button", { name: /^join$/i })[1] ?? null;
    const paidDetailAction = screen.getAllByRole("button", { name: /^view$/i })[1];

    await userEvent.click(directPaidJoin ?? paidDetailAction);

    expect(http.post).not.toHaveBeenCalledWith("/tournaments/paid-invitational/join");
    expect(screen.getByLabelText("current route")).toHaveTextContent("/tournaments/paid-invitational");
  });
});
