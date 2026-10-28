"""FastAPI bridge server connecting Dify.ai Cloud to reverse-engineered collectors, ATS feeds, and application submitter.
Uses config.yaml for location & title filters, and companies.yaml for ATS targets.
Exposes lightweight OpenAPI endpoints (< 100 KB) that fit within Dify Cloud's 1.00 MB payload ceiling.
"""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from typing import Any
import yaml
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from concurrent.futures import ThreadPoolExecutor, as_completed
from jobhunt.fetch import fetch_board, Job
from jobhunt.collectors.reverse_engineer.linkedin_voyager import LinkedInVoyagerClient
from jobhunt.collectors.reverse_engineer.x_api import XSearchClient
from jobhunt.collectors.agent_reach_client import AgentReachClient
from jobhunt.submitter import ApplicationSubmitter

app = FastAPI(
    title="JobHunt Cloud Bridge API",
    description="Lightweight Bridge API for Dify.ai to search ATS boards, LinkedIn, X, and submit applications with dynamic company, location, and keyword filters loaded from config.yaml and companies.yaml.",
    version="1.3.0",
)

# Load .env file
def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env()

# Load local profile.json if available
PROFILE_PATH = Path("profile.json")
profile_data: dict[str, Any] = {}
if PROFILE_PATH.exists():
    try:
        profile_data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        profile_data = {}

# Load companies.yaml if available
COMPANIES_PATH = Path("companies.yaml")
companies_list: list[dict[str, str]] = []
if COMPANIES_PATH.exists():
    try:
        data = yaml.safe_load(COMPANIES_PATH.read_text(encoding="utf-8"))
        companies_list = data.get("companies", [])
    except Exception:
        companies_list = []

# Load config.yaml if available
CONFIG_PATH = Path("config.yaml")
config_data: dict[str, Any] = {}
if CONFIG_PATH.exists():
    try:
        config_data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        config_data = {}

# Extract defaults from config.yaml (tuned for Fresher / 0-1 Year Experience)
cfg_filters = config_data.get("filters", {})
DEFAULT_LOCATIONS: list[str] = [l.lower() for l in cfg_filters.get("locations", ["bangalore", "bengaluru", "pune", "hyderabad", "india"])]
DEFAULT_INCLUDE_TITLES: list[str] = cfg_filters.get("include_titles", [
    r"\b(software|backend|back-end|full[- ]?stack|server|platform|cloud|api)\b.*\b(engineer|developer)\b",
    r"\b(engineer|developer)\b.*\b(backend|back-end|full[- ]?stack|platform|cloud|api)\b",
    r"\b(sde|swe)\b",
    r"\b(sde|swe)\s*[-_ ]*(1|i|one)?\b",
    r"software development engineer",
    r"software engineer",
    r"full[- ]?stack engineer",
    r"backend engineer",
    r"\b(intern|internship|trainee|apprentice|graduate|fresher|junior|associate)\b",
    r"\bmember of technical staff\b",
])
DEFAULT_EXCLUDE_TITLES: list[str] = cfg_filters.get("exclude_titles", [
    r"\b(staff|principal|distinguished|fellow|architect)\b",
    r"\b(sr\.?|senior)\b",
    r"\b(director|vp|vice president|head of|chief|cto)\b",
    r"\b(manager|management)\b",
    r"\blead\b",
    r"\b(sde|swe|engineer|developer)\s*[-_ ]*(2|3|4|5|ii|iii|iv|v|two|three)\b",
    r"\b(level|l)[-_ ]*[45678]\b",
    r"\b(people operations|human resources|\bhr\b|talent|recruiter|recruiting|sourcer)\b",
    r"\b(administrator|admin|sysadmin|systems admin)\b",
    r"\b(operations|associate operations)\b",
    r"\b(sales|account executive|marketing|finance|payroll|accounting|legal|compliance|procurement|counsel|attorney|lawyer)\b",
    r"\b(customer support|tech support|desktop support|helpdesk)\b",
    r"\b(data scientist|machine learning scientist|research scientist)\b",
    r"\b(frontend|front-end|ios|android|mobile|game|designer)\b",
])
DEFAULT_ALLOW_REMOTE: bool = bool(cfg_filters.get("allow_remote", False))

