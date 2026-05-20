# Keyword Portal

Internal Eupry marketing tool for self-serve keyword research. Replaces the workflow where marketing pings engineering to run `/seo cluster` in Claude Code.

## What it does

Marketing enters a topic name + seed keywords. The portal:

1. Expands seeds into longtails via DataForSEO
2. Pulls volume, difficulty, intent, CPC, and SERP top-10 for every keyword
3. Checks which keywords eupry.com already ranks for (cannibalization signal)
4. Pulls competitor coverage for each tracked competitor
5. Generates a markdown dossier the user downloads and pastes into Claude to draft articles

## Stack

- FastAPI + Jinja templates + a sprinkle of HTMX
- SQLite for job/run history
- DataForSEO for all live SEO data
- OpenRouter (default: Claude Sonnet 4.6) for semantic clustering + exec summary
- Google OAuth (restricted to `@eupry.com`)
- Deployed on Railway

## Local development

```bash
cp .env.example .env
# Fill in GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, DATAFORSEO_*
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Visit http://localhost:8000.

## Google OAuth setup

1. Go to https://console.cloud.google.com/apis/credentials
2. Create OAuth 2.0 Client ID → Web application
3. Authorized redirect URIs:
   - `http://localhost:8000/auth/callback` (dev)
   - `https://your-railway-domain/auth/callback` (prod)
4. Scopes needed: `openid`, `email`, `profile`
5. Paste Client ID + Secret into `.env`

Login is restricted to `@eupry.com` accounts via Google's `hd` (hosted domain) parameter and server-side email validation.

## Deploy to Railway

```bash
railway init
railway up
```

Then set env vars in the Railway dashboard. See `.env.example` for the full list.

## Project layout

```
app/
  main.py              # FastAPI app + middleware
  config.py            # Pydantic settings from env
  db.py                # SQLite session/queries
  routes/
    auth.py            # Google OAuth flow
    research.py        # POST /research, GET /research/{id}, download
    history.py         # GET /history
    pages.py           # HTML pages (login, dashboard)
  services/
    dataforseo.py      # DataForSEO client
    research_runner.py # Orchestrates a full research run
    cannibalization.py # Checks eupry.com ranking inventory
    competitors.py     # Loads competitor list from Google Sheets
    markdown_gen.py    # Jinja template → .md dossier
  templates/           # HTML + markdown templates
  static/              # CSS / JS
tests/
data/                  # SQLite db + generated dossiers (gitignored)
```

## Status

v0.1 — in development. See `plans/marketing-keyword-portal.md` in the `claude-seo` repo for the full spec.
