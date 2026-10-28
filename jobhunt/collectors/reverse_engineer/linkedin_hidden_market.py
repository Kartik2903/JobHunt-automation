"""Hidden Job Market scanner — finds hiring-manager posts on LinkedIn.

Uses Google Search with `site:linkedin.com/posts` queries to surface posts
where founders, VPs, and hiring managers announce roles directly
("I'm hiring", "join my team", "DM me"), bypassing standard job portals.

Three search patterns are run:
  A – Direct Intent:  "I'm hiring" / "hiring a" / "looking for a"
  B – Call to Action:  "DM me" / "send me your resume" / "email me"
  C – Team Growth:     "excited to announce" / "growing the team"

All queries are date-windowed to the last 30 days via Google's `after:` operator.
"""
from __future__ import annotations

import hashlib
import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from jobhunt.fetch import Job, strip_html


# ---------------------------------------------------------------------------
# Configuration — Kartik's target parameters
# ---------------------------------------------------------------------------

DEFAULT_TARGET_ROLES = [
    "Full-Stack Engineer",
    "Backend Engineer",
    "SDE",
    "Software Engineer Intern",
    "Software Engineer",
]

DEFAULT_TARGET_INDUSTRIES = ["SaaS", "Fintech", "AI"]

DEFAULT_TARGET_LOCATIONS = [
    "Bangalore",
    "India",
    "Remote",
]


# ---------------------------------------------------------------------------
# Google Search scraper (lightweight, no API key needed)
# ---------------------------------------------------------------------------

_GOOGLE_SEARCH_URL = "https://www.google.com/search"

# Regex to extract URLs from Google result links
_GOOGLE_LINK_RE = re.compile(
    r'<a[^>]+href="(/url\?q=([^&"]+))"', re.I
)
_GOOGLE_RESULT_BLOCK_RE = re.compile(
    r'<div class="[^"]*tF2Cxc[^"]*">(.*?)</div>\s*(?=<div class="[^"]*tF2Cxc|$)',
    re.DOTALL,
)

# More robust extraction: look for linkedin.com/posts URLs in href attributes
_LINKEDIN_POST_URL_RE = re.compile(
    r'https?://(?:www\.)?linkedin\.com/posts/[a-zA-Z0-9_-]+-[a-zA-Z0-9_-]+',
)

# Extract text snippets from Google results
_SNIPPET_RE = re.compile(
    r'<(?:span|div) class="[^"]*(?:aCOpRe|VwiC3b|st)[^"]*"[^>]*>(.*?)</(?:span|div)>',
    re.DOTALL | re.I,
)

# Title extraction
_TITLE_RE = re.compile(
    r'<h3[^>]*>(.*?)</h3>',
    re.DOTALL | re.I,
)


