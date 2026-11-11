import csv
from datetime import date
from pathlib import Path

import pytest
from casparser.types import CASData, NSDLCASData
from openpyxl import Workbook

from arthashreni.ingestion import cas, console, csv_template, detect, indmoney, tables
from arthashreni.ingestion.common import ImportRowError, parse_date, parse_decimal
from arthashreni.ledger.models import NewCorporateAction, NewStatementHolding, NewTxn
from arthashreni.ledger.types import TxnType
from tests.helpers import D, FakeResolver


@pytest.fixture
def res() -> FakeResolver:
    r = FakeResolver()
    r.add_account("ACC")
    r.add_asset("INE000000001")
    r.add_asset("TICKER:XNAS:UST", "EQUITY_US", "USD")
    r.add_asset("AMFI:111", "MF")
    r.add_asset("ZZMF00000001", "MF")
    return r


FOLIO = cas.folio_label("1234567/89")
DEMAT = cas.demat_label("IN300", "123456")


def write_csv(path: Path, header: str, *rows: str) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


# ---- value parsing and tables ---------------------------------------------------------------------


def test_parse_decimal_and_date() -> None:
    assert parse_decimal("1,234.50") == D("1234.50")
    assert parse_decimal("₹ 10") == D(10)
    assert parse_decimal("(5.5)") == D("-5.5")
    assert parse_decimal("") is None and parse_decimal("--") is None
    with pytest.raises(ImportRowError, match="required"):
        parse_decimal("", required=True, name="qty")
    with pytest.raises(ImportRowError, match="not a number"):
        parse_decimal("abc")
    assert (
        parse_date("2025-01-31") == parse_date("31-01-2025") == parse_date("31-Jan-2025") == date(2025, 1, 31)
    )
    assert parse_date(date(2025, 1, 1)) == date(2025, 1, 1)
    with pytest.raises(ImportRowError, match="unreadable date"):
        parse_date("soon")


