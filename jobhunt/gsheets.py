"""Google Sheets synchronization module for JobHunt.

Maintains 3 worksheets within the configured workbook:
1. "All Scanned Jobs"     - Full raw stream of discovered postings across ATS, LinkedIn, and X.
2. "Filtered & Shortlisted" - Filtered candidate roles with match scores, interactive Status & Stage
                            dropdown data validations, and Applied checkboxes.
3. "Analytics & Metrics"    - Platform-by-platform daily scan counts and formula-driven KPI metrics.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from .fetch import Job

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

WS_ALL = "All Scanned Jobs"
WS_FILTERED = "Filtered & Shortlisted"
WS_METRICS = "Analytics & Metrics"

STATUS_OPTIONS = ["Not Applied", "Applied", "Shortlisted", "Rejected"]
STAGE_OPTIONS = ["Not Started", "Assessment", "Technical", "HR", "Offer"]


class GoogleSheetsSync:
    def __init__(
        self,
        sheet_id: str | None = None,
        credentials_json: str | None = None,
        credentials_file: str | None = None,
    ):
        self.sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID", "").strip()
        self.credentials_json = credentials_json or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        self.credentials_file = credentials_file or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        self._client: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None

    def is_configured(self) -> bool:
        """Returns True if the required Sheet ID and credentials are present."""
        has_creds = bool(self.credentials_json or (self.credentials_file and Path(self.credentials_file).exists()))
        return bool(self.sheet_id and has_creds)

    def _get_client(self) -> gspread.Client:
        if self._client:
            return self._client

        if self.credentials_json:
            try:
                info = json.loads(self.credentials_json)
                creds = Credentials.from_service_account_info(info, scopes=SCOPES)
                self._client = gspread.authorize(creds)
                return self._client
            except Exception as e:
                raise ValueError(f"Failed to authenticate with GOOGLE_SERVICE_ACCOUNT_JSON: {e}") from e

        if self.credentials_file and Path(self.credentials_file).exists():
            creds = Credentials.from_service_account_file(self.credentials_file, scopes=SCOPES)
            self._client = gspread.authorize(creds)
            return self._client

        raise ValueError("No valid Google service account credentials found.")

    def get_spreadsheet(self) -> gspread.Spreadsheet:
        if self._spreadsheet:
            return self._spreadsheet
        client = self._get_client()
        self._spreadsheet = client.open_by_key(self.sheet_id)
        return self._spreadsheet

    def ensure_worksheets(self) -> dict[str, gspread.Worksheet]:
        """Ensures all 3 required worksheets exist with correct headers and formatting."""
        sh = self.get_spreadsheet()
        existing = {ws.title: ws for ws in sh.worksheets()}

        # 1. All Scanned Jobs
        if WS_ALL not in existing:
            ws_all = sh.add_worksheet(title=WS_ALL, rows=1000, cols=10)
            existing[WS_ALL] = ws_all
        ws_all = existing[WS_ALL]
        headers_all = [
            "Date Scanned",
            "Company",
            "Platform",
            "Role",
            "Location",
            "Job Link",
            "Passed Filter?",
            "Score",
            "Job ID",
        ]
        curr_all = ws_all.row_values(1)
        if not curr_all or curr_all != headers_all:
            ws_all.update(range_name="A1:I1", values=[headers_all])
            ws_all.format("A1:I1", {"textFormat": {"bold": True}})

        # 2. Filtered & Shortlisted
        if WS_FILTERED not in existing:
            ws_filtered = sh.add_worksheet(title=WS_FILTERED, rows=500, cols=12)
            existing[WS_FILTERED] = ws_filtered
        ws_filtered = existing[WS_FILTERED]
        headers_filtered = [
            "Date Searched",
            "Company Name",
            "Platform Name",
            "Role",
            "Description / Context",
            "Rating / Score",
            "Job Link",
            "Status",
            "Stage",
            "Applied?",
            "Applied Date",
            "Notes / Referral",
        ]
        curr_filtered = ws_filtered.row_values(1)
        if not curr_filtered or curr_filtered != headers_filtered:
            ws_filtered.update(range_name="A1:L1", values=[headers_filtered])
            ws_filtered.format("A1:L1", {"textFormat": {"bold": True}})

        # 3. Analytics & Metrics
        if WS_METRICS not in existing:
            ws_metrics = sh.add_worksheet(title=WS_METRICS, rows=200, cols=10)
            existing[WS_METRICS] = ws_metrics
        ws_metrics = existing[WS_METRICS]
        curr_metrics = ws_metrics.row_values(1)
        if not curr_metrics:
            self._init_metrics_sheet(ws_metrics)

        # If default "Sheet1" is present and empty, we can keep or leave it
        return existing

    def _init_metrics_sheet(self, ws: gspread.Worksheet) -> None:
        """Sets up KPI tables and daily platform logs with live Google Sheets formulas."""
        kpi_data = [
            ["OVERALL JOB HUNT CONVERSION DASHBOARD", ""],
            ["Metric", "Value"],
            ["Total Postings Scanned", f"=COUNTA('{WS_ALL}'!A2:A)"],
            ["Total Shortlisted Opportunities", f"=COUNTA('{WS_FILTERED}'!A2:A)"],
            ["Total Applied", f'=COUNTIF(\'{WS_FILTERED}\'!H2:H, "Applied") + COUNTIF(\'{WS_FILTERED}\'!J2:J, TRUE)'],
            ["Total Shortlisted for Interview", f'=COUNTIF(\'{WS_FILTERED}\'!H2:H, "Shortlisted")'],
            ["Total Rejected", f'=COUNTIF(\'{WS_FILTERED}\'!H2:H, "Rejected")'],
            ["Application Conversion Rate", '=IF(B4>0, TEXT(B5/B4, "0.0%"), "0.0%")'],
            ["Shortlist Conversion Rate", '=IF(B5>0, TEXT(B6/B5, "0.0%"), "0.0%")'],
            ["Rejection Rate", '=IF(B5>0, TEXT(B7/B5, "0.0%"), "0.0%")'],
            ["", ""],
            ["DAILY SCAN & PLATFORM BREAKDOWN", "", "", "", "", ""],
            ["Date", "Platform", "Scanned Count", "Passed Filter Count", "Shortlisted (Score >= 7.0)", "Notes"],
        ]
        ws.update(range_name="A1:F13", values=kpi_data, value_input_option="USER_ENTERED")
        ws.format("A1:B1", {"textFormat": {"bold": True}})
        ws.format("A2:B2", {"textFormat": {"bold": True}})
        ws.format("A12:B12", {"textFormat": {"bold": True}})
        ws.format("A13:F13", {"textFormat": {"bold": True}})

    def sync_run(
        self,
        all_scanned_jobs: list[Job],
        filtered_jobs: list[Job],
        shortlist: list[Job],
        run_date: str | None = None,
    ) -> dict[str, int]:
        """Synchronizes a completed jobhunt run into all 3 Google Sheets worksheets."""
        if not self.is_configured():
            return {"all_added": 0, "filtered_added": 0, "metrics_added": 0}

        sheets = self.ensure_worksheets()
        date_str = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        shortlist_ids = {j.job_id: j for j in shortlist}
        filtered_ids = {j.job_id: j for j in filtered_jobs}

        # --- 1. Sync All Scanned Jobs
        ws_all = sheets[WS_ALL]
        existing_all_ids = set()
        try:
            # Column I contains Job ID
            col_ids = ws_all.col_values(9)
            if len(col_ids) > 1:
                existing_all_ids = set(col_ids[1:])
        except Exception:
            existing_all_ids = set()

        new_all_rows = []
        for j in all_scanned_jobs:
            if j.job_id in existing_all_ids:
                continue
            passed = "Yes" if j.job_id in filtered_ids else "No"
            score_val = ""
            if j.score is not None:
                score_val = f"{j.score:.1f}"
            elif j.job_id in shortlist_ids and shortlist_ids[j.job_id].score is not None:
                score_val = f"{shortlist_ids[j.job_id].score:.1f}"

            new_all_rows.append([
                date_str,
                j.company,
                j.ats,
                j.title,
                j.location,
                j.url,
                passed,
                score_val,
                j.job_id,
            ])
            existing_all_ids.add(j.job_id)

        if new_all_rows:
            ws_all.append_rows(new_all_rows, value_input_option="USER_ENTERED")

        # --- 2. Sync Filtered & Shortlisted
        ws_filtered = sheets[WS_FILTERED]
        existing_filtered_urls = set()
        try:
            col_urls = ws_filtered.col_values(7)
            if len(col_urls) > 1:
                existing_filtered_urls = set(col_urls[1:])
        except Exception:
            existing_filtered_urls = set()

        # Target jobs: all filtered jobs that were evaluated
        candidate_jobs = shortlist if shortlist else [j for j in filtered_jobs if (j.score or 0) >= 6.0]
        if not candidate_jobs:
            candidate_jobs = filtered_jobs[:10]

        start_row_filtered = len(existing_filtered_urls) + 2  # 1 for header + existing
        new_filtered_rows = []
        for j in candidate_jobs:
            if j.url in existing_filtered_urls:
                continue
            desc_snippet = (j.reason or j.description[:200]).replace("\n", " ").strip()
            score_str = f"{j.score:.1f}" if j.score is not None else "-"
            new_filtered_rows.append([
                date_str,
                j.company,
                j.ats,
                j.title,
                desc_snippet,
                score_str,
                j.url,
                "Not Applied",       # Column H: Status
                "Not Started",       # Column I: Stage
                False,               # Column J: Applied? (Checkbox)
                "",                  # Column K: Applied Date
                "",                  # Column L: Notes
            ])
            existing_filtered_urls.add(j.url)

        if new_filtered_rows:
            ws_filtered.append_rows(new_filtered_rows, value_input_option="USER_ENTERED")
            # Apply dropdown validations & checkboxes to the newly added range
            end_row_filtered = start_row_filtered + len(new_filtered_rows) - 1
            self._apply_data_validations(ws_filtered, start_row_filtered, end_row_filtered)

        # --- 3. Sync Analytics Daily Log
        ws_metrics = sheets[WS_METRICS]
        platform_counts: dict[str, dict[str, int]] = {}
        for j in all_scanned_jobs:
            plat = j.ats
            if plat not in platform_counts:
                platform_counts[plat] = {"scanned": 0, "filtered": 0, "shortlist": 0}
            platform_counts[plat]["scanned"] += 1
            if j.job_id in filtered_ids:
                platform_counts[plat]["filtered"] += 1
            if j.job_id in shortlist_ids:
                platform_counts[plat]["shortlist"] += 1

        new_metric_rows = []
        for plat, stats in platform_counts.items():
            new_metric_rows.append([
                date_str,
                plat,
                stats["scanned"],
                stats["filtered"],
                stats["shortlist"],
                f"Automated daily crawl on {date_str}",
            ])

        if new_metric_rows:
            ws_metrics.append_rows(new_metric_rows, value_input_option="USER_ENTERED")

        return {
            "all_added": len(new_all_rows),
            "filtered_added": len(new_filtered_rows),
            "metrics_added": len(new_metric_rows),
        }

    def _apply_data_validations(self, ws: gspread.Worksheet, start_row: int, end_row: int) -> None:
        """Configures cell dropdown validations for Status (Col H), Stage (Col I) and Checkbox for Applied (Col J)."""
        sheet_id = ws.id
        spreadsheet = self.get_spreadsheet()

        # 0-indexed column coordinates:
        # Col H = index 7
        # Col I = index 8
        # Col J = index 9
        requests = [
            # Dropdown for Status (Col H)
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": 7,
                        "endColumnIndex": 8,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": opt} for opt in STATUS_OPTIONS],
                        },
                        "showCustomUi": True,
                        "strict": True,
                    },
                }
            },
            # Dropdown for Stage (Col I)
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": 8,
                        "endColumnIndex": 9,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": opt} for opt in STAGE_OPTIONS],
                        },
                        "showCustomUi": True,
                        "strict": True,
                    },
                }
            },
            # Checkbox for Applied? (Col J)
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": 9,
                        "endColumnIndex": 10,
                    },
                    "rule": {
                        "condition": {
                            "type": "BOOLEAN",
                        },
                        "showCustomUi": True,
                        "strict": True,
                    },
                }
            },
        ]

        try:
            spreadsheet.batch_update({"requests": requests})
        except Exception as e:
            print(f"  ! Note: Could not apply data validation dropdowns: {e}")
