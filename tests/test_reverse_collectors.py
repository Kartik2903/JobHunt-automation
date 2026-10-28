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
