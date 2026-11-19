import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("never turns HTML from a job advert into elements", () => {
    const advert = 'Hi <script>alert(1)</script> <img src=x onerror="alert(2)"> <b>bold</b> <a href="javascript:alert(3)">x</a>';
    const { container } = render(<Markdown text={advert} />);
    expect(container.querySelector("script, img, b, [onerror]")).toBeNull();
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  });

  it("drops javascript: links written as Markdown", () => {
    const { container } = render(<Markdown text="[click me](javascript:alert(1))" />);
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  });

  it("opens links in a new tab without handing it this page", () => {
    render(<Markdown text="[the advert](https://example.com/job)" />);
    const link = screen.getByRole("link", { name: "the advert" });
    expect(link).toHaveAttribute("href", "https://example.com/job");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