class HiddenJobMarketScanner:
    """Scans Google for LinkedIn hiring-manager posts matching target parameters."""

    def __init__(
        self,
        roles: list[str] | None = None,
        industries: list[str] | None = None,
        locations: list[str] | None = None,
        days_window: int = 30,
        timeout: int = 15,
    ):
        self.roles = roles or DEFAULT_TARGET_ROLES
        self.industries = industries or DEFAULT_TARGET_INDUSTRIES
        self.locations = locations or DEFAULT_TARGET_LOCATIONS
        self.days_window = days_window
        self.timeout = timeout
        self._date_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_window)
        ).strftime("%Y-%m-%d")

    def build_queries(self) -> list[dict[str, str]]:
        """Generate all Google dork queries for the three OSINT patterns.

        Returns a list of dicts with 'query' and 'pattern' keys.
        """
        queries: list[dict[str, str]] = []

        for role in self.roles:
            # Pattern A: Direct Intent
            for industry in self.industries:
                q = (
                    f'site:linkedin.com/posts '
                    f'"{role}" '
                    f'("I\'m hiring" OR "hiring a" OR "looking for a") '
                    f'{industry} '
                    f'-intitle:jobs '
                    f'after:{self._date_cutoff}'
                )
                queries.append({"query": q, "pattern": "A-DirectIntent"})

            # Pattern B: Call to Action
            for location in self.locations:
                q = (
                    f'site:linkedin.com/posts '
                    f'"{role}" '
                    f'("DM me" OR "send me your resume" OR "email me") '
                    f'"hiring" '
                    f'{location} '
                    f'-intitle:jobs '
                    f'after:{self._date_cutoff}'
                )
                queries.append({"query": q, "pattern": "B-CallToAction"})

            # Pattern C: Team Growth
            q = (
                f'site:linkedin.com/posts '
                f'"excited to announce" "growing the team" '
                f'"{role}" '
                f'-job -recruiter '
                f'after:{self._date_cutoff}'
            )
            queries.append({"query": q, "pattern": "C-TeamGrowth"})

        return queries

    def search_google(self, query: str, num_results: int = 10) -> list[dict[str, str]]:
        """Perform a Google search and extract LinkedIn post URLs + snippets.

        Returns list of dicts with keys: url, title, snippet.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        params = {
            "q": query,
            "num": str(num_results),
            "hl": "en",
        }

        results: list[dict[str, str]] = []
        try:
            with httpx.Client(http2=True, timeout=self.timeout, follow_redirects=True) as client:
                res = client.get(_GOOGLE_SEARCH_URL, params=params, headers=headers)
                if res.status_code != 200:
                    return results
                html_text = res.text
        except Exception:
            return results

        # Strategy: find all linkedin.com/posts URLs in the page
        seen_urls: set[str] = set()

        # Method 1: Extract from /url?q= redirect links
        for match in _GOOGLE_LINK_RE.finditer(html_text):
            raw_url = urllib.parse.unquote(match.group(2))
            if "linkedin.com/posts/" in raw_url:
                # Clean URL (remove trailing Google tracking params)
                clean_url = raw_url.split("&sa=")[0].split("&ved=")[0]
                if clean_url not in seen_urls:
                    seen_urls.add(clean_url)

        # Method 2: Direct regex for linkedin post URLs anywhere in page
        for match in _LINKEDIN_POST_URL_RE.finditer(html_text):
            url = match.group(0)
            if url not in seen_urls:
                seen_urls.add(url)

        # Extract titles and snippets around those URLs
        for url in seen_urls:
            # Find the context around this URL in the HTML
            idx = html_text.find(url)
            if idx == -1:
                results.append({"url": url, "title": "", "snippet": ""})
                continue

            # Get a window around the URL to extract title/snippet
            window_start = max(0, idx - 2000)
            window_end = min(len(html_text), idx + 2000)
            window = html_text[window_start:window_end]

            title = ""
            snippet = ""

            title_m = _TITLE_RE.search(window)
            if title_m:
                title = strip_html(title_m.group(1))

            snippet_m = _SNIPPET_RE.search(window)
            if snippet_m:
                snippet = strip_html(snippet_m.group(1))

            results.append({"url": url, "title": title, "snippet": snippet})

        return results

    def scan(self, max_results_per_query: int = 10) -> list[Job]:
        """Run all query patterns and return deduplicated Job objects.

        This is the main entry point. It builds all queries, runs them through
        Google, deduplicates by URL, and converts results to Job objects that
        plug directly into the jobhunt pipeline.
        """
        raw_results = self.scan_raw(max_results_per_query=max_results_per_query)
        return self._to_jobs(raw_results)

    def scan_raw(self, max_results_per_query: int = 10) -> list[dict[str, Any]]:
        """Run all query patterns and return raw search result dictionaries."""
        queries = self.build_queries()
        all_results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for q_info in queries:
            try:
                results = self.search_google(q_info["query"], num_results=max_results_per_query)
                for r in results:
                    url = r["url"]
                    if url not in seen_urls:
                        seen_urls.add(url)
                        r["pattern"] = q_info["pattern"]
                        all_results.append(r)
            except Exception:
                continue

        return all_results

    @staticmethod
    def format_markdown_table(items: list[Job] | list[dict[str, Any]]) -> str:
        """Format scan results into the requested Markdown Table format:

        | Hiring Manager / Company | Role & Context | **Direct Link to Post** |
        | :--- | :--- | :--- |
        | Name (if visible) & Company | Brief snippet of what they are looking for | [Click Here](URL) |
        """
        headers = [
            "| Hiring Manager / Company | Role & Context | **Direct Link to Post** |",
            "| :--- | :--- | :--- |",
        ]
        rows: list[str] = []

        for item in items:
            if isinstance(item, Job):
                company = item.company.replace("|", "-").strip()
                title_context = f"{item.title} — {item.description[:120]}...".replace("|", "-").replace("\n", " ").strip()
                url = item.url
            else:
                url = item.get("url", "")
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                name = _extract_name_and_company(title, url)
                company = name.replace("|", "-").strip()
                context = (snippet or title or "Looking for candidates").replace("|", "-").replace("\n", " ")
                if len(context) > 140:
                    context = context[:137] + "..."
                title_context = context.strip()

            rows.append(f"| {company} | {title_context} | [Click Here]({url}) |")

        if not rows:
            return "\n".join(headers + ["| None found | No recent posts found matching parameters | N/A |"])

        return "\n".join(headers + rows)

    @staticmethod
    def _to_jobs(results: list[dict[str, Any]]) -> list[Job]:
        """Convert raw search results to Job dataclass instances."""
        jobs: list[Job] = []

        for r in results:
            url = r.get("url", "")
            title_raw = r.get("title", "")
            snippet = r.get("snippet", "")
            pattern = r.get("pattern", "")

            # Generate a stable ID from the URL
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

            poster = _extract_name_and_company(title_raw, url)

            # Try to extract role from title or snippet
            role_title = _extract_role(title_raw + " " + snippet)
            if not role_title:
                role_title = "Software Engineer (Hiring Post)"

            # Build description from available data
            desc_parts = []
            if pattern:
                desc_parts.append(f"[Hidden Market — {pattern}]")
            if snippet:
                desc_parts.append(snippet)
            if title_raw and title_raw != snippet:
                desc_parts.append(f"Post: {title_raw}")
            description = "\n".join(desc_parts) or f"Hiring post by {poster or 'unknown'}"

            company = poster or "LinkedIn Hiring Post"

            jobs.append(Job(
                job_id=f"linkedin-hidden:{url_hash}",
                ats="linkedin-hidden",
                company=company,
                title=role_title,
                location="India / Remote",
                url=url,
                description=description,
            ))

        return jobs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROLE_PATTERNS = [
    re.compile(
        r'\b((?:Senior |Junior |Staff |Lead |Principal )?'
        r'(?:Full[- ]?Stack|Backend|Back-End|Frontend|Front-End|Platform|Infrastructure|Cloud|DevOps|Site Reliability|SRE|Data|ML|AI)\s+'
        r'(?:Engineer|Developer|SDE)(?:\s+(?:I{1,3}|[1-3]))?)\b',
        re.I,
    ),
    re.compile(r'\b(SDE\s*-?\s*(?:I{1,3}|[1-3]))\b', re.I),
    re.compile(r'\b(Software\s+(?:Development\s+)?Engineer(?:\s+(?:I{1,3}|[1-3]|Intern))?)\b', re.I),
    re.compile(r'\b((?:Full[- ]?Stack|Backend|Frontend)\s+(?:Engineer|Developer))\b', re.I),
    re.compile(r'\b(Software\s+Engineer\s+Intern)\b', re.I),
]


def _extract_name_and_company(title: str, url: str) -> str:
    """Extract hiring manager name or company from title and LinkedIn post URL."""
    # 1. From Google search title: e.g. "Sarah Connor on LinkedIn: I'm hiring..."
    if " on LinkedIn" in title:
        name_part = title.split(" on LinkedIn")[0].strip()
        if name_part and len(name_part) < 60:
            return name_part

    # 2. From URL: e.g. linkedin.com/posts/sarah-connor_im-hiring...
    slug_match = re.search(r'linkedin\.com/posts/([a-zA-Z0-9\-]+?)(?:_|-activity|/|\?|$)', url)
    if slug_match:
        name = slug_match.group(1).replace("-", " ").strip().title()
        if name and len(name) > 2:
            return name

    return "Hiring Manager"


def _extract_role(text: str) -> str:
    """Try to extract a job-role title from free-form text."""
    for pat in _ROLE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return ""
