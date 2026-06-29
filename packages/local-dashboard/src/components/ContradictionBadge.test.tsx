import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ContradictionBadge from "./ContradictionBadge";

describe("ContradictionBadge (Requirement 19.4)", () => {
  it("renders a distinct warning when count > 0", () => {
    render(<ContradictionBadge count={3} />);
    const badge = screen.getByRole("status");
    expect(badge).toHaveAttribute("aria-label", "3 unresolved contradictions");
    expect(badge).toHaveTextContent("Contradiction (3)");
  });

  it("uses singular wording for a single contradiction", () => {
    render(<ContradictionBadge count={1} />);
    expect(screen.getByRole("status")).toHaveAttribute(
      "aria-label",
      "1 unresolved contradiction",
    );
  });

  it("renders from the boolean flag when count is absent", () => {
    render(<ContradictionBadge hasContradictions />);
    const badge = screen.getByRole("status");
    expect(badge).toHaveAttribute("aria-label", "Unresolved contradictions");
    expect(badge).toHaveTextContent("Contradiction");
  });

  it("renders nothing when there are no contradictions", () => {
    const { container, rerender } = render(<ContradictionBadge count={0} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<ContradictionBadge hasContradictions={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("clamps negative counts to a clear (no badge) state", () => {
    const { container } = render(<ContradictionBadge count={-2} />);
    expect(container).toBeEmptyDOMElement();
  });
});