# Group default companies by ATS
GH_DEFAULT_SLUGS = [c["slug"] for c in companies_list if c.get("ats") == "greenhouse"]
LV_DEFAULT_SLUGS = [c["slug"] for c in companies_list if c.get("ats") == "lever"]
ASH_DEFAULT_SLUGS = [c["slug"] for c in companies_list if c.get("ats") == "ashby"]


def resolve_company_ats_and_name(slug: str) -> tuple[str, str]:
    slug_clean = slug.strip().lower()
    for c in companies_list:
        if c.get("slug", "").lower() == slug_clean:
            return c.get("ats", "greenhouse"), c.get("name", slug.capitalize())
    return "greenhouse", slug.capitalize()


def filter_and_format_jobs(
    jobs: list[Job],
    location: str | None = None,
    keywords: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    filtered = jobs

    # 1. Location filter (uses config.yaml locations if not specified)
    loc_str = location if isinstance(location, str) else None
    loc_input = loc_str.strip() if loc_str and loc_str.strip() else ", ".join(DEFAULT_LOCATIONS)
    if loc_input.lower() not in ("all", "any", "none"):
        loc_terms = [t.strip().lower() for t in loc_input.split(",") if t.strip()]
        loc_matched = []
        for j in filtered:
            hay = f"{j.location or ''} {j.title or ''}".lower()
            is_remote = DEFAULT_ALLOW_REMOTE and any(h in hay for h in ("remote", "anywhere", "wfh"))
            if is_remote or any(term in hay for term in loc_terms):
                loc_matched.append(j)
        if loc_matched:
            filtered = loc_matched

    # 2. Exclude filter - drop unwanted senior, manager, recruiter roles
    if DEFAULT_EXCLUDE_TITLES:
        non_excluded = []
        for j in filtered:
            title = j.title or ""
            if not any(re.search(epat, title, re.I) for epat in DEFAULT_EXCLUDE_TITLES):
                non_excluded.append(j)
        filtered = non_excluded

    # 3. Keywords / Title pattern filter (tuned for engineering / dev roles)
    kw_str = keywords if isinstance(keywords, str) else None
    if kw_str and kw_str.strip() and kw_str.strip().lower() not in ("all", "any", "none"):
        kw_terms = [k.strip().lower() for k in kw_str.split(",") if k.strip()]
        kw_matched = []
        for j in filtered:
            title_lower = (j.title or "").lower()
            text_lower = f"{title_lower} {(j.description or '')[:400]}".lower()
            is_eng_title = bool(re.search(r"\b(engineer|developer|sde|swe|programmer|coder|intern|associate|member of technical staff)\b", title_lower, re.I))
            if any(k in title_lower for k in kw_terms) or (is_eng_title and any(k in text_lower for k in kw_terms)):
                kw_matched.append(j)
        if kw_matched:
            filtered = kw_matched
    elif DEFAULT_INCLUDE_TITLES:
        # Fallback to config.yaml title regexes if no custom keywords passed
        inc_matched = []
        for j in filtered:
            title = j.title or ""
            if any(re.search(pat, title, re.I) for pat in DEFAULT_INCLUDE_TITLES):
                inc_matched.append(j)
        if inc_matched:
            filtered = inc_matched

    out = []
    for j in filtered[:limit]:
        d = j.to_dict()
        desc = d.get("description") or f"{j.title} at {j.company} in {j.location}"
        d["description"] = desc[:800]
        out.append(d)
    return out


class JobResponse(BaseModel):
    job_id: str = Field(..., description="Unique job ID e.g. linkedin:123 or greenhouse:stripe:456")
    ats: str = Field(..., description="Source platform or ATS name")
    company: str = Field(..., description="Company name or posting author")
    title: str = Field(..., description="Job role title")
    location: str = Field(..., description="Job location")
    url: str = Field(..., description="Direct posting link")
    description: str = Field(..., description="Truncated clean job description or tweet content (< 800 chars)")
    posted_at: str | None = None


class ApplyRequest(BaseModel):
    job_id: str = Field(..., description="Unique identifier for the job (e.g. greenhouse:slug:123)")
    url: str = Field(..., description="Application or job posting URL")
    cover_letter: str = Field("", description="Tailored cover note drafted by LLM")
    dry_run: bool = Field(True, description="When true, validates form data without final dispatch")


class ApplyResponse(BaseModel):
    success: bool
    dry_run: bool
    target: str | None = None
    endpoint: str | None = None
    message: str | None = None
    fields: dict[str, Any] | None = None
    error: str | None = None


@app.get("/health", tags=["Status"])
def health_check() -> dict[str, Any]:
    agent_reach = AgentReachClient()
    return {
        "status": "online",
        "service": "JobHunt Bridge for Dify.ai",
        "has_profile": bool(profile_data),
        "has_config": bool(config_data),
        "configured_locations": DEFAULT_LOCATIONS,
        "companies_configured": len(companies_list),
        "greenhouse_companies_count": len(GH_DEFAULT_SLUGS),
        "lever_companies_count": len(LV_DEFAULT_SLUGS),
        "ashby_companies_count": len(ASH_DEFAULT_SLUGS),
        "agent_reach_available": agent_reach.is_available(),
    }


def fetch_board_parallel(ats: str, slugs: list[str]) -> list[Job]:
    all_jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=min(len(slugs), 8)) as pool:
        future_to_slug = {
            pool.submit(fetch_board, ats, s, resolve_company_ats_and_name(s)[1]): s
            for s in slugs
        }
        for fut in as_completed(future_to_slug):
            try:
                res = fut.result()
                if res:
                    all_jobs.extend(res)
            except Exception:
                continue
    return all_jobs


