// Shapes of the API responses (see apps/web/fixtures, generated from the live API by tests/test_phase6_contract.py).

export type Status = "ok" | "stale" | "missing" | "conflicting" | "estimated" | "quarantined";

/** Every number from the API arrives wrapped like this (E2). `value` is a decimal string, never a float. */
export interface Measured<T = string> {
  value: T | null;
  status: Status;
  as_of: string;
  sources: string[];
  notes: string[];
}

export type M = Measured<string>;

export interface AssetInfo { id: number; name: string; asset_class: string; country: string; currency: string }
export interface Holding {
  position: { asset: AssetInfo; units: string };
  units: M; price: M; value_inr: M; cost_inr: M; unrealised_inr: M; unrealised_pct: M;
}
export interface Slice { key: string; value_inr: M; weight: M }
export interface Portfolio {
  dataset: string; as_of: string; fx_rate_type: string;
  totals: { holdings: number; value_inr: M; cost_inr: M; unrealised_inr: M; unrealised_pct: M; unvalued: string[] };
  invested_inr: M;
  returns: { measure: string; result: M; why: string }[];
  allocation: Record<string, Slice[]>;
  concentration: { top_n: number; top_n_weight: M; hhi: M };
  holdings: Holding[];
  realised: { proceeds_inr: M; cost_inr: M; gain_inr: M };
  income: Record<string, M>;
  fx_attribution: { asset: string; attribution: { local_return: M; fx_return: M; interaction: M; inr_return: M } }[];
  stats: { asset: AssetInfo; volatility: M; drawdown: { depth: M; current_depth: M; recovery_days: number | null } }[];
  look_through: { exposure: Record<string, M>; without_data: string[] };
}

export interface Alert {
  key: string; level: string; what: string; when: string; classification: string; evidence: string[];
  responses: string[]; if_nothing: string; watch: string; assets: string[];
}
export interface Scenario { id: string; label: string; description: string; note: string; total_inr: M; total_pct: M; per_holding: [string, M][] }
export interface Risk {
  dataset: string; day: string;
  level: { level: string; available: number; total: number; drivers: string[]; missing: string[] };
  confidence: { level: string; coverage: string; freshness: string; agreement: string; reasons: string[] };
  signals: { id: string; kind: string; scope: string; subject: string; level: string | null; value: M }[];
  scenarios: Scenario[];
  alerts: Alert[];
}

export interface Line {
  subject: string; action: string; amount_inr: string; reasons: string[]; risks: string[]; evidence_ids: string[];
  invalidation_conditions: string[]; assumptions: string[]; data_freshness: string; confidence: string;
}
export interface Recommendation {
  period: string; review_date: string; dataset: string; label: string; personalised: boolean; risk_level: string;
  overall_action: string; budget_inr: string; confidence: string; confidence_reasons: string[];
  envelope: { opening_budget_inr: string; committed_sip_inr: string; new_allocations_inr: string; reserve_transfers_inr: string; unallocated_inr: string };
  lines: Line[]; warnings: string[]; missing: string[]; assumptions: string[]; sources: string[];
  scenarios: { id: string; label: string; note: string; status: string; total_inr: string; total_pct: string }[];
  human_approval_required: boolean;
}
export interface Review { dataset: string; stored: boolean; recommendation: Recommendation }

export interface Txn {
  id: number; trade_date: string; type: string; asset: string | null; account: string; currency: string;
  quantity: M; price: M; fees: M; net_cash_inr: M; fx_rate: M; fx_rate_type: string | null; corrects_id: number | null;
}
export interface Transactions { dataset: string; total: number; limit: number; offset: number; items: Txn[] }
export interface ImportBatch {
  id: number; source_kind: string; file_name: string; status: string; received_at: string;
  rows: { new: number; duplicate: number; ambiguous: number; rejected: number };
}
export interface Imports { dataset: string; items: ImportBatch[] }

export interface Source {
  id: string; name: string; state: string; enabled: boolean; consented: boolean; freshness: string | null;
  freshness_note: string | null; latest_valid_at: string | null; last_success_at: string | null;
  last_failure_at: string | null; calls_today: number; daily_quota: number | null; pending_quarantine: number;
}
export interface Health {
  dataset: string; sources: Source[]; pending_quarantine: number; ledger_differences: number; blocks_monthly_review: boolean;
}
export type Quarantine = Record<string, unknown>[];
