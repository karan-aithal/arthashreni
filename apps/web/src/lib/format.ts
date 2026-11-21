// Decimal formatting on strings only: the API sends exact decimals and a float round-trip would change them.

const DEC = /^(-?)(\d+)(?:\.(\d+))?$/;

/** Round a decimal string half-up (away from zero) to `dp` places using BigInt. Returns null if not a decimal. */
export function roundDecimal(s: string, dp: number): string | null {
  const m = DEC.exec(s.trim());
  if (!m) return null;
  const [, sign, int, frac = ""] = m;
  const digits = BigInt(int + frac.padEnd(dp, "0").slice(0, Math.max(frac.length, dp)));
  const scale = frac.length;
  let n = digits;
  if (scale > dp) {
    const cut = 10n ** BigInt(scale - dp);
    n = digits / cut + (digits % cut >= cut / 2n ? 1n : 0n);
  }
  const str = n.toString().padStart(dp + 1, "0");
  const whole = dp ? str.slice(0, -dp) : str;
  const part = dp ? str.slice(-dp) : "";
  const neg = sign === "-" && n !== 0n;
  return `${neg ? "-" : ""}${whole}${dp ? "." + part : ""}`;
}

/** Indian digit grouping (12,34,567) of an integer string. */
function groupIndian(int: string): string {
  if (int.length <= 3) return int;
  const last3 = int.slice(-3);
  const rest = int.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${rest},${last3}`;
}

export function fmtInr(s: string, dp = 2): string {
  const r = roundDecimal(s, dp);
  if (r === null) return s;
  const neg = r.startsWith("-");
  const [int, frac] = r.replace("-", "").split(".");
  return `${neg ? "-" : ""}₹${groupIndian(int)}${frac ? "." + frac : ""}`;
}

export function fmtNum(s: string, dp = 2): string {
  const r = roundDecimal(s, dp);
  return r ?? s;
}

/** A ratio (0.123) as a percentage ("12.30%") by moving the decimal point, not by multiplying a float. */
export function fmtPct(s: string, dp = 2): string {
  const m = DEC.exec(s.trim());
  if (!m) return s;
  const [, sign, int, frac = ""] = m;
  const padded = frac.padEnd(2, "0");
  const shifted = `${sign}${int}${padded.slice(0, 2)}${padded.length > 2 ? "." + padded.slice(2) : ""}`;
  const r = roundDecimal(shifted, dp);
  return r === null ? s : `${r}%`;
}

export function fmtDate(iso: string): string {
  return iso.slice(0, 10);
}
