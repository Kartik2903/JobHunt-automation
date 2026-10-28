"""Reverse-engineered LinkedIn Voyager API client.
Emulates authenticated browser sessions to fetch structured job postings directly
without paying for enterprise APIs or tripping frontend rate limits.
"""
from __future__ import annotations

import os
import re
from typing import Any
import requests
import httpx

from jobhunt.fetch import Job, strip_html


class LinkedInVoyagerClient:
    VOYAGER_BASE = "https://www.linkedin.com/voyager/api"
    GUEST_BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def __init__(self, li_at: str | None = None, jsessionid: str | None = None, timeout: int = 20):
        self.li_at = li_at or os.getenv("LINKEDIN_LI_AT", "")
        self.jsessionid = jsessionid or os.getenv("LINKEDIN_JSESSIONID", "").strip('"')
        self.timeout = timeout
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self) -> None:
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "Accept-Language": "en-US,en;q=0.9",
            "x-li-lang": "en_US",
            "x-restli-protocol-version": "2.0.0",
        })
        if self.jsessionid:
            self.session.headers["csrf-token"] = self.jsessionid
            self.session.cookies.set("JSESSIONID", f'"{self.jsessionid}"', domain=".www.linkedin.com")
        if self.li_at:
            self.session.cookies.set("li_at", self.li_at, domain=".www.linkedin.com")

    def search_jobs(self, keywords: str, location: str = "Bangalore", limit: int = 25, days: int = 4) -> list[Job]:
        """Searches for jobs on LinkedIn. Uses Voyager API if logged in, otherwise public guest feed with recency filtering."""
        if self.li_at:
            try:
                return self._search_voyager(keywords, location, limit, days=days)
            except Exception:
                # Fallback to guest search if Voyager fails
                pass
        return self._search_guest(keywords, location, limit, days=days)

    def search_hidden_market(
        self,
        roles: list[str] | None = None,
        industries: list[str] | None = None,
        locations: list[str] | None = None,
        days_window: int = 30,
        max_results_per_query: int = 10,
    ) -> list[Job]:
        """OSINT hidden job market scanner targeting hiring manager posts on LinkedIn."""
        from .linkedin_hidden_market import HiddenJobMarketScanner
        scanner = HiddenJobMarketScanner(
            roles=roles,
            industries=industries,
            locations=locations,
            days_window=days_window,
            timeout=self.timeout,
        )
        return scanner.scan(max_results_per_query=max_results_per_query)

    def _search_voyager(self, keywords: str, location: str, limit: int, days: int = 4) -> list[Job]:
        """Calls internal Voyager search endpoint."""
        url = f"{self.VOYAGER_BASE}/voyagerJobsDashJobCards"
        f_tpr = f"r{days * 86400}" if days > 0 else ""
        query_str = f"(keywords:{keywords},locationFallback:{location})"
        if f_tpr:
            query_str = f"(keywords:{keywords},locationFallback:{location},selectedFilters:(timePostedRange:List({f_tpr})))"
        params = {
            "decorationId": "com.linkedin.voyager.dash.deco.jobs.search.JobSearchCardsCollection-216",
            "count": str(min(limit, 50)),
            "q": "jobSearch",
            "query": query_str,
        }
        res = self.session.get(url, params=params, timeout=self.timeout)
        res.raise_for_status()
        data = res.json()
        return self.parse_voyager_response(data)

    def _search_guest(self, keywords: str, location: str, limit: int, days: int = 4) -> list[Job]:
        """Calls LinkedIn's public guest search endpoint using HTTP/2 to bypass rate-limiting bot challenges."""
        all_jobs: list[Job] = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        f_tpr = f"r{days * 86400}" if days > 0 else None

        try:
            with httpx.Client(http2=True, timeout=self.timeout) as client:
                start = 0
                while len(all_jobs) < limit:
                    params = {
                        "keywords": keywords,
                        "location": location,
                        "start": str(start),
                    }
                    if f_tpr:
                        params["f_TPR"] = f_tpr

                    res = client.get(self.GUEST_BASE, params=params, headers=headers)
                    if res.status_code != 200:
                        break
                    batch = self.parse_guest_html(res.text)
                    if not batch:
                        break
                    all_jobs.extend(batch)
                    if len(batch) < 10 or len(all_jobs) >= limit:
                        break
                    start += len(batch)
        except Exception:
            pass
        return all_jobs[:limit]

    @staticmethod
    def parse_voyager_response(data: dict[str, Any]) -> list[Job]:
        """Pure parser for LinkedIn Voyager normalized JSON."""
        jobs: list[Job] = []
        elements = data.get("elements", [])
        included = {item.get("entityUrn", ""): item for item in data.get("included", [])}

        for elem in elements:
            card = elem.get("jobCard", {})
            urn = card.get("jobPostingUrn", "")
            raw_id = urn.split(":")[-1] if ":" in urn else card.get("trackingUrn", "")
            if not raw_id:
                continue

            title = card.get("title", {}).get("text", "")
            company = card.get("primaryDescription", {}).get("text", "")
            location = card.get("secondaryDescription", {}).get("text", "")
            job_url = f"https://www.linkedin.com/jobs/view/{raw_id}"

            # Check for expanded details in included items
            detail = included.get(urn, {})
            desc_raw = detail.get("description", {}).get("text") or card.get("insights", [{}])[0].get("text", "")

            jobs.append(Job(
                job_id=f"linkedin:{raw_id}",
                ats="linkedin",
                company=company or "LinkedIn Company",
                title=title or "Software Engineer",
                location=location or "Remote",
                url=job_url,
                description=strip_html(desc_raw) or f"{title} at {company}",
            ))

        return jobs

    @staticmethod
    def parse_guest_html(html_text: str) -> list[Job]:
        """Extracts job cards from guest HTML fragments."""
        jobs: list[Job] = []
        # Match each job card list item
        items = re.findall(r'<li[^>]*>(.*?)</li>', html_text, re.DOTALL)
        for item in items:
            link_m = re.search(r'href="https://[a-z\.]*linkedin\.com/jobs/view/(?:[a-zA-Z0-9\-_]+-)?([0-9]+)', item)
            title_m = re.search(r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3>', item, re.DOTALL)
            company_m = re.search(r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>(.*?)</h4>', item, re.DOTALL)
            location_m = re.search(r'<span[^>]*class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</span>', item, re.DOTALL)

            if link_m:
                job_id_num = link_m.group(1)
                title = strip_html(title_m.group(1)) if title_m else "Software Engineer"
                company = strip_html(company_m.group(1)) if company_m else "Unknown Company"
                location = strip_html(location_m.group(1)) if location_m else "Remote"
                url = f"https://www.linkedin.com/jobs/view/{job_id_num}"

                jobs.append(Job(
                    job_id=f"linkedin:{job_id_num}",
                    ats="linkedin",
                    company=company,
                    title=title,
                    location=location,
                    url=url,
                    description=f"{title} at {company} located in {location}.",
                ))

        return jobs
