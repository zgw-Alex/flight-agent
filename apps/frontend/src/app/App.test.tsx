import { render, screen } from "@testing-library/react";

import { App } from "./App";

describe("App", () => {
  it("renders the frontend development baseline smoke content", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Flight Agent" })).toBeInTheDocument();
    expect(screen.getByText("Development Baseline")).toBeInTheDocument();
  });
});
