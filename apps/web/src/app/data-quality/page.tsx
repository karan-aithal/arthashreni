"use client";

import { LoadAll } from "@/components/Load";
import { useApi } from "@/lib/api";
import type { Health, Quarantine } from "@/lib/types";
import { DataQualityView } from "@/views/DataQuality";

export default function Page() {
  return (
    <LoadAll a={useApi<Health>("/sources/health")} b={useApi<Quarantine>("/quarantine")}>
      {(health, quarantine) => <DataQualityView health={health} quarantine={quarantine} />}
    </LoadAll>
  );
}