@app.get("/jobs/greenhouse", response_model=list[JobResponse], tags=["Jobs"])
def get_greenhouse_jobs(
    company: str | None = Query(None, description="Company slug or comma-separated slugs. If omitted, uses companies.yaml Greenhouse list"),
    location: str | None = Query(None, description="Location filter. If omitted, uses config.yaml locations (bangalore, pune, hyderabad, india)"),
    keywords: str | None = Query(None, description="Keywords substring filter e.g. Full Stack, Backend, SDE"),
    limit: int = Query(20, ge=1, le=50, description="Max jobs to return"),
) -> list[dict[str, Any]]:
    """Fetch jobs from Greenhouse public board API with dynamic company, location, and keywords."""
    try:
        company_val = company if isinstance(company, str) and "{" not in company else None
        loc_val = location if isinstance(location, str) and "{" not in location else None
        kw_val = keywords if isinstance(keywords, str) and "{" not in keywords else None
        limit_val = limit if isinstance(limit, int) else 20
        slug_str = company_val if company_val and company_val.strip() else ",".join(GH_DEFAULT_SLUGS[:6])
        slugs = [s.strip() for s in slug_str.split(",") if s.strip()]
        all_jobs = fetch_board_parallel("greenhouse", slugs)
        return filter_and_format_jobs(all_jobs, location=loc_val, keywords=kw_val, limit=limit_val)
    except Exception as e:
        print(f"Error in get_greenhouse_jobs: {e}")
        return []


@app.get("/jobs/lever", response_model=list[JobResponse], tags=["Jobs"])
def get_lever_jobs(
    company: str | None = Query(None, description="Company slug or comma-separated slugs. If omitted, uses companies.yaml Lever list"),
    location: str | None = Query(None, description="Location filter. If omitted, uses config.yaml locations (bangalore, pune, hyderabad, india)"),
    keywords: str | None = Query(None, description="Keywords substring filter e.g. Full Stack, Backend, SDE"),
    limit: int = Query(20, ge=1, le=50, description="Max jobs to return"),
) -> list[dict[str, Any]]:
    """Fetch jobs from Lever public board API with dynamic company, location, and keywords."""
    try:
        company_val = company if isinstance(company, str) and "{" not in company else None
        loc_val = location if isinstance(location, str) and "{" not in location else None
        kw_val = keywords if isinstance(keywords, str) and "{" not in keywords else None
        limit_val = limit if isinstance(limit, int) else 20
        slug_str = company_val if company_val and company_val.strip() else ",".join(LV_DEFAULT_SLUGS[:5])
        slugs = [s.strip() for s in slug_str.split(",") if s.strip()]
        all_jobs = fetch_board_parallel("lever", slugs)
        return filter_and_format_jobs(all_jobs, location=loc_val, keywords=kw_val, limit=limit_val)
    except Exception as e:
        print(f"Error in get_lever_jobs: {e}")
        return []


