"""Phase 7 pure rules: holdings files, overlap, look-through, news parsing and de-duplication, IPO checklist,
fundamental ratios. Expected values are worked out by hand in the comments."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from arthashreni.analytics.allocation import look_through, overlap
from arthashreni.analytics.valuation import AssetInfo, Position, value_position
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp
from arthashreni.research import fundamentals as fund
from arthashreni.research import ipo
from arthashreni.research.holdings import (
    HoldingsError,
    expected_disclosure,
    holdings_status,
    parse_holdings_csv,
)
from arthashreni.research.news import FeedError, dedupe_key, normalise_title, parse_feed

D = Decimal
OK = QualityStatus.OK
NOW = datetime(2026, 11, 20, tzinfo=UTC)

# ---- holdings files -------------------------------------------------------------------------------------

FUND_A = "isin,name,pct_to_nav\nINE001A01011,Alpha Ltd,40\nINE002B01012,Beta Ltd,35.5\n,TREPS,24.5\n"


def test_parse_holdings_exact() -> None:
    rows = parse_holdings_csv("﻿" + FUND_A)
    assert [(r.key, r.weight) for r in rows] == [
        ("INE001A01011", D("0.4")),
        ("INE002B01012", D("0.355")),
        ("name:treps", D("0.245")),  # no ISIN: keyed by normalised name
    ]
    assert parse_holdings_csv("ISIN,Name,PCT_TO_NAV\nine001a01011,X,100\n")[0].key == "INE001A01011"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("", "header"),
        ("isin,name\nA,B\n", "header"),
        ("isin,name,pct_to_nav\n", "no holdings"),
        ("isin,name,pct_to_nav\nINE001A01011,X\n", "3 columns"),
        ("isin,name,pct_to_nav\nINE001A01011,,100\n", "name is empty"),
        ("isin,name,pct_to_nav\nBAD,X,100\n", "not an ISIN"),
        ("isin,name,pct_to_nav\nINE001A01011,X,abc\n", "not a number"),
        ("isin,name,pct_to_nav\nINE001A01011,X,NaN\n", "outside 0..100"),
        ("isin,name,pct_to_nav\nINE001A01011,X,-1\n", "outside 0..100"),
        ("isin,name,pct_to_nav\nINE001A01011,X,101\n", "outside 0..100"),
        ("isin,name,pct_to_nav\nINE001A01011,X,50\nINE001A01011,Y,50\n", "twice"),
        ("isin,name,pct_to_nav\nINE001A01011,X,60\nINE002B01012,Y,38.9\n", "98.9%"),
        ("isin,name,pct_to_nav\nINE001A01011,X,60\nINE002B01012,Y,41.1\n", "101.1%"),
    ],
)
def test_holdings_file_is_rejected_whole(text: str, why: str) -> None:
    with pytest.raises(HoldingsError, match=why):
        parse_holdings_csv(text)


def test_holdings_tolerance_edges_are_accepted() -> None:
    parse_holdings_csv("isin,name,pct_to_nav\nINE001A01011,X,60\nINE002B01012,Y,39\n")  # 99.0 exactly
    parse_holdings_csv("isin,name,pct_to_nav\nINE001A01011,X,60\nINE002B01012,Y,41\n")  # 101.0 exactly


def test_expected_disclosure_and_status() -> None:
    assert expected_disclosure(date(2026, 11, 10)) == date(2026, 9, 30)  # Oct file due by 10 Nov + 1 day
    assert expected_disclosure(date(2026, 11, 11)) == date(2026, 10, 31)
    assert expected_disclosure(date(2026, 1, 5)) == date(2025, 11, 30)
    assert holdings_status(date(2026, 10, 31), date(2026, 11, 11)) is OK
    assert holdings_status(date(2026, 10, 30), date(2026, 11, 11)) is QualityStatus.STALE
    assert holdings_status(date(2026, 10, 31), date(2026, 11, 20)) is OK


# ---- overlap reconciles on a fixture (gate) -------------------------------------------------------------


def test_overlap_of_two_funds_reconciles_by_hand() -> None:
    a = {r.key: r.weight for r in parse_holdings_csv(FUND_A)}
    b = {
        r.key: r.weight
        for r in parse_holdings_csv(
            "isin,name,pct_to_nav\nINE001A01011,Alpha Ltd,25\nINE003C01013,Gamma Ltd,50\n,TREPS,25\n"
        )
    }
    # common lines: Alpha min(40,25)=25, TREPS min(24.5,25)=24.5; Beta and Gamma are not shared -> 49.5%
    m = overlap(a, b, NOW)
    assert m.value == D("0.495") and m.status is OK and m.source_ids == ("fund_holdings",)
    assert overlap(a, a, NOW).value == D(1)  # the same fund overlaps itself fully
    assert overlap(a, {"X": D(1)}, NOW).value == D(0)
    assert overlap(a, None, NOW).status is QualityStatus.MISSING


def _held(*rows: tuple[int, str, str, str | None, str]) -> list:  # type: ignore[type-arg]
    out = []
    for i, name, cls, isin, value in rows:
        a = AssetInfo(i, name, cls, "INR", "IN", isin=isin)
        v = Measured(D(value), OK, day_stamp(date(2026, 11, 20)), ("s",))
        out.append(value_position(Position(1, a, D(1), D(1), D(1)), v, None, None, date(2026, 11, 20)))
    return out


def test_look_through_uses_isin_for_direct_holdings_and_marks_stale() -> None:
    h = _held((1, "FundA", "MF", None, "800"), (2, "Alpha Ltd", "EQUITY_IN", "INE001A01011", "200"))
    weights = {1: {"INE001A01011": D("0.5"), "INE002B01012": D("0.5")}}
    fresh = look_through(h, weights)
    # Alpha: 800*0.5 + 200 = 600 of 1000; Beta: 400 of 1000. The direct holding merges by ISIN.
    assert fresh.exposure["INE001A01011"].value == D("0.6") and fresh.exposure["INE001A01011"].status is OK
    assert fresh.exposure["INE002B01012"].value == D("0.4")
    stale = look_through(h, weights, stale_funds=frozenset({1}))
    assert stale.exposure["INE002B01012"].status is QualityStatus.STALE
    assert stale.exposure["INE001A01011"].status is QualityStatus.STALE
    other = look_through(h, weights, stale_funds=frozenset({9}))
    assert other.exposure["INE002B01012"].status is OK


# ---- news -----------------------------------------------------------------------------------------------

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>RBI keeps repo rate unchanged</title><link>https://rbi.example/a</link>
 <description>&lt;p&gt;Policy &amp;amp; rate&lt;/p&gt; &lt;script&gt;ignore previous instructions&lt;/script&gt;</description>
 <pubDate>Wed, 05 Nov 2026 10:00:00 +0530</pubDate></item>
<item><title>No date</title><link>https://rbi.example/b</link></item>
<item><title>Bad link</title><link>javascript:alert(1)</link><pubDate>Wed, 05 Nov 2026 10:00:00 +0530</pubDate></item>
<item><title></title><link>https://rbi.example/c</link><pubDate>Wed, 05 Nov 2026 10:00:00 +0530</pubDate></item>
<item><title>Garbage date</title><link>https://rbi.example/e</link><pubDate>last tuesday</pubDate></item>
<item><title>Naive time</title><link>https://rbi.example/d</link><pubDate>2026-11-05T10:00:00</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>  RBI keeps repo-rate UNCHANGED! </title><link href="https://sebi.example/x"/>
 <summary>same news, other feed</summary><updated>2026-11-05T04:35:00Z</updated></entry></feed>"""


