# 🦞 JobHunt on Dify.ai Cloud Setup Guide

This guide walks you through deploying your personal JobHunt autonomous agent on **Dify.ai Cloud** (`https://cloud.dify.ai`), bypassing scraping limits with reverse-engineered internal APIs (`reverse-api-engineer`) and multi-platform zero-key search (`agent-reach`).

---

## 1. Quick Re-import or Manual Node Update

You have two simple options to use the dynamic search capabilities:

### Option A: Import Updated DSL File (Recommended)
1. Go to **Dify Cloud Studio** (`https://cloud.dify.ai`).
2. Click **Create from DSL** (or click the dropdown arrow next to *Create from Blank* and choose *Import DSL*).
3. Select [`dify/jobhunt_workflow_dsl.yml`](jobhunt_workflow_dsl.yml).
4. All dynamic start parameters, bridge endpoints, prefilters, LLM screening, and apply actions are pre-wired.

### Option B: Edit Existing Nodes in Your Current Canvas
If you prefer editing your existing canvas in Dify, update the nodes as follows:

#### 1. In **Start Node**
Add input variables (all optional with defaults):
- `greenhouse_companies`: Text input (Default: `stripe, figma, postman`)
- `lever_companies`: Text input (Default: `levelai, zimperium, omnidian`)
- `ashby_companies`: Text input (Default: `linear, vanta, ramp`)
- `target_location`: Text input (Default: `Bangalore`)
- `target_keywords`: Text input (Default: `Full Stack, Backend, SDE`)
- `run_mode`: Select (`dry_run`, `production`)

#### 2. In **Fetch Greenhouse**
- **Method**: `GET`
- **URL**: `{{#env.BRIDGE_API_URL#}}/jobs/greenhouse?company={{#start_node.greenhouse_companies#}}&location={{#start_node.target_location#}}&keywords={{#start_node.target_keywords#}}&limit=15`
- **Headers**: `Accept: application/json`

#### 3. In **Fetch Lever**
- **Method**: `GET`
- **URL**: `{{#env.BRIDGE_API_URL#}}/jobs/lever?company={{#start_node.lever_companies#}}&location={{#start_node.target_location#}}&keywords={{#start_node.target_keywords#}}&limit=15`
- **Headers**: `Accept: application/json`

#### 4. In **Fetch Ashby**
- **Method**: `GET`
- **URL**: `{{#env.BRIDGE_API_URL#}}/jobs/ashby?company={{#start_node.ashby_companies#}}&location={{#start_node.target_location#}}&keywords={{#start_node.target_keywords#}}&limit=15`
- **Headers**: `Accept: application/json`

#### 5. In **Fetch LinkedIn (Bridge)**
- **Method**: `GET`
- **URL**: `{{#env.BRIDGE_API_URL#}}/jobs/linkedin?keywords={{#start_node.target_keywords#}}&location={{#start_node.target_location#}}&limit=15`
- **Headers**: `Accept: application/json`

#### 6. In **Fetch X (Bridge)**
- **Method**: `GET`
- **URL**: `{{#env.BRIDGE_API_URL#}}/jobs/x?keywords={{#start_node.target_keywords#}}&location={{#start_node.target_location#}}&count=15`
- **Headers**: `Accept: application/json`

---

## 2. Active Bridge Server & Tunnel Details

The local bridge server and Cloudflare tunnel are currently running:
- **Local Bridge**: `http://127.0.0.1:8000`
- **Public Tunnel**: `https://beliefs-origins-violations-livestock.trycloudflare.com`

Verify status anytime:
```bash
curl -s https://beliefs-origins-violations-livestock.trycloudflare.com/health
```

### Why Route Through the Bridge Server?
1. **Zero Payload Crashes**: Raw Greenhouse boards (like Stripe with 700+ jobs) exceed 5 MB, breaching Dify Cloud's strict 1.00 MB node payload limit. The bridge strips HTML and truncates descriptions to keep responses under 50 KB.
2. **Multi-Company Support**: Accepts comma-separated slugs (e.g. `stripe, figma, dropbox`) and fetches all of them in a single call.
3. **Location & Keyword Filtering**: Filters jobs server-side by city/country (`Bangalore`, `Remote`, `India`) and keywords (`Backend`, `Full Stack`, `SDE`).
4. **Header Compliance**: Automatically passes `Accept: application/json` and browser user-agents, eliminating Lever `406 Not Acceptable` errors.

---

## 3. Register Custom Tool in Dify (Optional)

If you wish to use the Bridge as a reusable Custom Tool in Dify Studio:
1. In Dify Cloud, go to **Tools** -> **Custom Tools** -> **Create Custom Tool**.
2. Enter Tool Name: `JobHuntBridge`.
3. Paste the contents of [`dify/openapi_spec.json`](openapi_spec.json).
4. Verify the server URL matches your tunnel: `https://beliefs-origins-violations-livestock.trycloudflare.com`.
5. Click **Save**. Available tools:
   - `get_greenhouse_jobs`
   - `get_lever_jobs`
   - `get_ashby_jobs`
   - `get_linkedin_jobs`
   - `get_x_jobs`
   - `get_aggregated_job_feed`
   - `apply_to_job`
