"""Ledger, imports, lots, reconciliation (Phase 2).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11

Dataset tables are created identically in `demo` and `real` (ADR-0004).
Append-only tables reject UPDATE/DELETE/TRUNCATE by trigger and give the app
role SELECT and INSERT only (ADR-0005). lots / lot_consumptions are derived
caches (ADR-0006) and may be rebuilt, so the app role may also DELETE them.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TXN_TYPES = (
    "BUY SELL SIP DIVIDEND DIVIDEND_REINVESTMENT FEE TAX SPLIT BONUS MERGER REDEMPTION "
    "SWITCH_IN SWITCH_OUT TRANSFER_IN TRANSFER_OUT DEPOSIT WITHDRAWAL FX_CONVERSION "
    "IPO_APPLICATION IPO_BLOCK IPO_ALLOTMENT IPO_REFUND CORPORATE_ACTION CORRECTION INTEREST"
).split()
ASSET_CLASSES = "EQUITY_IN EQUITY_US MF ETF_IN ETF_US GOLD_ETF SILVER_ETF SGB DIGITAL_GOLD CASH".split()
PLATFORMS = "INDMONEY ZERODHA_COIN ZERODHA_KITE CAMS KFINTECH OTHER".split()
ACCOUNT_KINDS = "BROKER MF_FOLIO DEMAT BANK RESERVE".split()
SOURCE_KINDS = (
    "CAS_CAMS CAS_KFIN CAS_NSDL CAS_CDSL ZERODHA_CONSOLE INDMONEY CSV_TEMPLATE "
    "CSV_CORPORATE_ACTIONS CSV_HOLDINGS MANUAL"
).split()
APP_ROLE = {"demo": "artha_demo", "real": "artha_real"}


def q(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


APPEND_ONLY = [
    "transaction_imports_raw",
    "transactions",
    "corporate_actions",
    "statement_holdings",
]


def dataset_sql(s: str) -> list[str]:
    non_correction = [t for t in TXN_TYPES if t != "CORRECTION"]
    stmts = [
        f"""CREATE TABLE {s}.accounts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            platform text NOT NULL CHECK (platform IN ({q(PLATFORMS)})),
            account_kind text NOT NULL CHECK (account_kind IN ({q(ACCOUNT_KINDS)})),
            label text NOT NULL UNIQUE,
            folio_or_ref_masked text,
            currency text NOT NULL DEFAULT 'INR' CHECK (length(currency) = 3),
            opened_on date,
            closed_on date
        )""",
        f"""CREATE TABLE {s}.import_batches (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source_kind text NOT NULL CHECK (source_kind IN ({q(SOURCE_KINDS)})),
            file_name text NOT NULL,
            file_sha256 text NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            status text NOT NULL CHECK (status IN ('PREVIEW', 'COMMITTED', 'REJECTED')),
            approved_at timestamptz,
            statement_period_start date,
            statement_period_end date,
            summary jsonb NOT NULL DEFAULT '{{}}'
        )""",
        f"""CREATE TABLE {s}.transaction_imports_raw (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            batch_id bigint NOT NULL REFERENCES {s}.import_batches(id),
            row_no integer NOT NULL,
            item_kind text NOT NULL CHECK (item_kind IN ('TRANSACTION', 'CORPORATE_ACTION', 'STATEMENT_HOLDING')),
            raw jsonb NOT NULL,
            candidate jsonb,
            fingerprint text,
            parse_status text NOT NULL CHECK (parse_status IN ('NEW', 'DUPLICATE', 'AMBIGUOUS', 'REJECTED')),
            reject_reason text,
            UNIQUE (batch_id, row_no)
        )""",
        f"""CREATE TABLE {s}.transactions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            asset_id bigint REFERENCES ref.assets(id),
            account_id bigint NOT NULL REFERENCES {s}.accounts(id),
            txn_type text NOT NULL CHECK (txn_type IN ({q(TXN_TYPES)})),
            corrected_type text CHECK (corrected_type IN ({q(non_correction)})),
            trade_date date NOT NULL,
            settlement_date date,
            price_date date,
            quantity numeric(24,8) CHECK (quantity >= 0),
            price numeric(24,8) CHECK (price >= 0),
            currency text NOT NULL CHECK (length(currency) = 3),
            fx_rate numeric(24,8) CHECK (fx_rate > 0),
            fx_rate_type text CHECK (fx_rate_type IN ('SBI_TT_BUY', 'FBIL_REF', 'BROKER_ACTUAL')),
            fees numeric(20,4) NOT NULL DEFAULT 0 CHECK (fees >= 0),
            taxes numeric(20,4) NOT NULL DEFAULT 0 CHECK (taxes >= 0),
            stamp_duty numeric(20,4) NOT NULL DEFAULT 0 CHECK (stamp_duty >= 0),
            net_cash_inr numeric(20,4) NOT NULL,
            net_cash_orig numeric(20,4) NOT NULL,
            source_document text NOT NULL,
            import_batch_id bigint REFERENCES {s}.import_batches(id),
            raw_record_id bigint REFERENCES {s}.transaction_imports_raw(id),
            verification_status text NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (verification_status IN ('UNVERIFIED', 'STATEMENT_MATCHED', 'MANUAL_CONFIRMED')),
            supersedes_id bigint REFERENCES {s}.transactions(id),
            correction_reason text,
            fingerprint text NOT NULL UNIQUE,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            source_tz text NOT NULL DEFAULT 'Asia/Kolkata',
            CONSTRAINT fx_type_with_rate CHECK (fx_rate IS NULL OR fx_rate_type IS NOT NULL),
            CONSTRAINT correction_shape CHECK (
                (txn_type = 'CORRECTION' AND supersedes_id IS NOT NULL
                    AND correction_reason IS NOT NULL AND corrected_type IS NOT NULL)
                OR (txn_type <> 'CORRECTION' AND supersedes_id IS NULL
                    AND correction_reason IS NULL AND corrected_type IS NULL)
            ),
            CONSTRAINT supersedes_earlier CHECK (supersedes_id IS NULL OR supersedes_id < id)
        )""",
        # A rejected batch frees its file to be imported again (e.g. after adding a missing asset).
        f"CREATE UNIQUE INDEX import_batches_file_once ON {s}.import_batches (file_sha256) "
        "WHERE status <> 'REJECTED'",
        f"CREATE UNIQUE INDEX transactions_one_correction ON {s}.transactions (supersedes_id) "
        "WHERE supersedes_id IS NOT NULL",
        f"CREATE INDEX transactions_asset_account ON {s}.transactions (account_id, asset_id, trade_date)",
        # Effective view: latest non-superseded version; corrections carry the corrected type;
        # a correction with zero quantity and zero cash is a void and drops out (docs/data-model.md).
        f"""CREATE VIEW {s}.transactions_effective AS
            SELECT t.id, t.asset_id, t.account_id,
                   COALESCE(t.corrected_type, t.txn_type) AS effective_type,
                   t.trade_date, t.settlement_date, t.price_date, t.quantity, t.price, t.currency,
                   t.fx_rate, t.fx_rate_type, t.fees, t.taxes, t.stamp_duty,
                   t.net_cash_inr, t.net_cash_orig, t.source_document, t.import_batch_id,
                   t.verification_status, t.supersedes_id, t.recorded_at
            FROM {s}.transactions t
            WHERE NOT EXISTS (SELECT 1 FROM {s}.transactions c WHERE c.supersedes_id = t.id)
              AND NOT (t.supersedes_id IS NOT NULL AND COALESCE(t.quantity, 0) = 0
                       AND t.net_cash_orig = 0 AND t.net_cash_inr = 0)""",
        f"""CREATE TABLE {s}.corporate_actions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            asset_id bigint NOT NULL REFERENCES ref.assets(id),
            action_type text NOT NULL CHECK (action_type IN ('SPLIT', 'BONUS', 'MERGER', 'NAME_CHANGE')),
            ex_date date NOT NULL,
            ratio_from numeric(24,8) NOT NULL CHECK (ratio_from > 0),
            ratio_to numeric(24,8) NOT NULL CHECK (ratio_to > 0),
            successor_asset_id bigint REFERENCES ref.assets(id),
            source_document text NOT NULL,
            import_batch_id bigint REFERENCES {s}.import_batches(id),
            fingerprint text NOT NULL UNIQUE,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT merger_has_successor CHECK (
                (action_type = 'MERGER') = (successor_asset_id IS NOT NULL))
        )""",
        f"""CREATE TABLE {s}.statement_holdings (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            import_batch_id bigint NOT NULL REFERENCES {s}.import_batches(id),
            account_id bigint NOT NULL REFERENCES {s}.accounts(id),
            asset_id bigint NOT NULL REFERENCES ref.assets(id),
            as_of date NOT NULL,
            units numeric(24,8) NOT NULL CHECK (units >= 0),
            source_document text NOT NULL,
            fingerprint text NOT NULL UNIQUE,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )""",
        # Derived caches: rebuilt from the effective ledger + corporate actions.
        f"""CREATE TABLE {s}.lots (
            id bigint PRIMARY KEY,
            transaction_id bigint REFERENCES {s}.transactions(id),
            corporate_action_id bigint REFERENCES {s}.corporate_actions(id),
            adjusted_by_ca_id bigint REFERENCES {s}.corporate_actions(id),
            asset_id bigint NOT NULL REFERENCES ref.assets(id),
            account_id bigint NOT NULL REFERENCES {s}.accounts(id),
            acquired_on date NOT NULL,
            original_qty numeric(24,8) NOT NULL,
            remaining_qty numeric(24,8) NOT NULL CHECK (remaining_qty >= 0),
            cost_inr numeric(20,4) NOT NULL,
            cost_orig numeric(20,4) NOT NULL,
            remaining_cost_inr numeric(20,4) NOT NULL CHECK (remaining_cost_inr >= 0),
            fx_rate_type text,
            is_bonus boolean NOT NULL DEFAULT false,
            CONSTRAINT lot_has_one_origin CHECK ((transaction_id IS NULL) <> (corporate_action_id IS NULL))
        )""",
        f"""CREATE TABLE {s}.lot_consumptions (
            id bigint PRIMARY KEY,
            lot_id bigint NOT NULL REFERENCES {s}.lots(id) ON DELETE CASCADE,
            disposal_transaction_id bigint NOT NULL REFERENCES {s}.transactions(id),
            qty numeric(24,8) NOT NULL CHECK (qty > 0),
            cost_inr_consumed numeric(20,4) NOT NULL,
            proceeds_inr numeric(20,4) NOT NULL,
            holding_days integer NOT NULL CHECK (holding_days >= 0)
        )""",
        f"""CREATE TABLE {s}.reconciliations (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            statement_holding_id bigint NOT NULL UNIQUE REFERENCES {s}.statement_holdings(id),
            run_at timestamptz NOT NULL DEFAULT now(),
            ledger_units numeric(24,8) NOT NULL,
            statement_units numeric(24,8) NOT NULL,
            diff numeric(24,8) NOT NULL,
            status text NOT NULL CHECK (status IN ('MATCHED', 'UNEXPLAINED', 'ACCEPTED')),
            accepted_reason text,
            accepted_diff numeric(24,8),
            CONSTRAINT accepted_has_reason CHECK (status <> 'ACCEPTED' OR accepted_reason IS NOT NULL)
        )""",
    ]
    for t in APPEND_ONLY:
        stmts.append(
            f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {s}.{t} "
            "FOR EACH ROW EXECUTE FUNCTION ref.reject_mutation()"
        )
        stmts.append(
            f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {s}.{t} "
            "FOR EACH STATEMENT EXECUTE FUNCTION ref.reject_mutation()"
        )
    r = APP_ROLE[s]
    stmts += [
        f"GRANT SELECT, INSERT ON {s}.accounts, {s}.transaction_imports_raw, {s}.transactions, "
        f"{s}.corporate_actions, {s}.statement_holdings TO {r}",
        f"GRANT SELECT, INSERT ON {s}.import_batches TO {r}",
        f"GRANT UPDATE (status, approved_at, summary) ON {s}.import_batches TO {r}",
        f"GRANT SELECT, INSERT, DELETE ON {s}.lots, {s}.lot_consumptions TO {r}",
        f"GRANT SELECT, INSERT ON {s}.reconciliations TO {r}",
        f"GRANT UPDATE (run_at, ledger_units, statement_units, diff, status, accepted_reason, "
        f"accepted_diff) ON {s}.reconciliations TO {r}",
        f"GRANT SELECT ON {s}.transactions_effective TO {r}",
    ]
    return stmts


def upgrade() -> None:
    op.execute(
        """CREATE FUNCTION ref.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'table %.% is append-only (ADR-0005)', TG_TABLE_SCHEMA, TG_TABLE_NAME;
        END $$"""
    )
    op.execute(
        f"""CREATE TABLE ref.assets (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            asset_class text NOT NULL CHECK (asset_class IN ({q(ASSET_CLASSES)})),
            name text NOT NULL,
            isin text UNIQUE,
            amfi_code text UNIQUE,
            ticker text,
            exchange_mic text CHECK (exchange_mic IN ('XNYS', 'XNAS', 'XBOM', 'XNSE')),
            currency text NOT NULL CHECK (length(currency) = 3),
            country text NOT NULL CHECK (length(country) = 2),
            mf_plan text CHECK (mf_plan IN ('DIRECT', 'REGULAR')),
            mf_option text CHECK (mf_option IN ('GROWTH', 'IDCW')),
            fund_house text,
            sector text,
            market_cap_bucket text,
            is_sme boolean NOT NULL DEFAULT false,
            regulated boolean NOT NULL DEFAULT true,
            maturity_date date,
            coupon_rate numeric(12,8),
            coupon_frequency_months integer,
            is_demo boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT has_identifier CHECK (isin IS NOT NULL OR amfi_code IS NOT NULL OR ticker IS NOT NULL),
            CONSTRAINT ticker_needs_mic CHECK (ticker IS NULL OR exchange_mic IS NOT NULL)
        )"""
    )
    op.execute(
        "CREATE UNIQUE INDEX assets_ticker_mic ON ref.assets (ticker, exchange_mic) WHERE ticker IS NOT NULL"
    )
    op.execute("GRANT SELECT, INSERT ON ref.assets TO artha_demo, artha_real")
    for s in ("demo", "real"):
        for stmt in dataset_sql(s):
            op.execute(stmt)


def downgrade() -> None:
    for s in ("real", "demo"):
        for t in APPEND_ONLY:
            op.execute(f"DROP TRIGGER {t}_append_only ON {s}.{t}")
            op.execute(f"DROP TRIGGER {t}_no_truncate ON {s}.{t}")
        for obj in (
            "reconciliations",
            "lot_consumptions",
            "lots",
            "statement_holdings",
            "corporate_actions",
        ):
            op.execute(f"DROP TABLE {s}.{obj}")
        op.execute(f"DROP VIEW {s}.transactions_effective")
        for obj in ("transactions", "transaction_imports_raw", "import_batches", "accounts"):
            op.execute(f"DROP TABLE {s}.{obj}")
    op.execute("DROP TABLE ref.assets")
    op.execute("DROP FUNCTION ref.reject_mutation()")