@app.get("/jobs/ashby", response_model=list[JobResponse], tags=["Jobs"])
def get_ashby_jobs(
    company: str | None = Query(None, description="Company slug or comma-separated slugs. If omitted, uses companies.yaml Ashby list"),
    location: str | None = Query(None, description="Location filter. If omitted, uses config.yaml locations (bangalore, pune, hyderabad, india)"),
    keywords: str | None = Query(None, description="Keywords substring filter e.g. Full Stack, Backend, SDE"),
    limit: int = Query(20, ge=1, le=50, description="Max jobs to return"),
) -> list[dict[str, Any]]:
    """Fetch jobs from Ashby public board API with dynamic company, location, and keywords."""
    try:
        company_val = company if isinstance(company, str) and "{" not in company else None
        loc_val = location if isinstance(location, str) and "{" not in location else None
        kw_val = keywords if isinstance(keywords, str) and "{" not in keywords else None
        limit_val = limit if isinstance(limit, int) else 20
        slug_str = company_val if company_val and company_val.strip() else ",".join(ASH_DEFAULT_SLUGS[:6])
        slugs = [s.strip() for s in slug_str.split(",") if s.strip()]
        all_jobs = fetch_board_parallel("ashby", slugs)
        return filter_and_format_jobs(all_jobs, location=loc_val, keywords=kw_val, limit=limit_val)
    except Exception as e:
        print(f"Error in get_ashby_jobs: {e}")
        return []


@app.get("/jobs/linkedin", response_model=list[JobResponse], tags=["Jobs"])
def get_linkedin_jobs(
    keywords: str | None = Query("Software Engineer, Backend", description="Search keywords"),
    location: str | None = Query(None, description="Target city or country. If omitted, uses config.yaml location"),
    limit: int = Query(25, ge=1, le=50, description="Max jobs to return"),
    days: int = Query(4, ge=1, le=14, description="Filter for jobs posted in the last N days (default 4 days)"),
) -> list[dict[str, Any]]:
    """Fetch LinkedIn jobs via reverse-engineered Voyager API / guest feed with recency filtering."""
    try:
        loc_val = location if isinstance(location, str) and "{" not in location else None
        kw_val = keywords if isinstance(keywords, str) and "{" not in keywords else "Software Engineer, Backend"
        limit_val = limit if isinstance(limit, int) else 25
        days_val = days if isinstance(days, int) else 4
        loc = loc_val or DEFAULT_LOCATIONS[0]
        client = LinkedInVoyagerClient()
        kw_primary = kw_val.split(",")[0].strip()
        jobs = client.search_jobs(keywords=kw_primary, location=loc, limit=limit_val, days=days_val)
        return filter_and_format_jobs(jobs, location=loc_val, keywords=kw_val, limit=limit_val)
    except Exception as e:
        print(f"Error in get_linkedin_jobs: {e}")
        return []


@app.get("/jobs/linkedin-hidden", response_model=list[JobResponse], tags=["Jobs"])
def get_linkedin_hidden_jobs(
    role: str | None = Query(None, description="Comma-separated target roles e.g. Full-Stack Engineer, Backend Engineer"),
    industry: str | None = Query(None, description="Comma-separated target industries e.g. SaaS, Fintech, AI"),
    location: str | None = Query(None, description="Comma-separated target locations e.g. Bangalore, India, Remote"),
    days: int = Query(30, ge=1, le=60, description="Date filter window in days (default 30)"),
    limit: int = Query(10, ge=1, le=25, description="Max results per search query"),
) -> list[dict[str, Any]]:
    """Scan the Hidden Job Market for hiring manager posts on LinkedIn via OSINT search."""
    try:
        from jobhunt.collectors.reverse_engineer.linkedin_hidden_market import HiddenJobMarketScanner
        roles_val = [r.strip() for r in role.split(",") if r.strip()] if role and "{" not in role else None
        ind_val = [i.strip() for i in industry.split(",") if i.strip()] if industry and "{" not in industry else None
        loc_val = [l.strip() for l in location.split(",") if l.strip()] if location and "{" not in location else None
        scanner = HiddenJobMarketScanner(
            roles=roles_val,
            industries=ind_val,
            locations=loc_val,
            days_window=days,
        )
        jobs = scanner.scan(max_results_per_query=limit)
        return [j.to_dict() for j in jobs]
    except Exception as e:
        print(f"Error in get_linkedin_hidden_jobs: {e}")
        return []


