"""Unit tests for reverse-engineered collectors, HAR analyzer, submitter, and bridge server."""
import json
import pytest
from starlette.testclient import TestClient

from jobhunt.collectors.reverse_engineer.har_analyzer import HarAnalyzer
from jobhunt.collectors.reverse_engineer.linkedin_voyager import LinkedInVoyagerClient
from jobhunt.collectors.reverse_engineer.x_api import XSearchClient
from jobhunt.collectors.agent_reach_client import AgentReachClient
from jobhunt.submitter import ApplicationSubmitter
from bridge_server import app


# ----------------------------------------------------------- HAR Analyzer tests
def test_har_analyzer_extract_endpoints():
    sample_har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "url": "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards?count=10",
                        "method": "GET",
                        "headers": [
                            {"name": "csrf-token", "value": "ajax:12345"},
                            {"name": "authorization", "value": "Bearer token"},
                        ],
                        "cookies": [
                            {"name": "li_at", "value": "AQEDATestCookie"},
                            {"name": "JSESSIONID", "value": "ajax:12345"},
                        ],
                    },
                    "response": {
                        "status": 200,
                        "content": {"text": '{"elements": []}'},
                    },
                },
                {
                    "request": {
                        "url": "https://static.licdn.com/image.png",
                        "method": "GET",
                        "headers": [],
                        "cookies": [],
                    },
                    "response": {"status": 200},
                },
            ]
        }
    }
    analyzer = HarAnalyzer(sample_har)
    endpoints = analyzer.find_endpoints("voyager")
    assert len(endpoints) == 1
    ep = endpoints[0]
    assert ep.method == "GET"
    assert "csrf-token" in ep.headers
    assert ep.cookies.get("li_at") == "AQEDATestCookie"

    bundle = analyzer.extract_auth_bundle("linkedin.com")
    assert bundle["domain"] == "linkedin.com"
    assert bundle["cookies"]["li_at"] == "AQEDATestCookie"
    assert bundle["headers"]["csrf-token"] == "ajax:12345"


# ------------------------------------------------- LinkedIn Voyager Parser tests
def test_linkedin_voyager_parse_response():
    sample_data = {
        "elements": [
            {
                "jobCard": {
                    "jobPostingUrn": "urn:li:fsd_jobPosting:4012345678",
                    "title": {"text": "Senior Backend Engineer"},
                    "primaryDescription": {"text": "Acme Corp"},
                    "secondaryDescription": {"text": "Bangalore, India"},
                    "insights": [{"text": "Requires Python and Distributed Systems experience."}],
                }
            }
        ],
        "included": [],
    }
    jobs = LinkedInVoyagerClient.parse_voyager_response(sample_data)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_id == "linkedin:4012345678"
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Acme Corp"
    assert job.location == "Bangalore, India"
    assert "linkedin.com/jobs/view/4012345678" in job.url
    assert "Python" in job.description


def test_linkedin_guest_html_parser():
    sample_html = """
    <ul>
      <li>
        <a href="https://in.linkedin.com/jobs/view/4099887766?refId=123" class="base-card__full-link">
          <h3 class="base-search-card__title">Full-Stack Developer</h3>
          <h4 class="base-search-card__subtitle">TechStartup Inc</h4>
          <span class="job-search-card__location">Bengaluru, Karnataka, India</span>
        </a>
      </li>
    </ul>
    """
    jobs = LinkedInVoyagerClient.parse_guest_html(sample_html)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_id == "linkedin:4099887766"
    assert job.title == "Full-Stack Developer"
    assert job.company == "TechStartup Inc"
    assert "Bengaluru" in job.location


