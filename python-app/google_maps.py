#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Google Maps business scraper — Playwright edition.

Companion script to yellow_pages.py. Same philosophy, different mechanism:
Google Maps is a JS-rendered, scroll-to-load SPA — there's no static HTML
results page to GET like Yellow Pages has, so this drives a real (headless)
Chromium browser instead of faking HTTP requests.

What this mirrors from yellow_pages.py
---------------------------------------
* Dataclass-based row schema + CSV writer (same asdict/fields pattern).
* Retry-with-backoff instead of dying on the first hiccup.
* Defensive parsing — a missing field never crashes the run.
* Honest diagnostics — real failure reasons printed, not swallowed.
* Same CLI ergonomics: keyword, place, -p/--pages (mapped to scroll passes),
  -o/--output, -q/--quiet.

What's different (because Maps forces it)
-------------------------------------------
* Requires a rendered browser (Playwright + Chromium), not curl_cffi/requests.
* "Pages" doesn't exist on Maps — instead we scroll the results panel and
  stop when no new cards appear or --max-results is hit.
* Each business is opened (via click, not a separate URL fetch) to pull
  hours / category / place_id / plus code, since the list view only exposes
  name, rating, category snippet, and sometimes phone.

Setup (one-time)
-----------------
    pip install playwright
    playwright install chromium

Usage
-----
    python google_maps.py "plumbers" "Austin, TX" --max-results 60 -o plumbers.csv
