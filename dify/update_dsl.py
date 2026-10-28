import yaml
import json

with open("dify/jobhunt_workflow_dsl.yml", "r", encoding="utf-8") as f:
    dsl = yaml.safe_load(f)

with open("companies.yaml", "r", encoding="utf-8") as f:
    companies_data = yaml.safe_load(f).get("companies", [])

with open("config.yaml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

# Extract from files
gh_slugs = ", ".join(dict.fromkeys(c["slug"] for c in companies_data if c["ats"] == "greenhouse"))
lv_slugs = ", ".join(dict.fromkeys(c["slug"] for c in companies_data if c["ats"] == "lever"))
ash_slugs = ", ".join(dict.fromkeys(c["slug"] for c in companies_data if c["ats"] == "ashby"))

cfg_locations = ", ".join(cfg.get("filters", {}).get("locations", ["bangalore", "bengaluru", "pune", "hyderabad", "india"]))
cfg_keywords = "sde, software engineer, backend, systems, infrastructure"

# 1. Update Start Node Variables
start_node = next(n for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == "start_node")
start_node["data"]["variables"] = [
    {
        "label": "Greenhouse Companies (from companies.yaml)",
        "max_length": 250,
        "options": [],
        "required": False,
        "type": "text-input",
        "variable": "greenhouse_companies",
        "default": gh_slugs
    },
    {
        "label": "Lever Companies (from companies.yaml)",
        "max_length": 250,
        "options": [],
        "required": False,
        "type": "text-input",
        "variable": "lever_companies",
        "default": lv_slugs
    },
    {
        "label": "Ashby Companies (from companies.yaml)",
        "max_length": 350,
        "options": [],
        "required": False,
        "type": "text-input",
        "variable": "ashby_companies",
        "default": ash_slugs
    },
    {
        "label": "Target Locations (from config.yaml)",
        "max_length": 150,
        "options": [],
        "required": False,
        "type": "text-input",
        "variable": "target_location",
        "default": cfg_locations
    },
    {
        "label": "Target Keywords / Roles (from config.yaml)",
        "max_length": 150,
        "options": [],
        "required": False,
        "type": "text-input",
        "variable": "target_keywords",
        "default": cfg_keywords
    },
    {
        "label": "Run Mode",
        "max_length": 50,
        "options": ["dry_run", "production"],
        "required": False,
        "type": "select",
        "variable": "run_mode",
        "default": "dry_run"
    }
]

# 2. Update Fetch Nodes
fetch_gh = next(n for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == "fetch_greenhouse")
fetch_gh["data"]["url"] = "{{#env.BRIDGE_API_URL#}}/jobs/greenhouse?company={{#start_node.greenhouse_companies#}}&location={{#start_node.target_location#}}&keywords={{#start_node.target_keywords#}}&limit=15"
fetch_gh["data"]["headers"] = "Accept: application/json"

fetch_lv = next(n for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == "fetch_lever")
fetch_lv["data"]["url"] = "{{#env.BRIDGE_API_URL#}}/jobs/lever?company={{#start_node.lever_companies#}}&location={{#start_node.target_location#}}&keywords={{#start_node.target_keywords#}}&limit=15"
fetch_lv["data"]["headers"] = "Accept: application/json"

fetch_ash = next(n for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == "fetch_ashby")
fetch_ash["data"]["url"] = "{{#env.BRIDGE_API_URL#}}/jobs/ashby?company={{#start_node.ashby_companies#}}&location={{#start_node.target_location#}}&keywords={{#start_node.target_keywords#}}&limit=15"
fetch_ash["data"]["headers"] = "Accept: application/json"

fetch_li = next(n for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == "fetch_linkedin")
fetch_li["data"]["url"] = "{{#env.BRIDGE_API_URL#}}/jobs/linkedin?keywords={{#start_node.target_keywords#}}&location={{#start_node.target_location#}}&limit=15"
fetch_li["data"]["headers"] = "Accept: application/json"

fetch_x = next(n for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == "fetch_x")
fetch_x["data"]["url"] = "{{#env.BRIDGE_API_URL#}}/jobs/x?keywords={{#start_node.target_keywords#}}&location={{#start_node.target_location#}}&count=15"
fetch_x["data"]["headers"] = "Accept: application/json"

# 3. Update Prefilter Node with config.yaml filters
prefilter = next(n for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == "prefilter_code")
prefilter["data"]["variables"] = [
    {"value_selector": ["fetch_greenhouse", "body"], "variable": "gh_body"},
    {"value_selector": ["fetch_lever", "body"], "variable": "lv_body"},
    {"value_selector": ["fetch_ashby", "body"], "variable": "ash_body"},
    {"value_selector": ["fetch_linkedin", "body"], "variable": "li_body"},
    {"value_selector": ["fetch_x", "body"], "variable": "x_body"},
    {"value_selector": ["start_node", "target_keywords"], "variable": "target_keywords"},
    {"value_selector": ["start_node", "target_location"], "variable": "target_location"},
]

prefilter["data"]["code"] = """import json
import re

def main(gh_body: str, lv_body: str, ash_body: str, li_body: str, x_body: str, target_keywords: str = "", target_location: str = "") -> dict:
    def parse_source(body, default_ats):
        if not body:
            return []
        try:
            data = json.loads(body) if isinstance(body, str) else body
            if isinstance(data, list):
                res = []
                for idx, j in enumerate(data):
                    if isinstance(j, dict) and j.get("title"):
                        res.append({
                            "job_id": str(j.get("job_id") or f"{default_ats}:{idx}"),
                            "company": str(j.get("company") or default_ats.capitalize()),
                            "title": str(j.get("title") or ""),
                            "location": str(j.get("location") or "Remote"),
                            "url": str(j.get("url") or ""),
                            "description": str(j.get("description") or "")[:1200],
                            "source": default_ats.capitalize(),
                        })
                return res
            elif isinstance(data, dict):
                res = []
                for idx, j in enumerate(data.get("jobs", [])):
                    loc_raw = j.get("location")
                    loc = loc_raw.get("name", "Remote") if isinstance(loc_raw, dict) else str(loc_raw or "Remote")
                    res.append({
                        "job_id": f"{default_ats}:{j.get('id', idx)}",
                        "company": default_ats.capitalize(),
                        "title": str(j.get("title") or ""),
                        "location": loc,
                        "url": str(j.get("absolute_url") or j.get("jobUrl") or ""),
                        "description": str(j.get("descriptionPlain") or j.get("title") or "")[:1200],
                        "source": default_ats.capitalize(),
                    })
                return res
        except Exception:
            pass
        return []

    raw_jobs = []
    raw_jobs.extend(parse_source(gh_body, "greenhouse"))
    raw_jobs.extend(parse_source(lv_body, "lever"))
    raw_jobs.extend(parse_source(ash_body, "ashby"))
    raw_jobs.extend(parse_source(li_body, "linkedin"))
    raw_jobs.extend(parse_source(x_body, "x"))

    # Config.yaml regex patterns
    kw_terms = [k.strip() for k in target_keywords.split(",") if k.strip()] if target_keywords else [
        "sde", "software engineer", "backend", "systems", "platform", "infrastructure"
    ]
    kw_pattern = "|".join(re.escape(k) for k in kw_terms)
    inc_pat = re.compile(rf"(?i)\\b({kw_pattern})\\b")
    
    # Exclude patterns directly from config.yaml including management & senior
    exc_pat = re.compile(r"(?i)\\b(staff|principal|distinguished|fellow|architect|senior|director|vp|vice president|head of|chief|cto|manager|management|lead|intern|internship|apprentice|trainee|sales|marketing|recruit)\\b")

    # Locations from config.yaml
    loc_list = [l.strip().lower() for l in target_location.split(",") if l.strip()] if target_location and target_location.lower() not in ("all", "any") else [
        "bangalore", "bengaluru", "pune", "hyderabad", "india"
    ]

    passed = []
    for j in raw_jobs:
        title = j.get("title", "")
        loc = j.get("location", "").lower()
        hay = f"{loc} {title.lower()}"
        if not inc_pat.search(title):
            continue
        if exc_pat and exc_pat.search(title):
            continue
        if loc_list and not any(l in hay for l in loc_list):
            continue
        passed.append(j)

    return {
        "total_fetched": len(raw_jobs),
        "passed_count": len(passed),
        "candidate_jobs": json.dumps(passed[:10])
    }
"""

# 4. Update format_digest to support robust JSON parsing and run_mode
digest_node = next(n for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == "format_digest")
var_names = [v["variable"] for v in digest_node["data"]["variables"]]
if "run_mode" not in var_names:
    digest_node["data"]["variables"].append({
        "value_selector": ["start_node", "run_mode"],
        "variable": "run_mode"
    })

digest_node["data"]["code"] = """import json
import re

def extract_json(raw):
    if not raw:
        return []
    if isinstance(raw, (list, dict)):
        return raw
    text = str(raw).strip()
    text = re.sub(r"^```(?:json)?\\s*", "", text, flags=re.I)
    text = re.sub(r"\\s*```$", "", text)
    m = re.search(r"(\\[[\\s\\S]*\\]|\\{[\\s\\S]*\\})", text)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except Exception:
        return []

def main(screen_raw: str, draft_raw: str, candidate_jobs_json: str, run_mode: str = "dry_run") -> dict:
    scores = {}
    s_data = extract_json(screen_raw)
    if isinstance(s_data, list):
        for item in s_data:
            if isinstance(item, dict) and item.get("job_id"):
                scores[str(item.get("job_id"))] = item
    elif isinstance(s_data, dict):
        for v in s_data.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and item.get("job_id"):
                        scores[str(item.get("job_id"))] = item

    drafts = {}
    d_data = extract_json(draft_raw)
    if isinstance(d_data, list):
        for item in d_data:
            if isinstance(item, dict) and item.get("job_id"):
                drafts[str(item.get("job_id"))] = item
    elif isinstance(d_data, dict):
        for v in d_data.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and item.get("job_id"):
                        drafts[str(item.get("job_id"))] = item

    jobs = []
    j_data = extract_json(candidate_jobs_json)
    if isinstance(j_data, list):
        jobs = j_data

    md_cards = []
    top_job_id = ""
    top_job_url = ""
    top_cover_note = ""

    for job in jobs:
        jid = str(job.get("job_id") or "")
        jid_suffix = jid.split(":")[-1] if ":" in jid else jid
        source = job.get("source", "ATS")

        score_info = scores.get(jid) or scores.get(jid_suffix) or {}
        score = float(score_info.get("score") or 0)
        reason = score_info.get("reason") or "Evaluated match"
        draft = drafts.get(jid) or drafts.get(jid_suffix) or {}

        card_md = f"### [{score:g}/10] [{source}] {job.get('title')} @ {job.get('company')}\\n"
        card_md += f"- **Location**: {job.get('location')}\\n"
        card_md += f"- **URL**: {job.get('url')}\\n"
        card_md += f"- **Match Reason**: {reason}\\n"
        if draft.get("cover_note"):
            card_md += f"\\n**Drafted Cover Note**:\\n> {draft.get('cover_note')}\\n"
        md_cards.append(card_md)

        if not top_job_id and score >= 7.0:
            top_job_id = jid
            top_job_url = job.get("url", "")
            top_cover_note = draft.get("cover_note", "")

    markdown_digest = "# 🦞 JobHunt Multi-Source Digest\\n\\n" + ("\\n---\\n".join(md_cards) if md_cards else "No high-scoring matches found for current search parameters.")

    is_dry_run = (str(run_mode).lower() != "production")
    apply_payload = {
        "job_id": top_job_id or "job:none",
        "url": top_job_url or "https://jobs.example.com",
        "cover_letter": top_cover_note or "I would love to apply for this engineering role.",
        "dry_run": is_dry_run
    }

    return {
        "digest_markdown": markdown_digest,
        "top_count": len(md_cards),
        "apply_payload_json": json.dumps(apply_payload)
    }
"""

with open("dify/jobhunt_workflow_dsl.yml", "w", encoding="utf-8") as f:
    yaml.dump(dsl, f, sort_keys=False, allow_unicode=True)

with open("dify/jobhunt_workflow_dsl.json", "w", encoding="utf-8") as f:
    json.dump(dsl, f, indent=2)

print("Updated dify/jobhunt_workflow_dsl.yml & json with robust JSON parsing and management exclusion!")
