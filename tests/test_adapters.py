"""Adapters against recorded responses (real excerpts of AMFI, SBI archive, Frankfurter FBIL, captured
2026-11-11) and hand-built Tiingo / SEC bodies. The latter follow each provider's documented fields but are
NOT recordings: no key or declared User-Agent was used to capture them (register V23). No network."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from arthashreni.ingestion.sources import amfi, fx, manual, sec, tiingo
from arthashreni.ingestion.sources.contract import (
    FundamentalObs,
    FxObs,
    PriceObs,
    RawPayload,
    SchemaDrift,
    SourceUnavailable,
    Window,
)
from arthashreni.ingestion.sources.http import Http
from arthashreni.ingestion.sources.registry import SPECS

REC = Path(__file__).parent / "fixtures" / "recorded"
W = Window(date(2026, 1, 1), date(2026, 11, 10))
D = Decimal


def http_for(
    handler, source_id: str = "x", **kw
) -> tuple[Http, list[float], list[tuple[bool, int | None, str]]]:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    calls: list[tuple[bool, int | None, str]] = []
    h = Http(
        source_id,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        on_call=lambda ok, st, d: calls.append((ok, st, d)),
        **kw,
    )
    return h, sleeps, calls


def payload(source: str, body: str | bytes, window: Window = W) -> RawPayload:
    from datetime import UTC, datetime

    b = body.encode() if isinstance(body, str) else body
    return RawPayload(source, datetime(2026, 11, 11, tzinfo=UTC), window, b)


# ---- Http: rate limit, retries with backoff, timeouts --------------------------------------------


def test_retries_with_exponential_backoff_then_succeeds() -> None:
    codes = iter([503, 429, 200])
    h, sleeps, calls = http_for(lambda r: httpx.Response(next(codes), content=b"ok"), backoff=2.0)
    assert h.get("https://example.test/") == b"ok"
    assert [s for s in sleeps if s >= 2.0] == [2.0, 4.0]
    assert [c[0] for c in calls] == [False, False, True]


def test_gives_up_as_an_outage_after_the_retries() -> None:
    h, _, calls = http_for(lambda r: httpx.Response(500), retries=2)
    with pytest.raises(SourceUnavailable, match="HTTP 500 after 3 attempts"):
        h.get("https://example.test/")
    assert len(calls) == 3


def test_timeouts_and_connection_errors_are_outages() -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=r)

    h, _, _ = http_for(boom, retries=1)
    with pytest.raises(SourceUnavailable, match="ConnectTimeout"):
        h.get("https://example.test/")


def test_client_errors_are_not_retried() -> None:
    h, sleeps, calls = http_for(lambda r: httpx.Response(404), retries=3)
    with pytest.raises(SourceUnavailable, match="HTTP 404"):
        h.get("https://example.test/")
    assert len(calls) == 1 and not sleeps


def test_minimum_interval_between_requests() -> None:
    now = [100.0]
    sleeps: list[float] = []
    h = Http(
        "x",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"."))),
        min_interval=5.0,
        sleep=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s))[0],
        clock=lambda: now[0],
    )
    h.get("https://example.test/")
    now[0] += 1.0
    h.get("https://example.test/")
    assert sleeps == [4.0]


# ---- AMFI ---------------------------------------------------------------------------------------


def test_amfi_keeps_only_held_schemes_and_parses_real_rows() -> None:
    text = (REC / "amfi_excerpt.txt").read_text()
    h, _, _ = http_for(lambda r: httpx.Response(200, content=text.encode()))
    a = amfi.AmfiNav(h, {"135762", "151165"})
    raw = a.fetch(W)
    assert b"135765" not in raw.body and raw.body.count(b"\n") == 2  # header + two held schemes
    obs = a.parse(raw)
    assert [(o.asset_ref, o.close, o.valid_at, o.price_type) for o in obs if isinstance(o, PriceObs)] == [
        ("AMFI:135762", D("30.9434"), date(2026, 11, 9), "NAV"),
        ("AMFI:151165", D("14.2474"), date(2026, 11, 9), "NAV"),
    ]


def test_amfi_header_change_is_schema_drift() -> None:
    old_format = (
        "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n"
    )
    h, _, _ = http_for(lambda r: httpx.Response(200, content=old_format.encode()))
    with pytest.raises(SchemaDrift, match="header changed"):
        amfi.AmfiNav(h, {"1"}).fetch(W)


@pytest.mark.parametrize(
    "line,msg",
    [
        ("abc;I;-;N;Direct Plan;Growth Option;1.0;09-Nov-2026", "not numeric"),
        ("", "no scheme rows"),
    ],
)
def test_amfi_bad_rows_are_schema_drift(line: str, msg: str) -> None:
    body = f"{amfi.HEADER}\n{line}\n"
    h, _, _ = http_for(lambda r: httpx.Response(200, content=body.encode()))
    with pytest.raises(SchemaDrift, match=msg):
        amfi.AmfiNav(h, {"1"}).fetch(W)


def test_amfi_nav_and_date_must_parse() -> None:
    bad_nav = payload("amfi_nav", f"{amfi.HEADER}\n1;I;-;N;Direct Plan;Growth Option;N.A.;09-Nov-2026\n")
    with pytest.raises(SchemaDrift, match="not a number"):
        amfi.AmfiNav(None, {"1"}).parse(bad_nav)  # type: ignore[arg-type]
    with pytest.raises(SchemaDrift, match="DD-Mon-YYYY"):
        amfi.parse_amfi_date("2026-11-09")
    assert amfi.AmfiNav(None, set()).parse(payload("amfi_nav", amfi.HEADER)) == []  # type: ignore[arg-type]


# ---- SBI archive and FBIL -----------------------------------------------------------------------


def test_sbi_parses_real_rows_including_the_zero_rate() -> None:
    text = (REC / "sbi_excerpt.csv").read_text()
    h, _, _ = http_for(lambda r: httpx.Response(200, content=text.encode()))
    a = fx.SbiTt(h)
    raw = a.fetch(Window(date(2020, 1, 1), date(2026, 12, 31)))
    obs = [o for o in a.parse(raw) if isinstance(o, FxObs)]
    assert [o.rate for o in obs] == [D("0.00"), D("71.65"), D("93.6"), D("93.55"), D("93.6")]
    assert all(o.rate_type == "SBI_TT_BUY" and o.underlying_series == "SBI" for o in obs)
    assert obs[0].valid_at == date(2020, 1, 4) and obs[0].observed_at is not None
    assert obs[-1].observed_at.isoformat() == "2026-11-09T07:38:00+00:00"  # 13:08 IST


def test_sbi_window_filters_rows() -> None:
    text = (REC / "sbi_excerpt.csv").read_text()
    h, _, _ = http_for(lambda r: httpx.Response(200, content=text.encode()))
    raw = fx.SbiTt(h).fetch(Window(date(2026, 11, 7), date(2026, 11, 9)))
    assert raw.body.decode().count("\n") == 3  # header + 2 rows


def test_sbi_column_change_is_schema_drift() -> None:
    h, _, _ = http_for(lambda r: httpx.Response(200, content=b"DATE,RATE\n2026-11-09 09:00,93\n"))
    with pytest.raises(SchemaDrift, match="columns changed"):
        fx.SbiTt(h).fetch(W)
    bad = payload("sbi_tt_archive", ",".join(fx.SBI_HEADER) + "\nnot-a-date,x,1,1,1,1,1,1,1,1\n")
    with pytest.raises(SchemaDrift, match="YYYY-MM-DD HH:MM"):
        fx.SbiTt(None).parse(bad)  # type: ignore[arg-type]
    bad_rate = payload("sbi_tt_archive", ",".join(fx.SBI_HEADER) + "\n2026-11-09 09:00,x,n/a,1,1,1,1,1,1,1\n")
    with pytest.raises(SchemaDrift, match="not a number"):
        fx.SbiTt(None).parse(bad_rate)  # type: ignore[arg-type]


def test_fbil_parses_the_real_frankfurter_response() -> None:
    body = (REC / "fbil_response.json").read_bytes()
    seen: list[httpx.Request] = []
    h, _, _ = http_for(lambda r: (seen.append(r), httpx.Response(200, content=body))[1])
    a = fx.Fbil(h)
    obs = [o for o in a.parse(a.fetch(Window(date(2026, 10, 26), date(2026, 11, 3)))) if isinstance(o, FxObs)]
    assert (obs[-1].valid_at, obs[-1].rate) == (date(2026, 11, 3), D("94.8606"))
    assert len(obs) == 7 and all(o.rate_type == "FBIL_REF" and o.provider == "frankfurter" for o in obs)
    assert "/providers/fbil/rates" in str(seen[0].url)  # the FBIL provider, never the blended default


@pytest.mark.parametrize(
    "body",
    ['{"rates": []}', '[{"date": "2026-11-03", "base": "USD", "quote": "INR"}]', "not json",
     '[{"date": "2026-11-03", "base": "EUR", "quote": "INR", "rate": 1.0}]'],
)  # fmt: skip
def test_fbil_shape_changes_are_schema_drift(body: str) -> None:
    with pytest.raises(SchemaDrift):
        fx.Fbil(None).parse(payload("fbil_ref", body))  # type: ignore[arg-type]


# ---- Tiingo and SEC (hand-built bodies) ---------------------------------------------------------

TIINGO_ROW = {"date": "2026-11-06T00:00:00.000Z", "close": 180.5, "high": 181, "low": 179, "open": 180,
              "volume": 1000, "adjClose": 180.5, "divCash": 0.0, "splitFactor": 1.0}  # fmt: skip


def test_tiingo_request_and_parse() -> None:
    seen: list[httpx.Request] = []
    h, _, _ = http_for(lambda r: (seen.append(r), httpx.Response(200, json=[TIINGO_ROW]))[1])
    a = tiingo.Tiingo(h, "k3y", ["DTECH"])
    obs = a.parse(a.fetch(W))
    assert obs == [PriceObs(asset_ref="TICKER:DTECH", valid_at=date(2026, 11, 6), close=D("180.5"), currency="USD",
                            price_type="CLOSE", split_factor=D("1.0"))]  # fmt: skip
    assert seen[0].headers["authorization"] == "Token k3y"
    assert "k3y" not in str(seen[0].url)  # the key travels in a header, never in the URL


@pytest.mark.parametrize(
    "body", ['{"DTECH": {"detail": "Not found"}}', '{"DTECH": [{"date": "2026-11-06"}]}', "[]"]
)
def test_tiingo_shape_changes_are_schema_drift(body: str) -> None:
    with pytest.raises(SchemaDrift):
        tiingo.Tiingo(None, "k", []).parse(payload("tiingo_eod", body))  # type: ignore[arg-type]


def facts(**units: list[dict[str, object]]) -> str:
    return json.dumps(
        {"0000320193": {"facts": {"us-gaap": {c: {"units": {"USD": v}} for c, v in units.items()}}}}
    )


def test_sec_parses_whitelisted_concepts_in_window() -> None:
    f = {"end": "2026-06-27", "val": 94930000000, "accn": "0000320193-26-000070", "fy": 2026, "fp": "Q3",
         "form": "10-Q", "filed": "2026-08-01"}  # fmt: skip
    old = {**f, "end": "2025-06-28", "accn": "0000320193-25-000057"}
    body = facts(Revenues=[f, old], SomethingElse=[f])
    obs = sec.SecCompanyFacts(None, []).parse(
        payload("sec_companyfacts", body, Window(date(2026, 1, 1), date(2026, 11, 10)))
    )  # type: ignore[arg-type]
    assert obs == [FundamentalObs(asset_ref="CIK:0000320193", concept="Revenues", valid_at=date(2026, 6, 27),
                                  fiscal_year=2026, fiscal_period="Q3", form="10-Q", unit="USD",
                                  value=D("94930000000"), filed_at=date(2026, 8, 1),
                                  accession_no="0000320193-26-000070")]  # fmt: skip


def test_sec_fetch_pads_the_cik_and_sends_the_declared_user_agent() -> None:
    seen: list[httpx.Request] = []
    h, _, _ = http_for(
        lambda r: (seen.append(r), httpx.Response(200, content=facts().encode()))[1],
        headers={"User-Agent": "Test Person test@example.com"},
    )
    sec.SecCompanyFacts(h, ["320193"]).fetch(W)
    assert str(seen[0].url).endswith("/CIK0000320193.json")
    assert seen[0].headers["user-agent"] == "Test Person test@example.com"


@pytest.mark.parametrize(
    "body",
    [
        "[]",
        '{"0000320193": {"facts": {}}}',
        '{"0000320193": {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [{"end": "x"}]}}}}}}',
    ],
)
def test_sec_shape_changes_are_schema_drift(body: str) -> None:
    with pytest.raises(SchemaDrift):
        sec.SecCompanyFacts(None, []).parse(payload("sec_companyfacts", body))  # type: ignore[arg-type]


# ---- manual CSV ---------------------------------------------------------------------------------


def test_manual_prices_and_fx(tmp_path: Path) -> None:
    p = tmp_path / "p.csv"
    p.write_text(
        "asset_ref,valid_at,close,currency,price_type\nTICKER:XNSE:DSTK,2026-11-02,512.35,inr,CLOSE\n"
    )
    obs = manual.ManualPrices(p).parse(manual.ManualPrices(p).fetch(W))
    assert obs == [PriceObs(asset_ref="TICKER:XNSE:DSTK", valid_at=date(2026, 11, 2), close=D("512.35"),
                            currency="INR", price_type="CLOSE")]  # fmt: skip
    f = tmp_path / "f.csv"
    f.write_text("rate_type,valid_at,rate\nFBIL_REF,2026-11-04,94.9\n")
    fobs = manual.ManualFx(f).parse(manual.ManualFx(f).fetch(W))
    assert [(o.rate_type, o.provider, o.rate) for o in fobs if isinstance(o, FxObs)] == [
        ("FBIL_REF", "manual", D("94.9"))
    ]


@pytest.mark.parametrize(
    "text,msg",
    [
        ("a,b\n1,2\n", "expected columns"),
        ("asset_ref,valid_at,close,currency,price_type\nX,2026-13-01,1,INR,CLOSE\n", "YYYY-MM-DD"),
        ("asset_ref,valid_at,close,currency,price_type\nX,2026-11-01,abc,INR,CLOSE\n", "not a number"),
        ("asset_ref,valid_at,close,currency,price_type\nX,2026-11-01,1,INR,LAST\n", "price_type"),
    ],
)
def test_manual_prices_bad_files(tmp_path: Path, text: str, msg: str) -> None:
    p = tmp_path / "p.csv"
    p.write_text(text)
    with pytest.raises(SchemaDrift, match=msg):
        manual.ManualPrices(p).parse(manual.ManualPrices(p).fetch(W))


def test_manual_fx_refuses_other_rate_types(tmp_path: Path) -> None:
    f = tmp_path / "f.csv"
    f.write_text("rate_type,valid_at,rate\nSBI_TT_BUY,2026-11-04,93.0\n")
    with pytest.raises(SchemaDrift, match="supports"):
        manual.ManualFx(f).parse(manual.ManualFx(f).fetch(W))


# ---- registry matches the feasibility document --------------------------------------------------


def test_registry_has_one_row_per_feasibility_source() -> None:
    doc = (Path(__file__).parents[1] / "docs" / "data-sources" / "feasibility.md").read_text()
    ids = {ln.split("|")[1].strip(" `") for ln in doc.split("## Summary")[1].split("## Evidence")[0].splitlines()
           if ln.startswith("| `")}  # fmt: skip
    assert ids - set(SPECS) == set(), "feasibility sources missing from the registry"
    assert set(SPECS) - ids <= {"manual_prices"}, "registry sources not in feasibility.md"


def test_prohibited_and_unbuilt_sources_are_not_enabled() -> None:
    assert not SPECS["nse_bhavcopy"].enabled and SPECS["nse_bhavcopy"].adapter is None
    assert not SPECS["alphavantage_eod"].enabled and SPECS["alphavantage_eod"].adapter is None
    assert all(not s.enabled for s in SPECS.values() if s.adapter is None)