"""

import argparse
import csv
import os
import random
import re
import sys
import time
from dataclasses import dataclass, asdict, fields
from urllib.parse import quote_plus

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("Missing dependency. Run:\n  pip install playwright\n  playwright install chromium")
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Row schema — superset of the YP Business dataclass, plus Maps-only fields.
# --------------------------------------------------------------------------- #
@dataclass
class MapsBusiness:
    rank: str = ""
    business_name: str = ""
    telephone: str = ""
    business_page: str = ""      # website, kept as same field name as YP script
    category: str = ""
    rating: str = ""
    review_count: str = ""
    street: str = ""
    locality: str = ""
    region: str = ""
    zipcode: str = ""
    hours: str = ""
    price_level: str = ""
    place_id: str = ""
    plus_code: str = ""
    maps_url: str = ""
    listing_url: str = ""        # the search URL this came from


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def looks_blocked(page_text):
    if not page_text:
        return True
    low = page_text.lower()
    signals = ["unusual traffic", "captcha", "before you continue",
               "our systems have detected"]
    return any(s in low for s in signals)


# --------------------------------------------------------------------------- #
# Scraper
# --------------------------------------------------------------------------- #
class MapsScraper:
    RESULTS_PANEL_SELECTOR = 'div[role="feed"]'
    CARD_SELECTOR = 'div[role="feed"] > div > div[jsaction]'

    def __init__(self, headless=True, max_retries=3, verbose=True, slow_mo=0):
        self.headless = headless
        self.max_retries = max_retries
        self.verbose = verbose
        self.slow_mo = slow_mo

    def log(self, *a):
        if self.verbose:
            print(*a)

    def build_url(self, keyword, place):
        query = quote_plus(f"{keyword} in {place}")
        return f"https://www.google.com/maps/search/{query}"

    def _human_pause(self, lo=0.6, hi=1.6):
        time.sleep(random.uniform(lo, hi))

    # -- scrolling / card collection ---------------------------------------- #
    def _scroll_and_collect(self, page, max_results, offset=0):
        """
        Scroll the results feed, collecting unique card hrefs as we go.

        Maps has no page/offset URL parameter -- the feed always starts at
        result #1 -- so "skipping" previously-scraped results means scrolling
        PAST them first. We keep scrolling until we've loaded offset +
        max_results cards, then slice off the first `offset` and only return
        the new window. This costs the same scroll time as before (we still
        have to load through position `offset`), it just avoids re-opening
        and re-parsing detail pages you already have.
        """
        seen_hrefs = []
        stagnant_rounds = 0
        max_stagnant = 4
        target = offset + max_results

        try:
            page.wait_for_selector(self.RESULTS_PANEL_SELECTOR, timeout=15000)
        except PWTimeout:
            return seen_hrefs  # no results feed rendered at all

        while len(seen_hrefs) < target and stagnant_rounds < max_stagnant:
            cards = page.query_selector_all('div[role="feed"] a[href*="/maps/place/"]')
            hrefs = []
            for c in cards:
                href = c.get_attribute("href")
                if href and href not in hrefs:
                    hrefs.append(href)

            new_count = len(set(hrefs) - set(seen_hrefs))
            seen_hrefs = list(dict.fromkeys(seen_hrefs + hrefs))

            if new_count == 0:
                stagnant_rounds += 1
            else:
                stagnant_rounds = 0

            self.log(f"  scrolled: {len(seen_hrefs)} unique listings so far "
                     f"(need {target}, offset={offset})")

            if len(seen_hrefs) >= target:
                break

            page.evaluate(
                f"""() => {{
                    const feed = document.querySelector('{self.RESULTS_PANEL_SELECTOR}');
                    if (feed) feed.scrollTop = feed.scrollHeight;
                }}"""
            )
            self._human_pause(0.8, 1.8)

        if offset:
            if len(seen_hrefs) <= offset:
                self.log(f"  offset ({offset}) is past the end of available "
                          f"results ({len(seen_hrefs)}) -- nothing new to collect.")
                return []
            self.log(f"  skipping first {offset} already-seen listings.")
        return seen_hrefs[offset:target]

    # -- per-listing extraction ---------------------------------------------- #
    def _extract_from_detail(self, page, href, rank, listing_url):
        for attempt in range(1, self.max_retries + 1):
            try:
                page.goto(href, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_selector("h1", timeout=10000)
                self._human_pause(0.4, 1.0)
                return self._parse_detail_dom(page, rank, listing_url, href)
            except PWTimeout:
                self.log(f"    timeout on detail page (attempt {attempt}/{self.max_retries})")
                if attempt < self.max_retries:
                    time.sleep(1.5 * attempt)
            except Exception as e:
                self.log(f"    detail page error: {e} (attempt {attempt}/{self.max_retries})")
                if attempt < self.max_retries:
                    time.sleep(1.5 * attempt)
        return None

    def _safe_text(self, page, selector):
        try:
            el = page.query_selector(selector)
            if el:
                return el.inner_text().strip()
        except Exception:
            pass
        return ""

    def _parse_detail_dom(self, page, rank, listing_url, href):
        name = self._safe_text(page, "h1")

        # Category + rating/review line usually sit near the top button row.
        category = ""
        rating = ""
        review_count = ""
        try:
            cat_el = page.query_selector('button[jsaction*="category"]')
            if cat_el:
                category = cat_el.inner_text().strip()
        except Exception:
            pass
        try:
            rating_el = page.query_selector('div[role="img"][aria-label*="star"]')
            if rating_el:
                label = rating_el.get_attribute("aria-label") or ""
                m = re.search(r"([\d.]+)\s*star", label)
                if m:
                    rating = m.group(1)
                m2 = re.search(r"([\d,]+)\s*review", label)
                if m2:
                    review_count = m2.group(1).replace(",", "")
        except Exception:
            pass

        # Address / phone / website / hours / plus code live in labelled
        # buttons — Google marks each with a data-item-id we can key off.
        street, locality, region, zipcode = "", "", "", ""
        telephone = ""
        website = ""
        hours = ""
        plus_code = ""

        info_buttons = page.query_selector_all('button[data-item-id], a[data-item-id]')
        for btn in info_buttons:
            item_id = (btn.get_attribute("data-item-id") or "").lower()
            text = ""
            try:
                text = btn.inner_text().strip()
            except Exception:
                pass
            if not text:
                continue
            if item_id.startswith("address"):
                street, locality, region, zipcode = self._split_address(text)
            elif item_id.startswith("phone"):
                telephone = text
            elif item_id.startswith("authority") or "website" in item_id:
                website = btn.get_attribute("href") or text
            elif "oloc" in item_id or "plus" in item_id:
                plus_code = text

        # Hours: Google renders a table row-by-row; grab it best-effort.
        try:
            hours_el = page.query_selector('div[aria-label*="Hours"], table')
            if hours_el:
                hours = " | ".join(
                    ln.strip() for ln in hours_el.inner_text().split("\n") if ln.strip()
                )[:500]
        except Exception:
            pass

        place_id = ""
        m = re.search(r"!1s([^!]+)", href)
        if m:
            place_id = m.group(1)

        return MapsBusiness(
            rank=str(rank), business_name=name, telephone=telephone,
            business_page=website, category=category, rating=rating,
            review_count=review_count, street=street, locality=locality,
            region=region, zipcode=zipcode, hours=hours, price_level="",
            place_id=place_id, plus_code=plus_code, maps_url=href,
            listing_url=listing_url,
        )

    def _split_address(self, text):
        """Best-effort split of a Maps address string. Never raises."""
        text = text.strip()
        m = re.search(r"(.*?),?\s*([A-Za-z .]+),\s*([A-Z]{2})\s*(\d{5})?$", text)
        if m:
            street = m.group(1).strip().rstrip(",")
            locality = m.group(2).strip()
            region = m.group(3).strip()
            zipcode = (m.group(4) or "").strip()
            return street, locality, region, zipcode
        return text, "", "", ""

    # -- public entrypoint ---------------------------------------------------- #
    def scrape(self, keyword, place, max_results=40, offset=0):
        listing_url = self.build_url(keyword, place)
        results = []

        with sync_playwright() as p:
            chromium_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            launch_kwargs = dict(
                headless=self.headless,
                slow_mo=self.slow_mo,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            if chromium_path:
                launch_kwargs["executable_path"] = chromium_path
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"),
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
            page = context.new_page()

            self.log(f"Opening: {listing_url}")
            try:
                page.goto(listing_url, timeout=25000, wait_until="domcontentloaded")
            except PWTimeout:
                self.log("  page load timed out — network issue or Google served a challenge.")
                browser.close()
                return results

            if looks_blocked(page.content()):
                self.log("  looks like a consent/CAPTCHA wall. Try again, or run non-headless "
                          "once to click through Google's consent dialog manually.")
                browser.close()
                return results

            self._human_pause(1.0, 2.0)

            hrefs = self._scroll_and_collect(page, max_results, offset=offset)
            self.log(f"Collected {len(hrefs)} listing links. Visiting each for details...")

            for i, href in enumerate(hrefs, start=offset + 1):
                self.log(f"[{i}/{offset + len(hrefs)}] {href[:90]}")
                row = self._extract_from_detail(page, href, i, listing_url)
                if row and row.business_name:
                    results.append(row)
                self._human_pause(0.5, 1.2)

            browser.close()

        return results


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def write_csv(rows, path):
    fieldnames = [f.name for f in fields(MapsBusiness)]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))


def main():
    ap = argparse.ArgumentParser(
        description="Scrape business listings from Google Maps (Playwright-driven).")
    ap.add_argument("keyword", help="search keyword, e.g. 'plumbers'")
    ap.add_argument("place", help="location, e.g. 'Austin, TX'")
    ap.add_argument("-r", "--max-results", type=int, default=40,
                     help="max number of listings to collect (default 40)")
    ap.add_argument("--offset", type=int, default=0,
                     help="number of results already scraped in a previous run "
                          "to skip past, so you don't re-visit the same "
                          "listings (default 0). Note: still has to scroll "
                          "through them, it just doesn't re-open/re-parse them.")
    ap.add_argument("-o", "--output", help="output CSV path")
    ap.add_argument("--retries", type=int, default=3,
                     help="max retries per detail page (default 3)")
    ap.add_argument("--headed", action="store_true",
                     help="show the browser window (useful for first run / debugging "
                          "consent walls)")
    ap.add_argument("--slow-mo", type=int, default=0,
                     help="ms delay between Playwright actions, for debugging")
    ap.add_argument("-q", "--quiet", action="store_true", help="less logging")
    args = ap.parse_args()

    scraper = MapsScraper(
        headless=not args.headed,
        max_retries=args.retries,
        verbose=not args.quiet,
        slow_mo=args.slow_mo,
    )

    rows = scraper.scrape(args.keyword, args.place, max_results=args.max_results,
                          offset=args.offset)

    if not rows:
        print("\nNo data scraped. Most likely, in order:")
        print("  1. Consent/CAPTCHA wall — rerun with --headed once and click through manually.")
        print("  2. Selectors changed — Google tweaks Maps' DOM periodically; re-inspect with --headed.")
        print("  3. Network/timeout — retry, or raise --retries.")
        sys.exit(1)

    out = args.output or (
        f"{args.keyword}-{args.place}-googlemaps.csv"
        .replace(" ", "_").replace(",", "")
    )
    write_csv(rows, out)
    print(f"\n\u2713 Wrote {len(rows)} listings to {out}")


if __name__ == "__main__":
    main()
