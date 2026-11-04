import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App, renderHighlightedText } from "../src/App";
import type { FieldDiagnostic } from "../src/contracts";

describe("LocaleGate Web Dashboard", () => {
  it("renders navigation tabs and Croatian preset example", () => {
    render(<App />);
    expect(screen.getByText("LocaleGate")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Try a Request" })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    expect(
      screen.getByDisplayValue(
        "Evidentiraj tro\u0161ak od 1.234,56 \u20ac od 03.11.2026. i predaj ga na odobrenje."
      )
    ).toBeInTheDocument();
  });

  it("switches presets including role-binding decoy preset and tabs cleanly", () => {
    render(<App />);
    fireEvent.click(
      screen.getByRole("button", {
        name: /Ambiguous Slash Date \(03\/04\/2026 in hr-HR\)/i,
      })
    );
    expect(
      screen.getByDisplayValue("Evidentiraj tro\u0161ak od 85,00 \u20ac od 03/04/2026.")
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: /Wrong-Span Decoy Amount Blocked \(FIELD_ROLE_MISMATCH\)/i,
      })
    );
    expect(
      screen.getByDisplayValue(
        "Preostali bud\u017eet je 1.500,00 \u20ac, a danas evidentiraj tro\u0161ak taksija od 45,00 \u20ac od 03.11.2026."
      )
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "System Status" }));
    expect(screen.getByText("Runtime & Gate Status")).toBeInTheDocument();
    expect(screen.getByText("1.1.0")).toBeInTheDocument();
  });

  it("highlights exact Unicode code-point and UTF-16 offsets in source text", () => {
    const text = "Evidentiraj tro\u0161ak od 1.234,56 \u20ac od 03.11.2026.";
    const diagnostics: FieldDiagnostic[] = [
      {
        field_name: "amount",
        parse_status: "PARSED",
        reason_code: "OK_AWAITING_CONFIRMATION",
        message: "ok",
        raw_quote: "1.234,56 \u20ac",
        context_excerpt: null,
        start_offset: 22,
        end_offset: 32,
        utf16_start_offset: 22,
        utf16_end_offset: 32,
        normalization_form_matched: "NFC_EXACT",
        role_binding_status: "BOUND",
        candidate_count: 1,
        competing_candidates: [],
        canonical_value: "1234.56",
        rule_id: "hr-HR_money_grouped_decimal",
        parser_version: "1.1.0",
      },
    ];
    render(<div>{renderHighlightedText(text, diagnostics)}</div>);
    const mark = screen.getByText("1.234,56 \u20ac (amount)");
    expect(mark).toBeInTheDocument();
    expect(mark).toHaveAttribute(
      "title",
      "amount CP [22:32] | UTF-16 [22:32]"
    );
  });
});
