"use client";

import { Load } from "@/components/Load";
import { useApi } from "@/lib/api";
import type { Risk } from "@/lib/types";
import { RiskView } from "@/views/Risk";

export default function Page() {
  return <Load r={useApi<Risk>("/risk")}>{(r) => <RiskView r={r} />}</Load>;
}
