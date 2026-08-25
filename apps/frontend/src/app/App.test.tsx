import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";

import type { ConversationReadResponse } from "./api";
import { startConversation } from "./api";
import { App } from "./App";

vi.mock("./api", () => {
  return { startConversation: vi.fn() };
});

const mockedStartConversation = startConversation as Mock;

describe("App", () => {
  beforeEach(() => {
    mockedStartConversation.mockReset();
  });

  it("submits the structured public DTO and shows loading state", async () => {
    mockedStartConversation.mockResolvedValueOnce(publishedConversation());
    render(<App />);

    fireEvent.change(screen.getByLabelText("Origin"), { target: { value: "sha" } });
    fireEvent.change(screen.getByLabelText("Destination"), { target: { value: "lax" } });
    fireEvent.change(screen.getByLabelText("Departure date"), { target: { value: "2026-09-02" } });
    fireEvent.change(screen.getByLabelText("Max price"), { target: { value: "1500" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(screen.getByText("Loading conversation projection")).toBeInTheDocument();
    await waitFor(() =>
      expect(mockedStartConversation).toHaveBeenCalledWith({
        origin: "SHA",
        destination: "LAX",
        departure_date: "2026-09-02",
        max_price_cny: 1500,
        lower_price_preferred: true,
      }),
    );
  });

  it("renders transport errors distinctly", async () => {
    mockedStartConversation.mockRejectedValueOnce(new Error("network is down"));
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByRole("heading", { name: "Transport Error" })).toBeInTheDocument();
    expect(screen.getByText("network is down")).toBeInTheDocument();
  });

  it("renders PUBLISHED from the current published recommendation", async () => {
    mockedStartConversation.mockResolvedValueOnce(publishedConversation());
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByRole("heading", { name: "PUBLISHED" })).toBeInTheDocument();
    expect(screen.getByText("PEK to SHA")).toBeInTheDocument();
    expect(screen.getByText("2026-09-01")).toBeInTheDocument();
    expect(screen.getByText("CNY 980")).toBeInTheDocument();
    expect(screen.getByText("BEST_OVERALL")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("Selected from rank 1 lower-price result")).toBeInTheDocument();
    expect(screen.getByText("OFFER:offer-a")).toBeInTheDocument();
    expect(screen.getByText("publication-1")).toBeInTheDocument();
    expect(screen.queryByText("recommendation-result-1")).not.toBeInTheDocument();
  });

  it.each([
    ["SEARCH_EMPTY", "No search results were returned for this requirement."],
    ["FILTER_EMPTY", "Search results were found, but none satisfied the max price."],
    ["PROVIDER_ERROR", "The flight provider did not return a usable search result."],
    ["NOT_READY", "The requirement is not search-ready yet."],
  ] as const)("renders backend outcome %s without a fake publication", async (outcome, message) => {
    mockedStartConversation.mockResolvedValueOnce({
      ...publishedConversation(),
      outcome,
      current_published_recommendation: null,
    });
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByRole("heading", { name: outcome })).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByText("CNY 980")).not.toBeInTheDocument();
    expect(screen.queryByText("publication-1")).not.toBeInTheDocument();
  });
});

function publishedConversation(): ConversationReadResponse {
  return {
    conversation_id: "conversation-1",
    outcome: "PUBLISHED",
    requirement_id: "requirement-1",
    requirement_version: 1,
    execution_id: "execution-1",
    current_published_recommendation: {
      publication_id: "publication-1",
      recommendation_result_id: "recommendation-result-1",
      execution_id: "execution-1",
      requirement_id: "requirement-1",
      requirement_version: 1,
      snapshot_id: "snapshot-1",
      snapshot_version: 1,
      published_at: "2026-08-25T08:00:00+00:00",
      route_origin: "PEK",
      route_destination: "SHA",
      departure_date: "2026-09-01",
      selected_price_amount: "980",
      selected_price_currency: "CNY",
      role: "BEST_OVERALL",
      reason: "Selected from rank 1 lower-price result",
      evidence: ["OFFER:offer-a"],
    },
  };
}
