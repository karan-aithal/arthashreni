import type { NextConfig } from "next";

// The browser talks only to this origin; /api/* is proxied to the local FastAPI (same-origin keeps the
// SameSite=Strict session cookie working, ADR-0012). Localhost only.
const backend = process.env.ARTHA_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

export default config;
