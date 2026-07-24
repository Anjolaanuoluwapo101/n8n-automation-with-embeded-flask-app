#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Yellow Pages scraper — hardened edition.

What changed vs. the original
-----------------------------
* TLS-fingerprint impersonation via curl_cffi — the single most effective way to
  get past modern bot detection (Cloudflare/PerimeterX fingerprint the TLS/JA3
  handshake; plain `requests` has a Python-shaped fingerprint that's trivially
  flagged).
* Automatic backend fallback chain: curl_cffi -> cloudscraper -> requests, so it
  still runs even if the fancy libraries aren't installed.
* Clean proxy support — a single proxy (--proxy) or a rotating pool (--proxy-file).
  A fresh proxy is picked on every retry, so one dead proxy doesn't kill the run.
* Realistic browser headers, a stable per-session User-Agent, a plausible Referer,
  and randomised human-like delays between pages.
* Exponential backoff with jitter that ACTUALLY retries. (The original had a retry
  loop but `return []` inside the `except`, so it bailed on the very first error.)
* Defensive parsing that never crashes on missing/odd fields. (The original's
  `locality.split(',')` / `.split(' ')` threw constantly and got swallowed by a
  bare `except:` as the useless message "Failed to process page".)
* Multi-page (pagination) scraping.
* Honest diagnostics: prints real status codes and detects block/CAPTCHA pages
  instead of hiding every failure behind one generic string.

IMPORTANT — about being blocked in Nigeria
-------------------------------------------
yellowpages.com serves **US traffic only**. If the request leaves an IP that YP
doesn't like (non-US, or a known datacenter range) you get blocked no matter how
good this code is — that's an IP-level decision made before your HTML ever loads.
No Python trick changes that. Two ways to fix it:

  1. Give the script a US IP:   --proxy 123.45.67.89:8080
     (or a whole list:          --proxy-file proxies.txt)
  2. Run the script from a US machine. Google Colab is free and runs in the US,
     so it works there with no proxy at all.

Recommended install for best results:
    pip install curl_cffi cloudscraper lxml requests
"""

import random
import argparse
import csv
import random
import re
import sys
import time

from fake_useragent import UserAgent
from dataclasses import dataclass, asdict, fields
from urllib.parse import quote_plus

from lxml import html

# --------------------------------------------------------------------------- #
# Optional HTTP backends. All optional; we degrade gracefully if missing.
# --------------------------------------------------------------------------- #
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

import requests as py_requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:  # very old urllib3
    Retry = None
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# --------------------------------------------------------------------------- #
# Headers / fingerprints
# --------------------------------------------------------------------------- #
REFERER = "https://www.yellowpages.com/"

_FALLBACK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def random_user_agent():
    """A realistic, up-to-date desktop Chrome UA via fake_useragent, falling
    back to a static string if the library's data source is unreachable."""
    try:
        return UserAgent(browsers=["Chrome"], os=["Windows", "Mac OS X", "Linux"]).random
    except Exception:
        return _FALLBACK_UA