@app.get("/jobs/x", response_model=list[JobResponse], tags=["Jobs"])
def get_x_jobs(
    query: str | None = Query(None, description="Custom X hiring query. If omitted, built from keywords and location"),
    keywords: str | None = Query("Software Engineer", description="Search keywords e.g. Full Stack, Backend, SDE"),
    location: str | None = Query(None, description="Target location filter"),
    count: int = Query(25, ge=1, le=50, description="Max tweets to inspect"),
    days: int = Query(4, ge=1, le=14, description="Filter for posts created in the last N days (default 4 days)"),
) -> list[dict[str, Any]]:
    """Search tech hiring announcements and founder posts on X with recency filtering."""
    try:
        query_val = query if isinstance(query, str) and "{" not in query else None
        kw_val = keywords if isinstance(keywords, str) and "{" not in keywords else "Software Engineer"
        loc_val = location if isinstance(location, str) and "{" not in location else None
        count_val = count if isinstance(count, int) else 25
        days_val = days if isinstance(days, int) else 4

        client = XSearchClient()
        loc = loc_val or DEFAULT_LOCATIONS[0]
        if not query_val:
            kw = kw_val
            kw_formatted = " OR ".join(f'"{k.strip()}"' if " " in k.strip() else k.strip() for k in kw.split(",") if k.strip())
            query_val = f'(hiring OR "we are hiring") ({kw_formatted}) {loc}'
        jobs = client.search_hiring(query=query_val, count=count_val, days=days_val)
        return filter_and_format_jobs(jobs, location=loc_val, keywords=kw_val, limit=count_val)
    except Exception as e:
        print(f"Error in get_x_jobs: {e}")
        return []


@app.get("/jobs/feed", response_model=list[JobResponse], tags=["Jobs"])
def get_aggregated_job_feed(
    companies: str | None = Query(None, description="Comma-separated company slugs e.g. stripe,figma,postman,levelai,linear. If empty, uses configured companies"),
    location: str | None = Query(None, description="Target location filter. If empty, uses config.yaml locations"),
    keywords: str | None = Query(None, description="Target keywords filter"),
    include_ats: bool = Query(True, description="Include Greenhouse, Lever, and Ashby postings"),
    include_linkedin: bool = Query(True, description="Include reverse-engineered LinkedIn jobs"),
    include_x: bool = Query(True, description="Include X / Twitter tech hiring posts"),
    limit_per_source: int = Query(20, ge=1, le=50, description="Max jobs to sample per board/platform"),
    days: int = Query(4, ge=1, le=14, description="Recency filter in days for LinkedIn and X postings"),
) -> list[dict[str, Any]]:
    """Aggregates postings across Greenhouse, Lever, Ashby, LinkedIn, and X with dynamic filtering from config.yaml, keeping payload < 100 KB."""
    companies_val = companies if isinstance(companies, str) else None
    loc_val = location if isinstance(location, str) else None
    kw_val = keywords if isinstance(keywords, str) else None
    inc_ats = include_ats if isinstance(include_ats, bool) else True
    inc_li = include_linkedin if isinstance(include_linkedin, bool) else True
    inc_x = include_x if isinstance(include_x, bool) else True
    limit_val = limit_per_source if isinstance(limit_per_source, int) else 20
    days_val = days if isinstance(days, int) else 4

    sampled_jobs: list[Job] = []

    # 1. ATS Boards
    if inc_ats:
        targets = []
        if companies_val and companies_val.strip():
            for slug in companies_val.split(","):
                slug = slug.strip()
                if not slug:
                    continue
                ats, name = resolve_company_ats_and_name(slug)
                targets.append({"ats": ats, "slug": slug, "name": name})
        else:
            targets = companies_list[:8]

        for c in targets:
            try:
                b_jobs = fetch_board(c["ats"], c["slug"], c.get("name", c["slug"]))
                sampled_jobs.extend(b_jobs)
            except Exception:
                continue

    # 2. LinkedIn (Open search across all companies, last N days)
    if inc_li:
        try:
            li_client = LinkedInVoyagerClient()
            li_kw = (kw_val or "Software Engineer").split(",")[0].strip()
            loc = loc_val or DEFAULT_LOCATIONS[0]
            li_jobs = li_client.search_jobs(keywords=li_kw, location=loc, limit=limit_val, days=days_val)
            sampled_jobs.extend(li_jobs)
        except Exception:
            pass

    # 3. X / Twitter (Open hiring announcements across all founders/teams, last N days)
    if inc_x:
        try:
            x_client = XSearchClient()
            x_kw = (kw_val or "Software Engineer").split(",")[0].strip()
            loc = loc_val or DEFAULT_LOCATIONS[0]
            x_query = f'(hiring OR "we are hiring") "{x_kw}" {loc}'.strip()
            x_jobs = x_client.search_hiring(query=x_query, count=limit_val, days=days_val)
            sampled_jobs.extend(x_jobs)
        except Exception:
            pass

    return filter_and_format_jobs(sampled_jobs, location=loc_val, keywords=kw_val, limit=limit_val * 4)


