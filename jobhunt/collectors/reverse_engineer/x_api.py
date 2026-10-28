"""Reverse-engineered X (Twitter) search client.
Captures hiring announcements, founder posts, and direct job links from X
without paying for the official $100+/mo X Enterprise API.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
import httpx

from jobhunt.fetch import Job


def _auto_load_env() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class XSearchClient:
    QUERY_ID = "f7SuGIm11eLbQs7c0K9X9w"
    X_GRAPHQL_ENDPOINT = f"https://x.com/i/api/graphql/{QUERY_ID}/SearchTimeline"
    # Public bearer token used by Twitter web client
    DEFAULT_BEARER = (
        "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
        "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
    )

    def __init__(self, auth_token: str | None = None, ct0: str | None = None, timeout: int = 20):
        _auto_load_env()
        self.auth_token = auth_token or os.getenv("X_AUTH_TOKEN", "")
        self.ct0 = ct0 or os.getenv("X_CT0", "")
        self.timeout = timeout

    def search_hiring(
        self,
        query: str = '(hiring OR "we are hiring") (sde OR "full stack" OR backend) bangalore',
        count: int = 30,
        days: int = 4,
    ) -> list[Job]:
        """Searches X for hiring tweets using reverse-engineered SearchTimeline with recency filtering."""
        if not self.auth_token or not self.ct0:
            # Return empty or fallback if session cookies are missing
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Authorization": f"Bearer {self.DEFAULT_BEARER}",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "x-csrf-token": self.ct0,
            "Referer": "https://x.com/search?q=hiring&src=typed_query&f=live",
            "Accept": "*/*",
        }
        cookies = {
            "ct0": self.ct0,
            "auth_token": self.auth_token,
        }

        variables = {
            "rawQuery": f"{query} -filter:replies",
            "count": min(count, 50),
            "querySource": "typed_query",
            "product": "Latest"
        }
        features = {
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "responsive_web_enhance_cards_enabled": False
        }

        try:
            with httpx.Client(http2=True, timeout=self.timeout) as client:
                res = client.post(
                    self.X_GRAPHQL_ENDPOINT,
                    json={
                        "variables": variables,
                        "features": features,
                        "queryId": self.QUERY_ID,
                    },
                    headers=headers,
                    cookies=cookies,
                )
                if res.status_code == 200:
                    return self.parse_search_timeline(res.json(), days=days)
        except Exception:
            pass

        return []

    @staticmethod
    def parse_search_timeline(data: dict[str, Any], days: int = 0) -> list[Job]:
        """Pure parser for X SearchTimeline GraphQL response with optional days filtering."""
        jobs: list[Job] = []
        instructions = (
            data.get("data", {})
            .get("search_by_raw_query", {})
            .get("search_timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )

        now = datetime.now(timezone.utc)
        cutoff_seconds = days * 86400 if days > 0 else 0

        for inst in instructions:
            entries = inst.get("entries", [])
            for entry in entries:
                content = entry.get("content", {})
                item_content = content.get("itemContent", {})
                tweet_results = item_content.get("tweet_results", {}).get("result", {})
                legacy = tweet_results.get("legacy") or tweet_results.get("tweet", {}).get("legacy", {})

                if not legacy:
                    continue

                # Filter by creation date if requested
                created_at_str = legacy.get("created_at")
                if cutoff_seconds > 0 and created_at_str:
                    try:
                        tweet_dt = parsedate_to_datetime(created_at_str)
                        if (now - tweet_dt).total_seconds() > cutoff_seconds:
                            continue
                    except Exception:
                        pass

                tweet_id = legacy.get("id_str", "")
                full_text = legacy.get("full_text", "")
                user_res = (
                    tweet_results.get("core", {}).get("user_results", {}).get("result", {})
                )
                user_core = user_res.get("core") or user_res.get("legacy") or {}
                user_name = user_core.get("name") or "X Recruiter"
                user_screen_name = user_core.get("screen_name") or ""

                # Extract external links from entities
                urls = legacy.get("entities", {}).get("urls", [])
                primary_url = urls[0].get("expanded_url") if urls else f"https://x.com/{user_screen_name}/status/{tweet_id}"

                # Infer role title
                title_match = re.search(r'\b(SDE|Software Engineer|Full[- ]?Stack|Backend|Frontend|Intern)\b[^\n\.\,]*', full_text, re.I)
                title = title_match.group(0).strip() if title_match else "Software Engineer (Hiring Post)"

                company_label = f"@{user_screen_name} ({user_name})" if user_screen_name else user_name

                jobs.append(Job(
                    job_id=f"x:{tweet_id}",
                    ats="x",
                    company=company_label,
                    title=title,
                    location="Bangalore / Remote",
                    url=primary_url,
                    description=full_text,
                    posted_at=created_at_str,
                ))

        return jobs