def full_headers(ua):
    """Full, browser-accurate headers for the plain-requests / cloudscraper path."""
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,image/apng,*/*;q=0.8",
        # NOTE: deliberately no 'br' here — requests only decodes brotli if the
        # brotli package is installed, and undecoded br looks like garbage.
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "Referer": REFERER,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", '
                     '"Not.A/Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def minimal_headers():
    """Light headers for curl_cffi — impersonate=... supplies accurate UA + client
    hints itself, so we only add what won't conflict."""
    return {"Accept-Language": "en-US,en;q=0.9", "Referer": REFERER}


# Pages that came back 200 but are actually a wall, not results.
BLOCK_SIGNATURES = [
    "access denied", "unusual traffic", "verify you are a human", "captcha",
    "cf-chl", "cloudflare", "px-captcha", "please enable javascript",
    "requests from your network", "temporarily blocked", "are you a robot",
]


def looks_blocked(text):
    if not text:
        return True
    low = text.lower()
    # A genuine results page contains the listing containers; a wall doesn't.
    if "search-results" in low or "business-name" in low or "v-card" in low:
        return False
    return any(sig in low for sig in BLOCK_SIGNATURES)


# --------------------------------------------------------------------------- #
# Fetcher — tries each backend, retries with backoff, rotates proxies
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, proxies=None, delay=(2.0, 5.0), max_retries=4, verbose=True):
        self.proxy_pool = proxies or []
        self.delay = delay
        self.max_retries = max_retries
        self.verbose = verbose
        self.ua = random_user_agent()                 # stable within a session
        self._session = self._build_session()
        self._scraper = cloudscraper.create_scraper() if HAS_CLOUDSCRAPER else None

    # -- helpers ----------------------------------------------------------- #
    def log(self, *a):
        if self.verbose:
            print(*a)

    def _build_session(self):
        s = py_requests.Session()
        if Retry is not None:
            retry = Retry(
                total=2, backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET"]),
            )
            adapter = HTTPAdapter(max_retries=retry)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
        return s

    def _pick_proxy(self):
        if not self.proxy_pool:
            return None
        p = random.choice(self.proxy_pool)
        return {"http": p, "https": p}

    def polite_sleep(self):
        lo, hi = self.delay
        t = random.uniform(lo, hi)
        self.log(f"  sleeping {t:.1f}s")
        time.sleep(t)

    # -- individual backends ---------------------------------------------- #
    def _via_curl_cffi(self, url, proxy):
        # Try the "latest chrome" alias first, then specific known builds, then
        # no impersonation — keeps working across curl_cffi versions.
        last = None
        for target in ("chrome", "chrome124", "chrome120", None):
            try:
                kw = dict(headers=minimal_headers(), proxies=proxy, timeout=25)
                if target:
                    kw["impersonate"] = target
                r = cffi_requests.get(url, **kw)
                return r.text, r.status_code
            except Exception as e:      # bad impersonate string, network, etc.
                last = e
                continue
        raise last if last else RuntimeError("curl_cffi failed")

    def _via_cloudscraper(self, url, proxy):
        r = self._scraper.get(url, headers=minimal_headers(),
                              proxies=proxy, timeout=25)
        return r.text, r.status_code

    def _via_requests(self, url, proxy):
        headers = full_headers(self.ua)
        try:
            r = self._session.get(url, headers=headers, proxies=proxy,
                                  timeout=25, verify=True)
        except py_requests.exceptions.SSLError:
            r = self._session.get(url, headers=headers, proxies=proxy,
                                  timeout=25, verify=False)
        return r.text, r.status_code

    def _try_backends(self, url, proxy):
        """Return (text, status). Tries best -> worst; logs each failure."""
        if HAS_CURL_CFFI:
            try:
                return self._via_curl_cffi(url, proxy)
            except Exception as e:
                self.log(f"    curl_cffi error: {e}")
        if self._scraper is not None:
            try:
                return self._via_cloudscraper(url, proxy)
            except Exception as e:
                self.log(f"    cloudscraper error: {e}")
        try:
            return self._via_requests(url, proxy)
        except Exception as e:
            self.log(f"    requests error: {e}")
        return None, None

    # -- public API -------------------------------------------------------- #
    def get(self, url):
        last_status = None
        for attempt in range(1, self.max_retries + 1):
            proxy = self._pick_proxy()
            where = f" via {list(proxy.values())[0]}" if proxy else ""
            self.log(f"[attempt {attempt}/{self.max_retries}] GET {url}{where}")

            text, status = self._try_backends(url, proxy)
            last_status = status

            if status == 200 and text and not looks_blocked(text):
                self.log(f"  OK ({len(text):,} bytes)")
                return text
            if status == 404:
                self.log("  404 — no such location")
                return None
            if status == 200 and looks_blocked(text):
                self.log("  200 but the page is a block/CAPTCHA wall")
            elif status:
                self.log(f"  status {status}")
            else:
                self.log("  request failed (timeout / connection / proxy)")

            if attempt < self.max_retries:
                backoff = min(2 ** attempt + random.uniform(0, 1.5), 30)
                self.log(f"  backing off {backoff:.1f}s")
                time.sleep(backoff)

        self.log(f"  giving up on this URL (last status: {last_status})")
        return None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
@dataclass
class Business:
    rank: str = ""
    business_name: str = ""
    telephone: str = ""
    business_page: str = ""
    category: str = ""
    website: str = ""
    rating: str = ""
    street: str = ""
    locality: str = ""
    region: str = ""
    zipcode: str = ""
    listing_url: str = ""
    email: str = ""


def _cls(name):
    """XPath fragment matching a whole class token (so 'result' doesn't also
    match 'search-results')."""
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"


def _first(node, xpaths):
    """Joined text from the first XPath that yields anything; '' otherwise."""
    for xp in xpaths:
        vals = node.xpath(xp)
        if vals:
            parts = [v.strip() for v in vals if isinstance(v, str) and v.strip()]
            if parts:
                return " ".join(parts)
    return ""


def parse_locality(raw):
    """'Boston, MA 02101' -> ('Boston', 'MA', '02101'). Tolerant of junk / NBSP /
    missing pieces. Never raises."""
    if not raw:
        return "", "", ""
    text = raw.replace("\xa0", " ").replace(",", ", ")
    text = re.sub(r"\s+", " ", text).strip().strip(",").strip()
    m = re.search(r"(.*?),?\s*\b([A-Z]{2})\b\s*(\d{5}(?:-\d{4})?)", text)
    if m:
        return m.group(1).strip().rstrip(","), m.group(2), m.group(3)
    m = re.search(r"(.*?),?\s*\b([A-Z]{2})\b", text)
    if m:
        return m.group(1).strip().rstrip(","), m.group(2), ""
    return text, "", ""


def extract_email(detail_html):
    """Best-effort email pulled from a business detail page: mailto: links or
    schema.org itemprop=email. Most YP business pages don't expose one —
    contact is usually gated behind a JS 'Email This Business' form."""
    doc = html.fromstring(detail_html)
    for xp in (".//a[starts-with(@href,'mailto:')]/@href",
               ".//*[@itemprop='email']/text()"):
        vals = doc.xpath(xp)
        if vals:
            v = vals[0].replace("mailto:", "").split("?")[0].strip()
            if v:
                return v
    return ""


def parse_page(page_html, listing_url):
    doc = html.fromstring(page_html)
    doc.make_links_absolute("https://www.yellowpages.com")

    cards = doc.xpath(
        f"//div[{_cls('search-results')} and {_cls('organic')}]"
        f"//div[{_cls('result')} or {_cls('v-card')}]"
    )
    if not cards:  # looser fallback if the wrapper markup changed
        cards = doc.xpath(f"//div[{_cls('result')} or {_cls('v-card')}]")

    results = []
    for card in cards:
        name = _first(card, [
            ".//a[contains(@class,'business-name')]//text()",
            ".//a[contains(@class,'business-name')]/span/text()",
        ])
        if not name:
            continue  # skip ads / empty shells

        rank = _first(card, [
            ".//h2[contains(@class,'n')]/text()",
            ".//*[contains(@class,'listing-number')]/text()",
        ])
        rank = re.sub(r"[.\u00a0]", "", rank).strip()

        phone = _first(card, [
            f".//div[{_cls('phones')} and {_cls('phone')}]//text()",
            ".//div[contains(@class,'phone')]//text()",
            ".//a[contains(@href,'tel:')]/@href",
        ]).replace("tel:", "")

        page = _first(card, [".//a[contains(@class,'business-name')]/@href"])

        category = _first(card, [
            ".//div[contains(@class,'categories')]//text()",
            ".//div[contains(@class,'categories')]//a/text()",
        ])
        if category:
            cats = [c.strip() for c in re.split(r"[,\n]", category) if c.strip()]
            category = ", ".join(dict.fromkeys(cats))  # de-dup, keep order

        website = _first(card, [
            ".//a[contains(@class,'track-visit-website')]/@href",
            f".//div[{_cls('links')}]//a[contains(@class,'website')]/@href",
            ".//a[contains(@class,'website')]/@href",
        ])

        rating = _first(card, [
            ".//div[contains(@class,'result-rating')]//span[contains(@class,'count')]/text()",
            ".//span[contains(@class,'count')]/text()",
        ])
        rating = rating.replace("(", "").replace(")", "").strip()

        street = _first(card, [
            ".//div[contains(@class,'street-address')]//text()",
            ".//span[@itemprop='streetAddress']/text()",
        ])

        raw_locality = _first(card, [
            ".//div[contains(@class,'locality')]//text()",
            ".//p[@itemprop='address']//text()",
        ])
        locality, region, zipcode = parse_locality(raw_locality)

        results.append(Business(
            rank=rank, business_name=name, telephone=phone, business_page=page,
            category=category, website=website, rating=rating, street=street,
            locality=locality, region=region, zipcode=zipcode,
            listing_url=listing_url,
        ))
    return results


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_url(keyword, place, page):
    url = ("https://www.yellowpages.com/search?"
           f"search_terms={quote_plus(keyword)}"
           f"&geo_location_terms={quote_plus(place)}")
    if page > 1:
        url += f"&page={page}"
    return url


def scrape(keyword, place, pages=1, fetcher=None, with_email=False, start_page=1):
    """
    start_page lets you continue a previous run without re-scraping pages you
    already have. `pages` is still the COUNT of pages to fetch this run, e.g.
    start_page=2, pages=3 -> fetches pages 2, 3, 4.
    """
    fetcher = fetcher or Fetcher()
    all_rows = []
    last_page = start_page + pages - 1
    for page in range(start_page, last_page + 1):
        url = build_url(keyword, place, page)
        page_html = fetcher.get(url)
        if not page_html:
            print(f"Page {page}: no data returned.")
            if page == start_page:      # first page of THIS run failed -> rest will too
                break
            continue
        rows = parse_page(page_html, url)
        print(f"Page {page}: {len(rows)} listings.")
        if not rows:
            break              # ran out of results or markup changed
        if with_email:
            for row in rows:
                if not row.business_page:
                    continue
                detail_html = fetcher.get(row.business_page)
                if detail_html:
                    row.email = extract_email(detail_html)
                fetcher.polite_sleep()
        all_rows.extend(rows)
        if page < last_page:
            fetcher.polite_sleep()
    return all_rows


def write_csv(rows, path):
    fieldnames = [f.name for f in fields(Business)]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))


def load_proxies(args):
    proxies = []
    if args.proxy:
        p = args.proxy
        proxies.append(p if "://" in p else f"http://{p}")
    if args.proxy_file:
        with open(args.proxy_file) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxies.append(line if "://" in line else f"http://{line}")
    return proxies


def main():
    ap = argparse.ArgumentParser(
        description="Scrape business listings from YellowPages.com")
    ap.add_argument("keyword", help="search keyword, e.g. restaurants")
    ap.add_argument("place", help="location, e.g. 'Boston, MA'")
    ap.add_argument("-p", "--pages", type=int, default=1,
                    help="number of result pages to scrape (default 1)")
    ap.add_argument("--start-page", type=int, default=1,
                    help="page number to start from, for continuing a previous "
                         "run without re-scraping pages you already have "
                         "(default 1)")
    ap.add_argument("-o", "--output", help="output CSV path")
    ap.add_argument("--proxy",
                    help="single proxy: 'ip:port' or 'http://user:pass@ip:port'")
    ap.add_argument("--proxy-file", help="text file, one proxy per line")
    ap.add_argument("--min-delay", type=float, default=2.0,
                    help="min seconds between pages (default 2)")
    ap.add_argument("--max-delay", type=float, default=5.0,
                    help="max seconds between pages (default 5)")
    ap.add_argument("--retries", type=int, default=4,
                    help="max retries per page (default 4)")
    ap.add_argument("-q", "--quiet", action="store_true", help="less logging")
    ap.add_argument("--with-email", action="store_true",
                    help="visit each business's detail page and try to pull an "
                         "email (mailto:/schema.org). Doubles requests; most "
                         "listings won't have one since YP gates contact behind "
                         "a form.")
    args = ap.parse_args()

    backends = []
    if HAS_CURL_CFFI:
        backends.append("curl_cffi (TLS impersonation)")
    if HAS_CLOUDSCRAPER:
        backends.append("cloudscraper")
    backends.append("requests")
    print("HTTP backends:", ", ".join(backends))
    if not HAS_CURL_CFFI:
        print("  TIP: `pip install curl_cffi` for far better anti-detection.")

    proxies = load_proxies(args)
    if proxies:
        print(f"Proxies loaded: {len(proxies)}")
    else:
        print("Proxies loaded: 0  —  reminder: YP is US-only. If you're outside "
              "the US, pass --proxy with a US IP or run this on Colab.")

    fetcher = Fetcher(proxies=proxies,
                      delay=(args.min_delay, args.max_delay),
                      max_retries=args.retries,
                      verbose=not args.quiet)

    if args.with_email:
        print("--with-email: fetching each business's detail page too — most "
              "won't expose one since YP gates contact behind a form.")

    rows = scrape(args.keyword, args.place, pages=args.pages, fetcher=fetcher,
                  with_email=args.with_email, start_page=args.start_page)

    if not rows:
        print("\nNo data scraped. Most likely, in order:")
        print("  1. Geo-block — your IP isn't US. Use --proxy <US IP> or run on Colab.")
        print("  2. CAPTCHA/challenge — retry, raise --min-delay, use a residential proxy.")
        print("  3. Markup changed — inspect the saved HTML and update the XPaths.")
        sys.exit(1)

    out = args.output or (
        f"{args.keyword}-{args.place}-yellowpages.csv"
        .replace(" ", "_").replace(",", "")
    )
    write_csv(rows, out)
    print(f"\n\u2713 Wrote {len(rows)} listings to {out}")


if __name__ == "__main__":
    main()
