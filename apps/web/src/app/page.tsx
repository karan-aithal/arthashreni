"use client";

import { LoadAll } from "@/components/Load";
import { useApi } from "@/lib/api";
import type { Health, Portfolio, Review, Risk } from "@/lib/types";
import { OverviewView } from "@/views/Overview";

export default function Page() {
  return (
    <LoadAll a={useApi<Portfolio>("/portfolio")} b={useApi<Risk>("/risk")} c={useApi<Review>("/review")} d={useApi<Health>("/sources/health")}>
      {(portfolio, risk, review, health) => <OverviewView {...{ portfolio, risk, review, health }} />}
    </LoadAll>
  );
}
