"""
CITATION VERIFICATION TEMPLATE -- structured database lookup (CrossRef),
not a text search. Catches title/journal/year mismatches automatically.

WHAT THIS DOES NOT CHECK, READ THIS FIRST:
Volume and page numbers are NOT automatically verified -- this exact gap
once let a real page-range error through undetected on a "no mismatches
detected" result. Always manually diff the printed Volume/Page line
against your own citation log before trusting a clean result.

Setup:
    pip install requests --break-system-packages

Fill in CITATIONS below with your paper's real references, then:
    python citation_verification_check.py
"""

import requests
import time
from pathlib import Path

HEADERS = {"User-Agent": "CitationCheck/1.0 (mailto:you@example.com)"}

# Fill this in with your paper's actual citations.
# 'doi': known DOI -> exact lookup (reliable).
# 'query': no DOI on file -> bibliographic search (less reliable, verify
#          the match by eye, not just the pass/fail flag).
CITATIONS = [
    {
        "label": "Example Author 2020",
        "doi": "10.1000/example",
        "expect_title_contains": "some distinctive phrase from the real title",
        "expect_journal_contains": "Journal Name Fragment",
        "expect_year": 2020,
    },
    {
        "label": "Example No-DOI Citation",
        "doi": None,
        "query": "author lastname distinctive title words year",
        "expect_title_contains": "distinctive phrase",
        "expect_year": 2019,
    },
]


def _get_with_retry(url, params=None, max_retries=4):
    """GET with exponential backoff on HTTP 429. A flat delay isn't
    enough under load -- see TECH_NOTE.md Section 5."""
    delay = 3
    for attempt in range(max_retries):
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code == 429:
            if attempt < max_retries - 1:
                print(f"    (rate-limited, waiting {delay}s, retry {attempt+2}/{max_retries})")
                time.sleep(delay)
                delay *= 2
                continue
            return None, f"HTTP 429 after {max_retries} attempts"
        return r, None
    return None, "unreachable"


def check_by_doi(doi):
    r, err = _get_with_retry(f"https://api.crossref.org/works/{doi}")
    if err:
        return None, err
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} -- DOI may not resolve"
    return r.json()["message"], None


def check_by_search(query):
    r, err = _get_with_retry("https://api.crossref.org/works",
                              params={"query.bibliographic": query, "rows": 1})
    if err:
        return None, err
    items = r.json()["message"]["items"]
    if not items:
        return None, "No results found"
    return items[0], None


def summarize(data):
    title = (data.get("title") or ["(no title)"])[0]
    journal = (data.get("container-title") or ["(no journal)"])[0]
    year = None
    for field in ["published-print", "published-online", "created"]:
        if field in data and "date-parts" in data[field]:
            year = data[field]["date-parts"][0][0]
            break
    authors = data.get("author", [])
    names = [f"{a.get('given','')} {a.get('family','')}".strip() for a in authors[:3]]
    return {
        "title": title, "journal": journal, "year": year, "authors": names,
        "volume": data.get("volume", "?"), "page": data.get("page", "?"),
        "doi": data.get("DOI", "?"),
    }


def run():
    results = []
    for c in CITATIONS:
        print(f"Checking: {c['label']} ...")
        if c.get("doi"):
            data, err = check_by_doi(c["doi"])
            method = "exact DOI lookup"
        else:
            data, err = check_by_search(c["query"])
            method = "bibliographic search -- verify match by eye"

        if err:
            results.append({"label": c["label"], "method": method, "error": err})
            time.sleep(1)
            continue

        s = summarize(data)
        title_ok = c.get("expect_title_contains", "").lower() in s["title"].lower()
        year_ok = (c.get("expect_year") == s["year"]) if c.get("expect_year") else None
        journal_ok = (c["expect_journal_contains"].lower() in s["journal"].lower()
                      if c.get("expect_journal_contains") else None)
        results.append({"label": c["label"], "method": method, "summary": s,
                         "title_ok": title_ok, "year_ok": year_ok, "journal_ok": journal_ok})
        time.sleep(1)
    return results


def write_report(results):
    lines = ["# Citation Verification Results\n"]
    flagged = []
    for r in results:
        lines.append(f"## {r['label']}\nMethod: {r['method']}\n")
        if "error" in r:
            lines.append(f"**ERROR: {r['error']}**\n")
            flagged.append(r["label"])
            continue
        s = r["summary"]
        lines.append(f"- Title: {s['title']}\n- Journal: {s['journal']}\n- Year: {s['year']}")
        lines.append(f"- Authors: {', '.join(s['authors'])}")
        lines.append(f"- Volume/Page: {s['volume']} / {s['page']}  <-- NOT auto-checked, verify by eye")
        lines.append(f"- DOI: {s['doi']}\n")
        flags = [k for k, ok in [("TITLE", r["title_ok"]), ("YEAR", r["year_ok"]), ("JOURNAL", r["journal_ok"])] if ok is False]
        if flags:
            lines.append(f"**FLAGGED: {', '.join(flags)}**\n")
            flagged.append(r["label"])
        else:
            lines.append("No title/journal/year mismatches detected.\n")

    lines.insert(1, f"**{len(flagged)}/{len(results)} flagged: {', '.join(flagged) or 'none'}**\n"
                     f"**Reminder: volume/page are never auto-checked. Diff them yourself.**\n")
    Path("citation_check_results.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    write_report(run())
