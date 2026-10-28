"""Application submitter for ATS and platforms.
Handles form field mapping, resume attachments, and submission requests with strict dry-run guards.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import requests


class ApplicationSubmitter:
    """Manages programmatic application submissions with safety gates."""

    def __init__(self, profile: dict[str, Any] | None = None, resume_path: str | Path = "resume.pdf"):
        self.profile = profile or {}
        self.resume_path = Path(resume_path)

    def validate_candidate(self) -> tuple[bool, str]:
        """Checks that essential candidate details and resume file exist."""
        if not self.profile.get("name"):
            return False, "Candidate name missing in profile"
        if not self.resume_path.exists():
            return False, f"Resume file not found at {self.resume_path}"
        return True, "Profile and resume valid"

    def submit_greenhouse(
        self,
        board_token: str,
        job_id: str,
        cover_letter: str = "",
        custom_answers: dict[str, Any] | None = None,
        dry_run: bool = True
    ) -> dict[str, Any]:
        """Submits application to Greenhouse public board API."""
        valid, msg = self.validate_candidate()
        if not valid:
            return {"success": False, "error": msg}

        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}"
        name_parts = self.profile.get("name", "Applicant").split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        payload = {
            "first_name": first_name,
            "last_name": last_name,
            "email": self.profile.get("email", ""),
            "phone": self.profile.get("phone", ""),
            "cover_letter_text": cover_letter,
        }
        if custom_answers:
            payload.update(custom_answers)

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "endpoint": endpoint,
                "target": f"Greenhouse:{board_token}:{job_id}",
                "fields": {k: v for k, v in payload.items() if k != "resume"},
                "resume_attached": str(self.resume_path),
            }

        with open(self.resume_path, "rb") as f:
            files = {"resume": (self.resume_path.name, f, "application/pdf")}
            res = requests.post(endpoint, data=payload, files=files, timeout=30)
            return {
                "success": res.status_code in (200, 201),
                "status_code": res.status_code,
                "response": res.text[:500],
            }

    def submit_lever(
        self,
        slug: str,
        job_id: str,
        cover_letter: str = "",
        dry_run: bool = True
    ) -> dict[str, Any]:
        """Submits application to Lever postings API."""
        valid, msg = self.validate_candidate()
        if not valid:
            return {"success": False, "error": msg}

        endpoint = f"https://api.lever.co/v0/postings/{slug}/{job_id}"
        payload = {
            "name": self.profile.get("name", ""),
            "email": self.profile.get("email", ""),
            "phone": self.profile.get("phone", ""),
            "comments": cover_letter,
        }

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "endpoint": endpoint,
                "target": f"Lever:{slug}:{job_id}",
                "fields": payload,
                "resume_attached": str(self.resume_path),
            }

        with open(self.resume_path, "rb") as f:
            files = {"resume": (self.resume_path.name, f, "application/pdf")}
            res = requests.post(endpoint, data=payload, files=files, timeout=30)
            return {
                "success": res.status_code in (200, 201),
                "status_code": res.status_code,
                "response": res.text[:500],
            }

    def submit_generic(
        self,
        job_id: str,
        url: str,
        cover_letter: str = "",
        dry_run: bool = True
    ) -> dict[str, Any]:
        """Generic dispatcher based on job_id format."""
        parts = job_id.split(":")
        ats = parts[0].lower() if parts else "unknown"

        if ats == "greenhouse" and len(parts) >= 3:
            return self.submit_greenhouse(parts[1], parts[2], cover_letter=cover_letter, dry_run=dry_run)
        elif ats == "lever" and len(parts) >= 3:
            return self.submit_lever(parts[1], parts[2], cover_letter=cover_letter, dry_run=dry_run)
        else:
            # Assisted link generation for LinkedIn, Ashby, X, etc.
            return {
                "success": True,
                "dry_run": dry_run,
                "target": job_id,
                "action": "assisted_apply",
                "direct_url": url,
                "cover_note": cover_letter,
                "message": "Direct web application prepared for manual review & submission",
            }
