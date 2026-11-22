# Phase 7 source check (V35, V36)

Checked 2026-11-22 by web search plus a direct fetch of a few pages. **This is a lead list, not a terms review.** Search snippets were not opened as pages unless marked "fetched". Nothing here lets code fetch automatically: holdings and news stay manual imports until the terms are read and recorded.

**Consent log (invariant 10).** On 2026-11-22 the investor named the AMCs, ETFs and stocks they hold and asked for this search. Queries sent to the search service named only AMCs, ETF issuers and public bodies, plus one query that used a name from the list ("Quantinuum ETF"). No amounts, folios or accounts were sent.

## Mutual fund houses (holdings, V35)

| AMC | Where monthly holdings appear | Checked |
| --- | --- | --- |
| PPFAS (Parag Parikh) | `amc.ppfas.com/downloads/factsheet/` ("Detailed Portfolio Disclosure") and per-month pages under `amc.ppfas.com/downloads/digital-factsheet/<year>/<month>-<year>/` | search only |
| Quant | Monthly factsheet PDFs at `quantmutual.com/Admin/Factsheet/`. A separate full-portfolio page was not found | search only |
| Nippon India | `mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures` lists monthly portfolios by month; NSE archives hold copies of the PDFs | search only |
| UTI | Official site `utimf.com` downloads area; the monthly portfolio page was not located | search only, not found |
| DSP | `dspim.com` downloads (monthly factsheets with holdings); scheme documents say a monthly portfolio is also emailed to registered holders within 10 days of month end | search only |
| All AMCs | AMFI has a portfolio disclosure page, `amfiindia.com/online-center/portfolio-disclosure`. It is a dynamic selector; no file URL or single consolidated file was found | fetched (page only) |

- File format (xlsx, pdf, csv) was not confirmed for any AMC. Layouts will differ, so a parser per AMC comes later.
- **Terms of use were not read for any AMC site.** AMFI's terms page (`/terms-of-use`) was not fetched.
- **The "10 days after month end" rule is not confirmed.** One search found only older SEBI circulars (3rd working day for some debt schemes in 2009; 7 working days for monthly AAUM data in 2014). The current Master Circular for Mutual Funds (20 Mar 2026) has a monthly portfolio section, but its deadline was not read. The 10-day figure used for staleness stays an assumption.

## ETFs and stocks (holdings, V35)

| Issuer | What was found | Terms |
| --- | --- | --- |
| Vanguard | Holdings on fund pages | **Fetched.** "you agree not to reproduce, distribute, or disseminate the portfolio holdings, in whole or in part, in any form without prior written permission from Vanguard"; provided "as is"; delayed. Personal reading only; do not redistribute |
| iShares (BlackRock) | Holdings download exists per fund | US holdings-page terms not found. A BlackRock report tool says downloaded data is for internal use (search only) |
| Defiance | Each fund has a "full holdings" page with a download option | Terms not found |
| WisdomTree | Fund pages | US terms say any use of WisdomTree Indexes or data needs a licence (search only) |
| Invesco (Nasdaq 100) | Holdings pages | US terms assert IP ownership; no holdings-specific clause found (search only) |
| State Street SPDR | Fund pages | Holdings download and terms not found |
| IREN, IONQ (stocks) | SEC EDGAR (already the D1 source for US fundamentals) | EDGAR needs a declared User-Agent with a contact email |

- **Tickers given by the investor on 2026-11-22**, with the issuer I took each to be (from general knowledge, not looked up): QNT (Quantinuum, a stock, not an ETF: use EDGAR like IREN and IONQ), QQQM (Invesco), SPY and XME (State Street SPDR), VOO, VOOV, VOOG and VTI (Vanguard), JPXN and IVV (iShares), QTUM (Defiance). The earlier "Quantinuum ETF" question is settled: it is the stock. A leveraged QNT fund (QNTU, Tradr) exists but is not on the list.
- **Also held (investor confirmed 2026-11-22, tickers from general knowledge, not looked up):** EWY (iShares MSCI South Korea), DXJ (WisdomTree Japan Hedged Equity) and VDE (Vanguard Energy).
- Six issuers cover all thirteen ETFs: Vanguard, iShares, State Street SPDR, Invesco, Defiance and WisdomTree. Of these, only Vanguard's terms were read.

## Official news feeds (V36)

| Source | Feed | Checked | Terms |
| --- | --- | --- | --- |
| SEBI | `https://www.sebi.gov.in/sebirss.xml` (press releases, circulars, orders) | fetched, reachable | no terms wording on the RSS page; the disclaimer URL tried returned 404 |
| RBI | press releases `https://rbi.org.in/pressreleases_rss.xml`; also notifications, speeches, tenders, publications (all listed on `rbi.org.in/Scripts/rss.aspx`) | fetched, reachable | footer "All Rights Reserved"; the disclaimer page says "caching and links to, and the framing of this Web Site or any of the contents are prohibited" (aimed at other websites; personal use is not addressed) |
| PIB | RSS page `pib.gov.in/ViewRss.aspx` | fetch returned HTTP 403; one third-party listing gives a feed URL that is not confirmed | not read |
| NSE | RSS page `nseindia.com/static/rss-feed` | fetch timed out twice | Terms of use not read. The NSE Data & Analytics terms (search only) restrict external use of licensed data, which includes corporate data |
| BSE | feed offered through an RSS reader (search only) | not fetched | disclaimer only: no warranty on accuracy (search only) |
| Federal Reserve | official page `federalreserve.gov/feeds`; feed files such as `press_other.xml`, `press_bcreg.xml`, `boardmeetings.xml` | search only; the all-releases URL was not confirmed | not read |
| SEC | `sec.gov/about/secrss.shtml` lists press, speeches, litigation and other feeds | search only | not read |
| Bank of Japan | "What's New" RSS and a statistics RSS (`boj.or.jp/en/tips.htm`) | search only | copyright page (search): content may be copied with credit to the Bank, except marked materials; RSS may stop without notice |
| EIA | `eia.gov/tools/rssfeeds` lists Today in Energy, What's New, testimony, presentations | search only | not read |
| Bank of Korea | not searched | — | — |

## What this means

- Nothing changes in the code. Holdings and news stay manual imports. **V35 and V36 stay open.**
- Personal reading and saving a file for your own use looks acceptable at these sources, but none of the AMC, NSE, BSE or PIB terms were actually read. Vanguard's clause is the one confirmed restriction (do not redistribute).
- Open items: read AMFI and each AMC's terms; find UTI's and Quant's holdings pages; read the current Master Circular for the disclosure deadline; confirm PIB and NSE feed URLs by hand in a browser; check Bank of Korea.
