"use client";

import { Load } from "@/components/Load";
import { useApi } from "@/lib/api";
import type { Review } from "@/lib/types";
import { ReportView } from "@/views/Report";

export default function Page() {
  return <Load r={useApi<Review>("/review")}>{(r) => <ReportView review={r} />}</Load>;
}
