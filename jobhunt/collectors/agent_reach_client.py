"""Agent Reach client wrapper inspired by Panniantong/agent-reach.
Provides multi-platform zero-key search across X/Twitter and LinkedIn for AI agents.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from jobhunt.fetch import Job


class AgentReachClient:
    """Wrapper around agent-reach CLI or direct search endpoints."""

    def __init__(self, cli_binary: str | None = None):
        self.cli_binary = cli_binary or shutil.which("agent-reach")

    def is_available(self) -> bool:
        return self.cli_binary is not None

    def search_platform(self, platform: str, query: str, limit: int = 10) -> list[Job]:
        """Searches a platform (e.g. 'twitter', 'linkedin') via agent-reach."""
        if not self.is_available():
            return []

        cmd = [self.cli_binary, "search", platform, query, "--limit", str(limit), "--json"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                return self.parse_agent_reach_output(data, platform)
        except Exception:
            pass
        return []

    @staticmethod
    def parse_agent_reach_output(items: list[dict[str, Any]], platform: str) -> list[Job]:
        """Converts Agent Reach standard search results into Job objects."""
        jobs: list[Job] = []
        for item in items:
            raw_id = str(item.get("id") or item.get("url", "").split("/")[-1])
            title = item.get("title") or item.get("text", "")[:60]
            company = item.get("author") or item.get("company") or platform.capitalize()
            url = item.get("url") or f"https://{platform}.com"
            desc = item.get("text") or item.get("description") or title

            jobs.append(Job(
                job_id=f"{platform}:{raw_id}",
                ats=platform,
                company=company,
                title=title,
                location=item.get("location", "Bangalore / Remote"),
                url=url,
                description=desc,
                posted_at=item.get("date"),
            ))
        return jobs
