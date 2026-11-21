"use client";

import { Load } from "@/components/Load";
import { useApi } from "@/lib/api";
import type { Portfolio } from "@/lib/types";
import { PortfolioView } from "@/views/Portfolio";

export default function Page() {
  return <Load r={useApi<Portfolio>("/portfolio")}>{(p) => <PortfolioView p={p} />}</Load>;
}