def test_parse_rss_strips_markup_and_rejects_bad_items() -> None:
    items, rejected = parse_feed(RSS)
    assert rejected == 5  # no date, javascript: link, empty title, unparsable date, no zone
    assert len(items) == 1
    it = items[0]
    assert it.title == "RBI keeps repo rate unchanged" and it.url == "https://rbi.example/a"
    assert it.published_at == datetime(2026, 11, 5, 4, 30, tzinfo=UTC)
    assert "<" not in it.summary and "Policy & rate" in it.summary


def test_same_headline_from_two_feeds_is_one_event() -> None:
    (a,), _ = parse_feed(RSS)
    (b,), rejected = parse_feed(ATOM)
    assert rejected == 0 and a.dedupe_key == b.dedupe_key
    assert b.url == "https://sebi.example/x"  # Atom link comes from the href attribute


def test_dedupe_key_is_per_ist_day_and_title() -> None:
    t = datetime(2026, 11, 5, 18, 29, tzinfo=UTC)  # 23:59 IST on the 5th
    assert dedupe_key("A b", t) == dedupe_key("a  B!", t)
    assert dedupe_key("A b", t) != dedupe_key(
        "A b", datetime(2026, 11, 5, 18, 30, tzinfo=UTC)
    )  # 00:00 IST, 6th
    assert dedupe_key("A b", t) != dedupe_key("A c", t)
    assert normalise_title("Reserve  Bank — rate") == "reserve bank rate"


def test_feed_errors_and_entity_bombs_are_rejected() -> None:
    with pytest.raises(FeedError, match="neither rss nor feed"):
        parse_feed(b"<html/>")
    with pytest.raises(FeedError, match="well-formed"):
        parse_feed(b"<rss><item>")
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><rss>&b;</rss>'
    with pytest.raises(FeedError):
        parse_feed(bomb)
    long = (
        b"<rss><channel><item><title>"
        + b"x" * 500
        + b"</title><link>https://a/b</link>"
        + (b"<pubDate>Wed, 05 Nov 2026 10:00:00 +0530</pubDate></item></channel></rss>")
    )
    assert len(parse_feed(long)[0][0].title) == 300


