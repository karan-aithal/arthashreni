// The UI is read-only: no code path may send anything but GET, except the login form.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(p) ? [p] : [];
  });
}

describe("read-only UI", () => {
  const src = files("src");
  it("sends no write request outside the login form", () => {
    for (const f of src) {
      const text = readFileSync(f, "utf8");
      if (f.endsWith(join("login", "page.tsx"))) continue;
      expect(text, f).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)/i);
    }
  });
  it("has no chat interface and no chart library (Phase 6 scope)", () => {
    const pkg = JSON.parse(readFileSync("package.json", "utf8"));
    const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
    expect(deps.filter((d) => /chart|plotly|d3|recharts|chat|tailwind/i.test(d))).toEqual([]);
  });
});
