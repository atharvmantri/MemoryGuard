import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import TrustMeter from "./TrustMeter";

describe("TrustMeter (Requirements 19.3)", () => {
  it("exposes an accessible meter with the numeric score", () => {
    render(<TrustMeter trustScore={0.82} label="Memory A" />);
    const meter = screen.getByRole("meter");
    expect(meter).toHaveAttribute("aria-valuenow", "0.82");
    expect(meter).toHaveAttribute("aria-valuemin", "0");
    expect(meter).toHaveAttribute("aria-valuemax", "1");
    expect(meter).toHaveAttribute(
      "aria-label",
      "Memory A: trust score 0.82 (high)",
    );
  });

  it("classifies high / medium / low via data-trust-level", () => {
    const { rerender } = render(<TrustMeter trustScore={0.9} />);
    expect(screen.getByRole("meter")).toHaveAttribute(
      "data-trust-level",
      "high",
    );

    rerender(<TrustMeter trustScore={0.5} />);
    expect(screen.getByRole("meter")).toHaveAttribute(
      "data-trust-level",
      "medium",
    );

    rerender(<TrustMeter trustScore={0.2} />);
    expect(screen.getByRole("meter")).toHaveAttribute(
      "data-trust-level",
      "low",
    );
  });

  it("renders a visible percentage unless hidden", () => {
    const { rerender } = render(<TrustMeter trustScore={0.73} />);
    expect(screen.getByText("73%")).toBeInTheDocument();

    rerender(<TrustMeter trustScore={0.73} hideValue />);
    expect(screen.queryByText("73%")).not.toBeInTheDocument();
  });
});
