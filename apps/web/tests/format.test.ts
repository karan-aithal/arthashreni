import { describe, expect, it } from "vitest";
import { fmtInr, fmtPct, roundDecimal } from "@/lib/format";

describe("decimal formatting works on strings, never floats", () => {
  it("rounds half away from zero", () => {
    expect(roundDecimal("2.345", 2)).toBe("2.35");
    expect(roundDecimal("-2.345", 2)).toBe("-2.35");
    expect(roundDecimal("0.999", 2)).toBe("1.00");
    expect(roundDecimal("-0.001", 2)).toBe("0.00");
    expect(roundDecimal("5", 2)).toBe("5.00");
    expect(roundDecimal("12", 0)).toBe("12");
    expect(roundDecimal("abc", 2)).toBeNull();
  });
  it("keeps precision a float would lose", () => {
    expect(roundDecimal("12345678901234567890.125", 2)).toBe("12345678901234567890.13");
    expect(roundDecimal("0.1000000000000000055511151231257827", 20)).toBe("0.10000000000000000555");
  });
  it("groups INR the Indian way", () => {
    expect(fmtInr("1234567.5")).toBe("₹12,34,567.50");
    expect(fmtInr("-999")).toBe("-₹999.00");
    expect(fmtInr("100000", 0)).toBe("₹1,00,000");
  });
  it("shows ratios as percentages by moving the point", () => {
    expect(fmtPct("0.1234")).toBe("12.34%");
    expect(fmtPct("-0.0004")).toBe("-0.04%");
    expect(fmtPct("1")).toBe("100.00%");
    expect(fmtPct("0.714152820630054454")).toBe("71.42%");
  });
});