@app.post("/apply", response_model=ApplyResponse, tags=["Apply"])
def apply_to_job(req: ApplyRequest) -> dict[str, Any]:
    """Submit or prepare job application."""
    submitter = ApplicationSubmitter(profile=profile_data, resume_path="resume.pdf")
    res = submitter.submit_generic(
        job_id=req.job_id,
        url=req.url,
        cover_letter=req.cover_letter,
        dry_run=req.dry_run,
    )
    return res


class EmailNotifyRequest(BaseModel):
    subject: str = Field("🦞 JobHunt Digest: Filtered Opportunities", description="Subject line for email")
    content: str = Field(..., description="Markdown or text content of the digest")
    to_email: str | None = Field(None, description="Recipient email (defaults to MAIL_TO from .env)")


class EmailNotifyResponse(BaseModel):
    success: bool
    message: str | None = None
    error: str | None = None


@app.post("/notify/email", response_model=EmailNotifyResponse, tags=["Notification"])
def send_email_notification(req: EmailNotifyRequest) -> dict[str, Any]:
    """Sends the formatted digest directly to your configured Gmail inbox."""
    try:
        import smtplib
        from email.message import EmailMessage

        host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("SMTP_PORT", "587"))
        user = os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASS")
        to_addr = req.to_email or os.getenv("MAIL_TO", user)

        if not user or not password:
            return {"success": False, "error": "SMTP_USER or SMTP_PASS not configured in .env"}

        # Format markdown into clean, readable HTML email
        lines = req.content.splitlines()
        html_paragraphs = []
        for line in lines:
            if line.startswith("# "):
                html_paragraphs.append(f"<h1 style='color: #d9381e;'>{line[2:]}</h1>")
            elif line.startswith("### "):
                html_paragraphs.append(f"<h3 style='color: #1a1a1a; margin-top: 18px; margin-bottom: 6px;'>{line[4:]}</h3>")
            elif line.startswith("- "):
                html_paragraphs.append(f"<li style='margin-bottom: 4px;'>{line[2:]}</li>")
            elif line.startswith("> "):
                html_paragraphs.append(f"<blockquote style='border-left: 3px solid #0066cc; padding-left: 10px; color: #444; background: #f8f9fa; margin: 8px 0;'>{line[2:]}</blockquote>")
            elif line.strip() == "---":
                html_paragraphs.append("<hr style='border: none; border-top: 1px solid #e1e4e8; margin: 16px 0;'/>")
            elif line.strip():
                html_paragraphs.append(f"<p style='margin: 4px 0;'>{line}</p>")

        html_body = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; color: #24292e; max-width: 680px; margin: 0 auto; padding: 24px;">
            {''.join(html_paragraphs)}
        </body>
        </html>
        """

        msg = EmailMessage()
        msg["Subject"] = req.subject
        msg["From"] = user
        msg["To"] = to_addr
        msg.set_content(req.content)
        msg.add_alternative(html_body, subtype="html")

        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)

        return {"success": True, "message": f"Digest successfully emailed to {to_addr}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

