import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": r("./src"), "@fixtures": r("./fixtures") } },
  test: { environment: "jsdom", setupFiles: ["./tests/setup.ts"], css: false },
});
