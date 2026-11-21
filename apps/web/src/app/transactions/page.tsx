"use client";

import { LoadAll } from "@/components/Load";
import { useApi } from "@/lib/api";
import type { Imports, Transactions } from "@/lib/types";
import { TransactionsView } from "@/views/Transactions";

export default function Page() {
  return (
    <LoadAll a={useApi<Transactions>("/transactions")} b={useApi<Imports>("/imports")}>
      {(t, imports) => <TransactionsView t={t} imports={imports} />}
    </LoadAll>
  );
}