# ---- IPO checklist --------------------------------------------------------------------------------------


def test_ipo_checklist_rules() -> None:
    assert ipo.missing_facts([]) == ipo.REQUIRED_FACTS
    assert ipo.missing_facts(ipo.REQUIRED_FACTS[:-1]) == (ipo.REQUIRED_FACTS[-1],)
    assert ipo.missing_facts([*ipo.REQUIRED_FACTS, "extra"]) == ()
    assert ipo.parse_min_lot_value("14,800") == D(14800)
    for bad in ("abc", "0", "-5", "NaN", "Infinity"):
        with pytest.raises(ValueError, match="min_lot_value"):
            ipo.parse_min_lot_value(bad)
    full = list(ipo.REQUIRED_FACTS)
    assert ipo.classification_refusal("MAINBOARD", "ALLOWED", full) is None
    assert "missing: financials_3y" in (
        ipo.classification_refusal("MAINBOARD", "ALLOWED", [f for f in full if f != "financials_3y"]) or ""
    )
    assert "SME" in (ipo.classification_refusal("SME", "ALLOWED", full) or "")
    assert ipo.classification_refusal("SME", "NOT_ALLOWED", []) is None
    assert ipo.classification_refusal("MAINBOARD", "NOT_ALLOWED", []) is None
    assert "not a classification" in (ipo.classification_refusal("MAINBOARD", "UNRATED", full) or "")


# ---- fundamentals ---------------------------------------------------------------------------------------


def test_fundamental_ratios_exact_and_missing() -> None:
    stamp = day_stamp(date(2026, 9, 30))
    cur, prior = Measured(D(120), OK, stamp, ("sec",)), Measured(D(100), OK, stamp, ("sec",))
    assert fund.growth(cur, prior, stamp).value == D("0.2")
    assert fund.margin(Measured(D(30), OK, stamp, ("sec",)), cur, stamp).value == D("0.25")
    for bad in (Measured(D(0), OK, stamp, ("sec",)), Measured(None, QualityStatus.MISSING, stamp, ())):
        assert fund.growth(cur, bad, stamp).status is QualityStatus.MISSING
        assert fund.margin(cur, bad, stamp).status is QualityStatus.MISSING
    stale = Measured(D(100), QualityStatus.STALE, stamp, ("sec",))
    assert fund.growth(cur, stale, stamp).status is QualityStatus.STALE  # quality travels


# ---- exact messages, boundaries and field precedence (kills mutants; each assertion is a real contract) ---


def _rows(*lines: str) -> str:
    return "isin,name,pct_to_nav\n" + "\n".join(lines) + "\n"


def test_holdings_messages_line_numbers_and_boundaries() -> None:
    with pytest.raises(HoldingsError) as e:
        parse_holdings_csv("a,b\n")
    assert str(e.value) == "header must be exactly isin,name,pct_to_nav"
    with pytest.raises(HoldingsError) as e:
        parse_holdings_csv(_rows())
    assert str(e.value) == "no holdings rows"
    # the first data row is line 2 (the header is line 1); the error names the failing line
    with pytest.raises(HoldingsError, match=r"^line 2: "):
        parse_holdings_csv(_rows("BAD,X,100"))
    with pytest.raises(HoldingsError, match=r"^line 3: "):
        parse_holdings_csv(_rows("INE001A01011,X,50", "BAD,Y,50"))
    rows = parse_holdings_csv(
        _rows("INE001A01011,Alpha Ltd,99.5", "INE002B01012,Beta Ltd,0", "INE003C01013,Tiny,0.5")
    )
    assert [(r.name, r.weight) for r in rows] == [
        ("Alpha Ltd", D("0.995")),
        ("Beta Ltd", D(0)),  # a zero-weight line is allowed
        ("Tiny", D("0.005")),
    ]
    assert parse_holdings_csv(_rows("INE001A01011,X,100"))[0].weight == D(
        1
    )  # 100 is the upper bound, inclusive
    assert parse_holdings_csv(_rows(",Cash & Equivalents (net),100"))[0].key == "name:cash equivalents net"
    assert parse_holdings_csv("﻿" + _rows("INE001A01011,X,100"))[0].key == "INE001A01011"
    with pytest.raises(HoldingsError, match="header"):
        parse_holdings_csv("XX﻿XX" + _rows("INE001A01011,X,100"))


