"""HAR Analyzer inspired by kalil0321/reverse-api-engineer.
Extracts authenticated headers, cookies, and internal endpoint queries from browser HAR recordings.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs


@dataclass
class EndpointProfile:
    url: str
    method: str
    host: str
    path: str
    query_params: dict[str, list[str]]
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    response_status: int = 0
    sample_response_preview: str = ""


class HarAnalyzer:
    """Parses HAR files and reverse engineers authenticated internal APIs."""

    SIGNIFICANT_HEADERS = {
        "authorization",
        "csrf-token",
        "x-csrf-token",
        "x-li-pem-metadata",
        "x-li-track",
        "x-restli-protocol-version",
        "x-twitter-auth-type",
        "x-twitter-active-user",
        "x-client-transaction-id",
        "user-agent",
        "accept",
    }

    SIGNIFICANT_COOKIES = {
        "li_at",
        "JSESSIONID",
        "li_theme",
        "auth_token",
        "ct0",
        "twid",
    }

    def __init__(self, har_data: dict[str, Any] | None = None):
        self.har_data = har_data or {}

    @classmethod
    def from_file(cls, path: str | Path) -> HarAnalyzer:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(data)

    def find_endpoints(self, domain_pattern: str) -> list[EndpointProfile]:
        """Finds all requests targeting a specific domain (e.g. 'linkedin.com', 'x.com')."""
        profiles: list[EndpointProfile] = []
        entries = self.har_data.get("log", {}).get("entries", [])

        for entry in entries:
            req = entry.get("request", {})
            resp = entry.get("response", {})
            url = req.get("url", "")
            if domain_pattern.lower() not in url.lower():
                continue

            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)

            # Extract relevant headers
            headers: dict[str, str] = {}
            for h in req.get("headers", []):
                name = h.get("name", "").lower()
                if name in self.SIGNIFICANT_HEADERS or name.startswith("x-"):
                    headers[name] = h.get("value", "")

            # Extract relevant cookies
            cookies: dict[str, str] = {}
            for c in req.get("cookies", []):
                name = c.get("name", "")
                if name in self.SIGNIFICANT_COOKIES or "auth" in name.lower():
                    cookies[name] = c.get("value", "")

            preview = resp.get("content", {}).get("text", "")[:300] if resp.get("content") else ""

            profile = EndpointProfile(
                url=url,
                method=req.get("method", "GET"),
                host=parsed.netloc,
                path=parsed.path,
                query_params=query_params,
                headers=headers,
                cookies=cookies,
                response_status=resp.get("status", 0),
                sample_response_preview=preview,
            )
            profiles.append(profile)

        return profiles

    def extract_auth_bundle(self, domain: str) -> dict[str, Any]:
        """Extracts combined auth headers and cookies for a target domain."""
        endpoints = self.find_endpoints(domain)
        merged_headers: dict[str, str] = {}
        merged_cookies: dict[str, str] = {}

        for ep in endpoints:
            merged_headers.update(ep.headers)
            merged_cookies.update(ep.cookies)

        return {
            "domain": domain,
            "headers": merged_headers,
            "cookies": merged_cookies,
            "endpoints_found": len(endpoints),
        }