def test_read_table_csv_skips_blank_lines_and_normalises_headers(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("﻿Trade Date, ISIN ,Qty.\n2025-01-01,X,1\n,,\n\n2025-01-02,Y,2\n")
    rows = tables.read_table(p, {"trade_date", "isin"})
    assert rows == [
        {"trade_date": "2025-01-01", "isin": "X", "qty": "1"},
        {"trade_date": "2025-01-02", "isin": "Y", "qty": "2"},
    ]
    with pytest.raises(ValueError, match="no header row"):
        tables.read_table(p, {"nonexistent"})


def test_read_table_xlsx_finds_header_below_title_rows(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Zerodha Console"])
    ws.append([])
    ws.append(["trade_date", "isin", "trade_type", "quantity", "price", "trade_id"])
    ws.append([date(2025, 1, 2), "INE000000001", "buy", 10, 12.5, "t1"])
    p = tmp_path / "t.xlsx"
    wb.save(p)
    assert detect.detect_format(p) == detect.ZERODHA_TRADEBOOK
    rows = tables.read_table(p, console.TRADEBOOK_REQUIRED)
    assert rows[0]["trade_date"] == "2025-01-02" and rows[0]["price"] == "12.5"


def test_tables_reject_other_types_and_big_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "x.txt"
    p.write_text("a")
    with pytest.raises(ValueError, match="unsupported"):
        tables.peek_headers(p)
    assert detect.detect_format(p) is None
    big = write_csv(tmp_path / "b.csv", "a,b", "1,2")
    monkeypatch.setattr(tables, "MAX_FILE_BYTES", 3)
    with pytest.raises(ValueError, match="larger"):
        tables.read_table(big, {"a"})


def test_detect_format(tmp_path: Path) -> None:
    assert detect.detect_format(tmp_path / "x.PDF") == detect.CAS_PDF
    cases = {
        detect.TRANSACTIONS: csv_template.TXN_COLUMNS,
        detect.CORPORATE_ACTIONS: csv_template.ACTION_COLUMNS,
        detect.HOLDINGS: csv_template.HOLDING_COLUMNS,
        detect.INDMONEY: indmoney.SAMPLE_HEADER,
        detect.ZERODHA_TRADEBOOK: "symbol,isin,trade_date,trade_type,quantity,price,trade_id",
        detect.ZERODHA_HOLDINGS: "symbol,isin,quantity_available,average_price",
    }
    for kind, header in cases.items():
        assert detect.detect_format(write_csv(tmp_path / f"{kind}.csv", header)) == kind
    assert detect.detect_format(write_csv(tmp_path / "unknown.csv", "foo,bar")) is None


# ---- CSV templates ---------------------------------------------------------------------------------


def test_template_transactions_derive_cash_and_reject_bad_rows(tmp_path: Path, res: FakeResolver) -> None:
    p = write_csv(
        tmp_path / "t.csv",
        csv_template.TXN_COLUMNS,
        "ACC,INE000000001,BUY,2025-01-02,,10,100,INR,,,5,0,0,,,x1,",
        "ACC,INE000000001,SELL,2025-01-03,,4,110,INR,,,0,0,0,,,x2,",
        "ACC,INE000000001,DIVIDEND_REINVESTMENT,2025-01-04,,1,100,INR,,,0,0,0,,,x3,",
        "ACC,,DEPOSIT,2025-01-05,,,,INR,,,0,0,0,,1000,x4,",
        "ACC,,DIVIDEND,2025-01-05,,,,INR,,,0,0,0,,,x5,",  # cash missing
        "ACC,INE000000001,BUY,2025-01-02,,10,,INR,,,0,0,0,,,x6,",  # price missing
        "NOPE,INE000000001,BUY,2025-01-02,,1,1,INR,,,0,0,0,,,x7,",  # unknown account
        "ACC,INE999999999,BUY,2025-01-02,,1,1,INR,,,0,0,0,,,x8,",  # unknown asset
        "ACC,INE000000001,SPLIT,2025-01-02,,1,1,INR,,,0,0,0,,0,x9,",  # reserved type
        "ACC,INE000000001,JUMP,2025-01-02,,1,1,INR,,,0,0,0,,0,x10,",
        "ACC,,DEPOSIT,2025-01-02,,,,USD,80,,0,0,0,,10,x11,",  # fx rate without type
        "ACC,,DEPOSIT,2025-01-02,,,,USD,,,0,0,0,,10,x12,",  # USD without fx
        "ACC,INE000000001,BUY,2025-01-02,,abc,1,INR,,,0,0,0,,,x13,",
        "ACC,TICKER:XNAS:UST,BUY,2025-01-02,,1,1,INR,,,0,0,0,,,x14,",  # currency differs from asset
        "ACC,INE000000001,BUY,2025-01-02,,1,1,INR,,,0,0,0,,100,x15,",  # wrong cash sign
    )
    out = csv_template.parse_transactions(p, res)
    by = {r.raw["external_id"]: r for r in out.rows}
    buy = by["x1"].candidate
    assert (
        isinstance(buy, NewTxn)
        and buy.net_cash_orig == D("-1005.0000")
        and buy.net_cash_inr == D("-1005.0000")
    )
    sell = by["x2"].candidate
    assert isinstance(sell, NewTxn) and sell.net_cash_orig == D("440.0000")
    reinvest = by["x3"].candidate
    assert isinstance(reinvest, NewTxn) and reinvest.net_cash_orig == 0
    assert by["x4"].status == "NEW"
    reasons = {k: (r.reason or "") for k, r in by.items() if r.status == "REJECTED"}
    assert "net_cash_orig is required" in reasons["x5"]
    assert "needs quantity and price" in reasons["x6"]
    assert "unknown account" in reasons["x7"]
    assert "no asset registered" in reasons["x8"]
    assert "cannot be imported" in reasons["x9"]
    assert "unknown transaction type" in reasons["x10"]
    assert "fx_rate" in reasons["x11"] and "fx_rate" in reasons["x12"]
    assert "not a number" in reasons["x13"]
    assert "does not match" in reasons["x14"]
    assert "must be negative" in reasons["x15"]
    assert all(r.key is None for r in out.rows if r.status == "REJECTED")


def test_template_usd_row_uses_fx_for_inr_cash(tmp_path: Path, res: FakeResolver) -> None:
    p = write_csv(
        tmp_path / "t.csv", csv_template.TXN_COLUMNS,
        "ACC,TICKER:XNAS:UST,BUY,2025-01-02,2025-01-03,2,100,USD,83,BROKER_ACTUAL,1,0,0,,,u1,",
    )  # fmt: skip
    row = csv_template.parse_transactions(p, res).rows[0]
    assert isinstance(row.candidate, NewTxn)
    assert (row.candidate.net_cash_orig, row.candidate.net_cash_inr) == (D("-201.0000"), D("-16683.0000"))
    assert row.candidate.settlement_date == date(2025, 1, 3)


def test_template_corporate_actions_and_holdings(tmp_path: Path, res: FakeResolver) -> None:
    res.add_asset("INE000000002")
    p = write_csv(
        tmp_path / "ca.csv",
        csv_template.ACTION_COLUMNS,
        "INE000000001,SPLIT,2025-03-01,1,5,",
        "INE000000001,MERGER,2025-03-01,1,2,INE000000002",
        "INE000000001,MERGER,2025-03-01,1,2,",  # missing successor
        "INE000000001,SPLIT,2025-03-01,0,5,",
        "INE000000001,SPLIT,2025-03-01,1,5,INE000000002",  # successor on a split
        "INE000000001,SHRINK,2025-03-01,1,5,",
    )
    rows = csv_template.parse_corporate_actions(p, res).rows
    assert [r.status for r in rows] == ["NEW", "NEW", "REJECTED", "REJECTED", "REJECTED", "REJECTED"]
    assert (
        isinstance(rows[1].candidate, NewCorporateAction) and rows[1].candidate.successor_asset_id is not None
    )

    h = write_csv(
        tmp_path / "h.csv", csv_template.HOLDING_COLUMNS,
        "ACC,INE000000001,2025-03-31,12.5", "ACC,INE000000001,2025-03-31,-1", "ACC,INE000000001,2025-03-31,x",
    )  # fmt: skip
    hrows = csv_template.parse_holdings(h, res).rows
    assert [r.status for r in hrows] == ["NEW", "REJECTED", "REJECTED"]
    assert isinstance(hrows[0].candidate, NewStatementHolding)


# ---- Zerodha Console ---------------------------------------------------------------------------------


def test_console_tradebook(tmp_path: Path, res: FakeResolver) -> None:
    p = write_csv(
        tmp_path / "tb.csv",
        "symbol,isin,trade_date,exchange,segment,series,trade_type,quantity,price,trade_id,order_id",
        "ABC,INE000000001,2025-02-03,NSE,EQ,EQ,buy,5,200.5,t1,o1",
        "ABC,INE000000001,2025-02-04,NSE,EQ,EQ,SELL,2,210,t2,o2",
        "ABC,INE000000001,2025-02-04,NSE,FO,EQ,buy,2,210,t3,o3",
        "ABC,INE000000001,2025-02-04,NSE,EQ,EQ,short,2,210,t4,o4",
    )
    out = console.parse_tradebook(p, res, "ACC")
    assert [r.status for r in out.rows] == ["NEW", "NEW", "REJECTED", "REJECTED"]
    first = out.rows[0].candidate
    assert isinstance(first, NewTxn) and first.type is TxnType.BUY and first.net_cash_orig == D("-1002.5000")
    assert "not supported" in (out.rows[2].reason or "") and "unknown trade_type" in (
        out.rows[3].reason or ""
    )
    assert any("no brokerage" in w for w in out.warnings)


def test_console_holdings(tmp_path: Path, res: FakeResolver) -> None:
    p = write_csv(
        tmp_path / "h.csv", "symbol,isin,quantity_available,average_price",
        "ABC,INE000000001,7,100", "XYZ,INE000000009,1,5",
    )  # fmt: skip
    out = console.parse_holdings(p, res, "ACC", date(2025, 3, 31))
    assert [r.status for r in out.rows] == ["NEW", "REJECTED"]
    assert out.period_end == date(2025, 3, 31)


# ---- INDmoney (assumed format) -----------------------------------------------------------------------


def test_indmoney_assumed_format(tmp_path: Path, res: FakeResolver) -> None:
    p = write_csv(
        tmp_path / "ind.csv",
        indmoney.SAMPLE_HEADER,
        "2025-03-05,UST,NASDAQ,BUY,2,100,0.5,0,,83.2,r1",
        "2025-03-06,UST,NASDAQ,SELL,1,110,0.2,0,,84,r2",
        "2025-06-20,UST,NASDAQ,DIVIDEND,,,0,0.5,3.0,85,r3",
        "2025-06-21,,,FEE,,,,,1.25,85,r4",
        "2025-03-04,,,FX_CONVERSION,,,,,-500,83.1,r5",
        "2025-03-07,UST,LSE,BUY,1,1,0,0,,83,r6",
        "2025-03-07,UST,NASDAQ,SWAP,1,1,0,0,,83,r7",
        "2025-03-07,UST,NASDAQ,BUY,1,1,0,0,,,r8",
    )
    out = indmoney.parse_statement(p, res, "ACC")
    c = [r.candidate for r in out.rows]
    assert (
        isinstance(c[0], NewTxn)
        and c[0].net_cash_orig == D("-200.5000")
        and c[0].fx_rate_type == "BROKER_ACTUAL"
    )
    assert isinstance(c[2], NewTxn) and c[2].net_cash_orig == D("3.0") and c[2].taxes == D("0.5")
    assert isinstance(c[3], NewTxn) and c[3].net_cash_orig == D("-1.25")  # sign from the transaction type
    assert isinstance(c[4], NewTxn) and c[4].net_cash_orig == D(-500)  # FX conversion keeps the given sign
    assert "unknown exchange" in (out.rows[5].reason or "") and "unknown txn_type" in (
        out.rows[6].reason or ""
    )
    assert out.rows[7].status == "REJECTED"
    assert any("ASSUMED" in w for w in out.warnings)


# ---- CAS (casparser models) ---------------------------------------------------------------------------


def scheme(
    txns: list[dict], close: str = "20.000", calc: str | None = None, isin: str | None = "ZZMF00000001"
) -> dict:
    return {
        "scheme": "Demo Fund", "rta_code": "R1", "rta": "CAMS", "isin": isin, "amfi": "111", "open": "0",
        "close": close, "close_calculated": calc or close,
        "valuation": {"date": "2025-03-31", "nav": "50", "value": "1000"}, "transactions": txns,
    }  # fmt: skip


def cas_data(schemes: list[dict], **extra: object) -> CASData:
    return CASData.model_validate(
        {
            "statement_period": {"from": "01-Jan-2025", "to": "31-Mar-2025"},
            "folios": [{"folio": "1234567/89", "amc": "Demo AMC", "schemes": schemes}],
            "investor_info": {"name": "n", "email": "e", "address": "a", "mobile": "m"},
            "cas_type": "DETAILED", "file_type": "CAMS", **extra,
        }
    )  # fmt: skip


def txn(
    date_: str, typ: str, amount: str | None, units: str | None, nav: str | None, desc: str = "d"
) -> dict:
    return {
        "date": date_,
        "description": desc,
        "amount": amount,
        "units": units,
        "nav": nav,
        "balance": "0",
        "type": typ,
    }


def test_cas_label_masks_identifiers() -> None:
    assert cas.mask_id("1234567/89") == "*****6789"
    assert cas.folio_label("1234567/89") == "FOLIO-*****6789"
    assert cas.demat_label("IN300", "123456") == "DEMAT-*******3456"
    assert cas.demat_label(None, None) == "DEMAT-UNKNOWN"
    assert cas.mask_id("12") == "12"


def test_cas_detailed_maps_transactions_and_checks_units(res: FakeResolver) -> None:
    res.add_account(FOLIO)
    data = cas_data(
        [
            scheme(
                [
                    txn("2025-01-06", "PURCHASE_SIP", "500.00", "10.000", "50.0000"),
                    txn("2025-01-06", "STAMP_DUTY_TAX", "0.03", None, None),  # folded into the purchase
                    txn("2025-02-06", "PURCHASE", "1000.00", "20.000", "50.0000"),
                    txn("2025-03-06", "REDEMPTION", "-600.00", "-12.000", "50.0000"),
                    txn("2025-03-07", "DIVIDEND_REINVEST", "5.00", "0.100", "50.0000"),
                    txn("2025-03-08", "DIVIDEND_PAYOUT", "7.00", None, None),
                    txn("2025-03-09", "STT_TAX", "0.60", None, None),
                    txn("2025-03-10", "STAMP_DUTY_TAX", "0.05", None, None),  # no purchase that day
                    txn("2025-03-11", "SWITCH_OUT", "-100.00", "-2.000", "50.0000"),
                    txn("2025-03-12", "SWITCH_IN", "100.00", "2.000", "50.0000"),
                    txn("2025-03-13", "MISC", "1.00", None, None),
                    txn(
                        "2025-03-14", "PURCHASE", "1000.00", "25.000", "50.0000"
                    ),  # units do not match the amount
                ],
                close="20.000",
            )
        ]
    )
    out = cas.parse_cas(data, res, "cas.pdf")
    assert out.source_kind == "CAS_CAMS" and out.period_end == date(2025, 3, 31)
    rows = {r.raw.get("date", "") + "|" + r.raw.get("type", ""): r for r in out.rows if r.raw.get("type")}
    sip = (
        rows["2025-01-06|TransactionType.PURCHASE_SIP"].candidate
        if "2025-01-06|TransactionType.PURCHASE_SIP" in rows
        else None
    )
    sips = [r for r in out.rows if "PURCHASE_SIP" in str(r.raw.get("type"))]
    assert len(sips) == 1
    sip = sips[0].candidate
    assert isinstance(sip, NewTxn) and sip.type is TxnType.SIP and sip.stamp_duty == D("0.03")
    assert sip.net_cash_orig == D(-500) and sip.quantity == D(10) and sip.price == D(50)
    by_type = {str(r.raw.get("type")).split(".")[-1]: r for r in out.rows if r.raw.get("type")}
    assert by_type["REDEMPTION"].candidate.net_cash_orig == D(600)  # type: ignore[union-attr]
    assert by_type["DIVIDEND_REINVEST"].candidate.net_cash_orig == 0  # type: ignore[union-attr]
    assert by_type["DIVIDEND_PAYOUT"].candidate.net_cash_orig == D(7)  # type: ignore[union-attr]
    assert by_type["STT_TAX"].candidate.net_cash_orig == D("-0.60")  # type: ignore[union-attr]
    assert by_type["STAMP_DUTY_TAX"].candidate.type is TxnType.TAX  # type: ignore[union-attr]
    assert (
        by_type["SWITCH_OUT"].candidate.type is TxnType.SWITCH_OUT
        and by_type["SWITCH_IN"].candidate.type is TxnType.SWITCH_IN
    )  # type: ignore[union-attr]
    assert by_type["MISC"].status == "AMBIGUOUS" and "manual review" in (by_type["MISC"].reason or "")
    bad = [r for r in out.rows if (r.reason or "").startswith("UNITS_MISMATCH")]
    assert len(bad) == 1 and bad[0].status == "REJECTED"
    holding = [r for r in out.rows if r.item_kind == "STATEMENT_HOLDING"]
    assert len(holding) == 1 and isinstance(holding[0].candidate, NewStatementHolding)
    assert holding[0].candidate.units == D(20) and holding[0].candidate.as_of == date(2025, 3, 31)


def test_cas_stamp_duty_is_shared_across_same_day_purchases(res: FakeResolver) -> None:
    res.add_account(FOLIO)
    data = cas_data(
        [scheme([
            txn("2025-01-06", "PURCHASE", "300.00", "6.000", "49.9950"),
            txn("2025-01-06", "PURCHASE", "700.00", "14.000", "49.9950"),
            txn("2025-01-06", "STAMP_DUTY_TAX", "0.10", None, None),
        ])]
    )  # fmt: skip
    out = cas.parse_cas(data, res, "cas.pdf")
    stamps = [r.candidate.stamp_duty for r in out.rows if isinstance(r.candidate, NewTxn)]  # type: ignore[union-attr]
    assert stamps == [D("0.03"), D("0.07")] and sum(stamps) == D("0.10")


def test_cas_blocks_on_balance_mismatch_and_parser_warnings(res: FakeResolver) -> None:
    res.add_account(FOLIO)
    out = cas.parse_cas(
        cas_data([scheme([], close="10.000", calc="9.000")], parse_warnings=["row dropped"]), res, "c.pdf"
    )
    assert any("closing balance" in b for b in out.blocking) and any(
        "casparser warning: row dropped" in b for b in out.blocking
    )


def test_cas_scheme_without_identifiers_and_unknown_asset(res: FakeResolver) -> None:
    res.add_account(FOLIO)
    out = cas.parse_cas(
        cas_data([scheme([txn("2025-01-06", "PURCHASE", "500", "10", "50")], isin=None)]), res, "c.pdf"
    )
    assert out.rows[0].status == "NEW"  # falls back to the AMFI code
    data = cas_data(
        [{**scheme([txn("2025-01-06", "PURCHASE", "500", "10", "50")]), "isin": None, "amfi": None}]
    )
    out2 = cas.parse_cas(data, res, "c.pdf")
    assert all(r.status == "REJECTED" and "no ISIN or AMFI" in (r.reason or "") for r in out2.rows)


def test_cas_missing_fields_and_unknown_account(res: FakeResolver) -> None:
    out = cas.parse_cas(cas_data([scheme([txn("2025-01-06", "PURCHASE", None, "10", "50")])]), res, "c.pdf")
    assert "amount is missing" in (out.rows[0].reason or "") or out.rows[0].status == "REJECTED"
    assert all(r.status == "REJECTED" for r in out.rows)  # the folio account does not exist


def test_cas_depository_holdings_only(res: FakeResolver) -> None:
    res.add_account(DEMAT)
    res.add_asset("INE000000009", "EQUITY_IN")
    res.add_asset("INF000000009", "MF")
    data = NSDLCASData.model_validate(
        {
            "accounts": [
                {
                    "name": "x", "type": "NSDL", "dp_id": "IN300", "client_id": "123456", "folios": 1, "balance": "0",
                    "owners": [{"name": "o", "PAN": "AAAAA0000A"}],
                    "equities": [{"isin": "INE000000009", "num_shares": "10", "price": "1", "value": "10"}],
                    "mutual_funds": [{"isin": "INF000000009", "balance": "1.5", "nav": "1", "value": "1.5"}],
                    "bonds": [{"isin": "INE9999", "num_bonds": "3", "value": "3"}],
                }
            ],
            "statement_period": {"from": "01-Jan-2025", "to": "31-Mar-2025"},
            "investor_info": {"name": "n", "email": "e", "address": "a", "mobile": "m"},
            "file_type": "NSDL", "parse_warnings": ["nav not confirmed"],
        }
    )  # fmt: skip
    out = cas.parse_cas(data, res, "nsdl.pdf")
    assert out.source_kind == "CAS_NSDL" and len(out.rows) == 3
    assert [r.status for r in out.rows] == ["NEW", "NEW", "REJECTED"]  # bond ISIN not registered
    assert any("holdings only" in w for w in out.warnings) and out.blocking == [
        "casparser warning: nav not confirmed"
    ]


def test_read_cas_wraps_casparser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import casparser

    data = cas_data([])
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda path, password: data)
    assert cas.read_cas(tmp_path / "x.pdf", "pw") is data
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda path, password: {"not": "a model"})
    with pytest.raises(ImportRowError, match="unexpected"):
        cas.read_cas(tmp_path / "x.pdf", "pw")


def test_csv_writer_roundtrip_helper(tmp_path: Path) -> None:
    p = tmp_path / "r.csv"
    with p.open("w", newline="") as f:
        csv.writer(f).writerow(["a", "b"])
    assert tables.peek_headers(p) == {"a", "b"}
