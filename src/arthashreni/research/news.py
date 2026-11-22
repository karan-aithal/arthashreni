"""Official news feeds (D1) as untrusted data (invariant 11): parsed, stripped of markup, de-duplicated, never
interpreted. XML goes through `defusedxml` (no entity expansion). Same headline on the same IST day is one
event; a reworded headline is a different event (a documented limit, ADR-0030)."""

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

IST = timezone(timedelta(hours=5, minutes=30))
TITLE_MAX, SUMMARY_MAX = 300, 1000
_TAG = re.compile(r"<[^>]*>")


class FeedError(ValueError):
    """The payload is not an RSS 2.0 or Atom feed: the whole file is rejected."""


@dataclass(frozen=True)
class NewsItem:
    title: str
    summary: str
    url: str
    published_at: datetime  # UTC

    @property
    def dedupe_key(self) -> str:
        return dedupe_key(self.title, self.published_at)


def clean(text: str | None, limit: int) -> str:
    t = html.unescape(_TAG.sub(" ", html.unescape(text or "")))
    return re.sub(r"\s+", " ", t).strip()[:limit]


def normalise_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"[^\w]+", " ", t).strip()


def dedupe_key(title: str, published_at: datetime) -> str:
    day = published_at.astimezone(IST).date().isoformat()
    return hashlib.sha256(f"{normalise_title(title)}|{day}".encode()).hexdigest()[:32]


def _date(s: str | None) -> datetime | None:
    if not s or not s.strip():
        return None
    try:
        d = parsedate_to_datetime(s.strip())
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.strip())
        except ValueError:
            return None
    return d.astimezone(UTC) if d.tzinfo else None  # a time without a zone cannot be placed on an IST day


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _first(f: dict[str, Element], *names: str) -> str | None:
    """Text of the first of `names` that is present."""
    return next((f[k].text for k in names if k in f), None)


def _link(f: dict[str, Element]) -> str:
    """RSS has the URL as text, Atom as an `href` attribute."""
    el = f.get("link")
    if el is None:
        return ""
    return (el.get("href") or el.text or "").strip()


def parse_feed(body: bytes) -> tuple[list[NewsItem], int]:
    """Return the valid items and the number of rejected ones (no title, bad link, or no usable date)."""
    try:
        root = ElementTree.fromstring(body)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise FeedError(f"not a safe, well-formed XML feed: {exc}") from exc
    kind = _local(root.tag)
    if kind == "rss":
        entries = list(root.iter("item"))
    elif kind == "feed":
        entries = [e for e in root if _local(e.tag) == "entry"]
    else:
        raise FeedError(f"root element <{kind}> is neither rss nor feed")
    items: list[NewsItem] = []
    rejected = 0
    for e in entries:
        f = {_local(c.tag): c for c in e}
        title = clean(_first(f, "title"), TITLE_MAX)
        url = _link(f)
        when = _date(_first(f, "pubDate", "published", "updated"))
        if not title or when is None or not url.lower().startswith(("http://", "https://")):
            rejected += 1
            continue
        items.append(
            NewsItem(title, clean(_first(f, "description", "summary", "content"), SUMMARY_MAX), url, when)
        )
    return items, rejected