# ---------------------------------------------------- X GraphQL Timeline tests
def test_x_search_timeline_parser():
    sample_timeline = {
        "data": {
            "search_by_raw_query": {
                "search_timeline": {
                    "timeline": {
                        "instructions": [
                            {
                                "entries": [
                                    {
                                        "content": {
                                            "itemContent": {
                                                "tweet_results": {
                                                    "result": {
                                                        "legacy": {
                                                            "id_str": "1840000000000001",
                                                            "full_text": "We are hiring SDE 2 in Bangalore! High scale distributed systems. Apply here: https://t.co/abc1234",
                                                            "created_at": "Wed Oct 21 12:00:00 +0000 2026",
                                                            "entities": {
                                                                "urls": [
                                                                    {"expanded_url": "https://company.com/careers/sde2"}
                                                                ]
                                                            },
                                                        },
                                                        "core": {
                                                            "user_results": {
                                                                "result": {
                                                                    "legacy": {
                                                                        "name": "Jane Doe",
                                                                        "screen_name": "janedoe_tech",
                                                                    }
                                                                }
                                                            }
                                                        },
                                                    }
                                                }
                                            }
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
    }
    jobs = XSearchClient.parse_search_timeline(sample_timeline)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_id == "x:1840000000000001"
    assert "@janedoe_tech" in job.company
    assert job.url == "https://company.com/careers/sde2"
    assert "SDE" in job.title
    assert "Bangalore" in job.description


# --------------------------------------------------- Agent Reach Parser tests
def test_agent_reach_parse_output():
    sample_output = [
        {
            "id": "tweet-9988",
            "title": "Hiring Founding Engineer",
            "author": "YCombinatorFounder",
            "url": "https://x.com/founder/status/9988",
            "text": "Looking for a full-stack engineer in Bangalore with React & FastAPI skills.",
            "location": "Bangalore",
        }
    ]
    jobs = AgentReachClient.parse_agent_reach_output(sample_output, platform="x")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_id == "x:tweet-9988"
    assert job.company == "YCombinatorFounder"
    assert "Founding Engineer" in job.title


# ------------------------------------------------- Application Submitter tests
def test_submitter_validation(tmp_path):
    dummy_resume = tmp_path / "resume.pdf"
    dummy_resume.write_text("Dummy PDF content")

    profile = {"name": "Test Candidate", "email": "candidate@example.com"}
    submitter = ApplicationSubmitter(profile=profile, resume_path=dummy_resume)
    valid, msg = submitter.validate_candidate()
    assert valid is True

    # Test Greenhouse Dry Run
    gh_res = submitter.submit_greenhouse("stripe", "55001", cover_letter="Tailored note", dry_run=True)
    assert gh_res["success"] is True
    assert gh_res["dry_run"] is True
    assert gh_res["target"] == "Greenhouse:stripe:55001"
    assert gh_res["fields"]["first_name"] == "Test"
    assert gh_res["fields"]["cover_letter_text"] == "Tailored note"

    # Test Lever Dry Run
    lever_res = submitter.submit_lever("netlify", "99001", cover_letter="Lever note", dry_run=True)
    assert lever_res["success"] is True
    assert lever_res["dry_run"] is True
    assert lever_res["target"] == "Lever:netlify:99001"

    # Test Generic Dispatcher
    generic_res = submitter.submit_generic("linkedin:887766", url="https://linkedin.com/jobs/view/887766")
    assert generic_res["action"] == "assisted_apply"


# ------------------------------------------------------ FastAPI Bridge tests
def test_bridge_server_endpoints():
    client = TestClient(app)

    # Health check
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "online"

    # Apply Dry Run
    apply_payload = {
        "job_id": "greenhouse:stripe:12345",
        "url": "https://boards.greenhouse.io/stripe/jobs/12345",
        "cover_letter": "I would love to join Stripe.",
        "dry_run": True,
    }
    apply_res = client.post("/apply", json=apply_payload)
    assert apply_res.status_code == 200
    data = apply_res.json()
    assert data["success"] is True
    assert data["dry_run"] is True


# ----------------------------------------------- LinkedIn Hidden Market tests
def test_hidden_market_scanner_queries():
    from jobhunt.collectors.reverse_engineer.linkedin_hidden_market import HiddenJobMarketScanner
    scanner = HiddenJobMarketScanner(
        roles=["Product Manager"],
        industries=["Fintech"],
        locations=["San Francisco"],
        days_window=30,
    )
    queries = scanner.build_queries()
    # 1 role x 1 industry = 1 Pattern A
    # 1 role x 1 location = 1 Pattern B
    # 1 role = 1 Pattern C
    assert len(queries) == 3
    patterns = {q["pattern"] for q in queries}
    assert patterns == {"A-DirectIntent", "B-CallToAction", "C-TeamGrowth"}

    for q in queries:
        query_str = q["query"]
        assert "site:linkedin.com/posts" in query_str
        assert "after:" in query_str
        if q["pattern"] == "A-DirectIntent":
            assert '"Product Manager"' in query_str
            assert '"I\'m hiring" OR "hiring a" OR "looking for a"' in query_str
            assert "Fintech" in query_str
            assert "-intitle:jobs" in query_str
        elif q["pattern"] == "B-CallToAction":
            assert '"Product Manager"' in query_str
            assert '"DM me" OR "send me your resume" OR "email me"' in query_str
            assert '"hiring"' in query_str
            assert "San Francisco" in query_str
            assert "-intitle:jobs" in query_str
        elif q["pattern"] == "C-TeamGrowth":
            assert '"excited to announce"' in query_str
            assert '"growing the team"' in query_str
            assert '"Product Manager"' in query_str
            assert "-job -recruiter" in query_str


def test_hidden_market_to_jobs_and_table():
    from jobhunt.collectors.reverse_engineer.linkedin_hidden_market import HiddenJobMarketScanner
    sample_results = [
        {
            "url": "https://www.linkedin.com/posts/sarah-connor_im-hiring-fullstack-activity-7123456789-abcd",
            "title": "Sarah Connor on LinkedIn: I'm hiring a Full-Stack Engineer...",
            "snippet": "We are looking for a Full-Stack Engineer to join our growing AI team in Bangalore. DM me or email resume.",
            "pattern": "A-DirectIntent",
        }
    ]
    jobs = HiddenJobMarketScanner._to_jobs(sample_results)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.ats == "linkedin-hidden"
    assert "Sarah Connor" in job.company
    assert "Full-Stack Engineer" in job.title
    assert "linkedin.com/posts/sarah-connor" in job.url
    assert "Bangalore" in job.description

    # Test Markdown table generation
    table = HiddenJobMarketScanner.format_markdown_table(sample_results)
    assert "| Hiring Manager / Company | Role & Context | **Direct Link to Post** |" in table
    assert "| :--- | :--- | :--- |" in table
    assert "| Sarah Connor |" in table
    assert "[Click Here](https://www.linkedin.com/posts/sarah-connor" in table

    # Also test table from Job object
    table_jobs = HiddenJobMarketScanner.format_markdown_table(jobs)
    assert "| Sarah Connor |" in table_jobs
    assert "[Click Here](" in table_jobs


def test_bridge_server_linkedin_hidden_endpoint(monkeypatch):
    from jobhunt.collectors.reverse_engineer.linkedin_hidden_market import HiddenJobMarketScanner
    from jobhunt.fetch import Job

    dummy_job = Job(
        job_id="linkedin-hidden:test1234",
        ats="linkedin-hidden",
        company="Tech Recruiter",
        title="Full-Stack Engineer",
        location="India / Remote",
        url="https://www.linkedin.com/posts/techrecruiter_hiring",
        description="Looking for Full-Stack devs in Bangalore",
    )
    monkeypatch.setattr(HiddenJobMarketScanner, "scan", lambda self, max_results_per_query=10: [dummy_job])

    client = TestClient(app)
    res = client.get("/jobs/linkedin-hidden?limit=1")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["job_id"] == "linkedin-hidden:test1234"
    assert data[0]["company"] == "Tech Recruiter"
    assert isinstance(res.json(), list)
