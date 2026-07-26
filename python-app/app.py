#!/usr/bin/env python
"""
Flask wrapper around yellow_pages.py / google_maps.py / email_scraper.py.

n8n calls these over HTTP (localhost, inside the same container) instead of
using the Execute Command node. Each endpoint maps JSON body fields to the
CLI flags the underlying script already supports.

Endpoints
---------
POST /yellowpages   {keyword, place, pages?, with_email?, min_delay?, max_delay?}
POST /googlemaps    {keyword, place, max_results?}
POST /email         {url, max_depth?, max_count?}

All three return JSON: {"ok": bool, "stdout": str, "stderr": str, "csv"/"emails": ...}
"""

import json
import os
import subprocess
import tempfile
import uuid

from flask import Flask, request, jsonify

app = Flask(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Hard ceiling so one bad request can't hang the container forever.
SUBPROCESS_TIMEOUT = 300  # seconds


def run_script(args, timeout=SUBPROCESS_TIMEOUT):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, cwd=SCRIPT_DIR
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Timed out after {timeout}s"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


@app.route("/yellowpages", methods=["POST"])
def yellowpages():
    data = request.get_json(force=True) or {}
    keyword = data.get("keyword")
    place = data.get("place")
    if not keyword or not place:
        return jsonify({"ok": False, "error": "keyword and place are required"}), 400

    out_name = f"yp-{uuid.uuid4().hex}.csv"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    args = ["python3", "yellow_pages.py", keyword, place, "-o", out_path, "-q"]
    if data.get("pages"):
        args += ["-p", str(int(data["pages"]))]
    if data.get("start_page"):
        args += ["--start-page", str(int(data["start_page"]))]
    if data.get("with_email"):
        args += ["--with-email"]
    if data.get("min_delay") is not None:
        args += ["--min-delay", str(data["min_delay"])]
    if data.get("max_delay") is not None:
        args += ["--max-delay", str(data["max_delay"])]
    if data.get("proxy"):
        args += ["--proxy", data["proxy"]]

    code, stdout, stderr = run_script(args)

    rows = []
    if os.path.exists(out_path):
        import csv
        with open(out_path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        os.remove(out_path)  # container is ephemeral anyway; don't accumulate files

    return jsonify({
        "ok": code == 0 and bool(rows),
        "returncode": code,
        "row_count": len(rows),
        "rows": rows,
        "stdout": stdout[-4000:],
        "stderr": stderr[-2000:],
    })


@app.route("/googlemaps", methods=["POST"])
def googlemaps():
    data = request.get_json(force=True) or {}
    keyword = data.get("keyword")
    place = data.get("place")
    if not keyword or not place:
        return jsonify({"ok": False, "error": "keyword and place are required"}), 400

    out_name = f"gm-{uuid.uuid4().hex}.csv"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    max_results = int(data.get("max_results", 20))  # keep default low on free tier
    offset = int(data.get("offset", 0))

    args = ["python3", "google_maps.py", keyword, place,
            "-r", str(max_results), "-o", out_path, "-q"]
    if offset:
        args += ["--offset", str(offset)]

    # Playwright + a headless browser is heavy; give it more time and let the
    # caller override if needed.
    code, stdout, stderr = run_script(args, timeout=data.get("timeout", 600))

    rows = []
    if os.path.exists(out_path):
        import csv
        with open(out_path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        os.remove(out_path)

    return jsonify({
        "ok": code == 0 and bool(rows),
        "returncode": code,
        "row_count": len(rows),
        "rows": rows,
        "stdout": stdout[-4000:],
        "stderr": stderr[-2000:],
    })


@app.route("/email", methods=["POST"])
def email_scrape():
    data = request.get_json(force=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"ok": False, "error": "url is required"}), 400

    args = ["python3", "email_scraper.py", url, "--json", "-q"]
    if data.get("max_depth") is not None:
        args += ["-d", str(int(data["max_depth"]))]
    if data.get("max_count") is not None:
        args += ["-c", str(int(data["max_count"]))]
    if data.get("analyze_pages_limit") is not None:
        args += ["--analyze-pages-limit", str(int(data["analyze_pages_limit"]))]
    if data.get("all_domains"):
        args += ["--all-domains"]
    if data.get("proxy"):
        args += ["--proxy", data["proxy"]]
    if data.get("min_delay") is not None:
        args += ["--min-delay", str(data["min_delay"])]
    if data.get("max_delay") is not None:
        args += ["--max-delay", str(data["max_delay"])]
    if data.get("retries") is not None:
        args += ["--retries", str(int(data["retries"]))]

    code, stdout, stderr = run_script(args, timeout=data.get("timeout", SUBPROCESS_TIMEOUT))

    parsed = None
    if code == 0 and stdout.strip():
        try:
            # --json prints one JSON line; be tolerant of any stray output before it
            last_line = stdout.strip().splitlines()[-1]
            parsed = json.loads(last_line)
        except (json.JSONDecodeError, IndexError):
            parsed = None

    emails = parsed.get("emails", {}) if parsed else {}
    return jsonify({
        "ok": code == 0 and parsed is not None,
        "returncode": code,
        "emails": list(emails.keys()),          # flat list for easy downstream use
        "email_sources": emails,                # email -> [pages it was found on]
        "email_count": len(emails),
        "intelligence": parsed.get("intelligence", {}) if parsed else {},
        "pages_scraped": parsed.get("pages_scraped", 0) if parsed else 0,
        "scrape_errors": parsed.get("errors", []) if parsed else [],
        "stdout": stdout[-4000:],
        "stderr": stderr[-2000:],
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