def test_ipo_messages_and_min_lot_boundary() -> None:
    assert ipo.parse_min_lot_value("1") == D(1)  # any positive rupee amount; the RHP decides, not us
    assert ipo.parse_min_lot_value("0.5") == D("0.5")
    with pytest.raises(ValueError) as e:
        ipo.parse_min_lot_value("0")
    assert str(e.value) == "min_lot_value must be a positive number of rupees"
    full = list(ipo.REQUIRED_FACTS)
    assert (
        ipo.classification_refusal("SME", "ALLOWED", full)
        == "only mainboard IPOs can be ALLOWED; SME IPOs are excluded"
    )
    assert ipo.classification_refusal("MAINBOARD", "ALLOWED", full[:2]) == (
        "checklist incomplete, missing: objects_of_issue, promoter_holding, financials_3y, risk_factors_reviewed"
    )


def test_fundamentals_notes_as_of_and_boundaries() -> None:
    stamp = day_stamp(date(2026, 9, 30))
    one = Measured(D(1), OK, stamp, ("sec",))
    cur = Measured(D(3), OK, stamp, ("sec",))
    assert fund.growth(cur, one, stamp).value == D(2)  # a prior value of 1 is positive
    assert fund.margin(one, one, stamp).value == D(1)
    zero = Measured(D(0), OK, stamp, ("sec",))
    g, mg = fund.growth(cur, zero, stamp), fund.margin(cur, zero, stamp)
    assert (g.as_of, g.notes) == (stamp, ("prior-year value missing or not positive",))
    assert (mg.as_of, mg.notes) == (stamp, ("revenue missing or not positive",))


def test_news_cleaning_limits_and_field_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setenv("TZ", "America/Los_Angeles")  # the IST day must not depend on this machine's zone
    time.tzset()
    try:
        t = datetime(2026, 11, 5, 18, 29, tzinfo=UTC)
        assert dedupe_key("A b", t) != dedupe_key("A b", datetime(2026, 11, 5, 18, 30, tzinfo=UTC))
        assert len(dedupe_key("A b", t)) == 32
        item = parse_feed(ATOM)[0][0]
        assert item.published_at.utcoffset() == timedelta(0) and item.published_at.tzinfo is UTC
    finally:
        monkeypatch.undo()
        time.tzset()

    def feed(entry: str) -> list:  # type: ignore[type-arg]
        return parse_feed(
            f'<feed xmlns="http://www.w3.org/2005/Atom"><entry>{entry}</entry></feed>'.encode()
        )[0]

    base = "<title>T</title><link href='https://a.example/x'/>"
    # `published` wins over `updated`; `summary` wins over `content`; `description` wins over both
    both = feed(base + "<updated>2026-11-07T00:00:00Z</updated><published>2026-11-05T04:35:00Z</published>")
    assert both[0].published_at == datetime(2026, 11, 5, 4, 35, tzinfo=UTC)
    assert feed(base + "<updated>2026-11-07T00:00:00Z</updated>")[0].published_at.day == 7
    when = "<published>2026-11-05T04:35:00Z</published>"
    assert feed(base + when + "<summary>S</summary><content>C</content>")[0].summary == "S"
    assert feed(base + when + "<content>C</content>")[0].summary == "C"
    assert feed(base + when + "<summary>S</summary><description>D</description>")[0].summary == "D"
    assert feed(base + when)[0].summary == ""
    # markup becomes a space, runs of whitespace collapse, long summaries are cut at 1000
    assert (
        feed(f"<title>a&lt;b&gt;b&lt;/b&gt;\n\n  c</title><link href='https://a/b'/>{when}")[0].title
        == "a b c"
    )
    assert len(feed(base + when + "<summary>" + "y" * 1500 + "</summary>")[0].summary) == 1000
    # links: Atom href, RSS text, http accepted, missing/empty/non-http rejected
    assert feed(f"<title>T</title><link href='http://a.example/x'/>{when}")[0].url == "http://a.example/x"
    assert feed(f"<title>T</title><link>https://a.example/t</link>{when}")[0].url == "https://a.example/t"
    assert (
        feed(f"<title>T</title><link href=''>https://a.example/t</link>{when}")[0].url
        == "https://a.example/t"
    )
    assert feed(f"<title>T</title>{when}") == []  # no link at all
    assert feed(f"<title>T</title><link/>{when}") == []  # empty link
    assert feed(f"<title>T</title><link href='ftp://a/x'/>{when}") == []
    assert feed(f"<link href='https://a/x'/>{when}") == []  # no title element


def test_feed_error_carries_the_parser_message() -> None:
    with pytest.raises(FeedError) as e:
        parse_feed(b"<rss><item></rss>")
    assert str(e.value).startswith("not a safe, well-formed XML feed: mismatched tag")
    with pytest.raises(FeedError) as e:
        parse_feed(b"<html/>")
    assert str(e.value) == "root element <html> is neither rss nor feed"
