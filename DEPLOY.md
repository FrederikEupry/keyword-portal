# Deploying to Railway

## One-time setup

1. **Create the Railway project**
   ```bash
   cd ~/Documents/claude-stuff/keyword-portal
   railway login
   railway init  # Choose "Empty project"
   railway up    # First deploy (will fail on env vars — that's expected)
   ```

2. **Add a volume for SQLite + dossiers** (Railway dashboard)
   - Settings → Volumes → New volume
   - Mount path: `/app/data`
   - Set env var `DB_PATH=/app/data/portal.db` and `DOSSIER_DIR=/app/data/dossiers`

3. **Set environment variables** (Railway dashboard → Variables)

   | Variable | Where to get it |
   |---|---|
   | `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
   | `APP_BASE_URL` | Your Railway domain, e.g. `https://keyword-portal.up.railway.app` |
   | `DEBUG` | `false` |
   | `GOOGLE_CLIENT_ID` | Google Cloud Console (see below) |
   | `GOOGLE_CLIENT_SECRET` | Google Cloud Console |
   | `ALLOWED_EMAIL_DOMAIN` | `eupry.com` |
   | `DATAFORSEO_LOGIN` | https://app.dataforseo.com/api-access |
   | `DATAFORSEO_PASSWORD` | Same page (this is the API password, not your account password) |
   | `MAX_COST_PER_RUN_USD` | `2.00` (safety cap) |
   | `EUPRY_DOMAIN` | `eupry.com` |
   | `COMPETITORS_SHEET_ID` | `10EtUFYPH5TyOdeQZW7P7IYYcHgoOadYxlJXfvMonxOM` |
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | Paste the full JSON of the service account credentials (see below) |
   | `OPENROUTER_API_KEY` | https://openrouter.ai/keys — for clustering + exec summary |
   | `OPENROUTER_MODEL` | Default `anthropic/claude-sonnet-4.6`. Alt: `anthropic/claude-haiku-4.5` (cheaper, no caching benefit at our payload size) |

4. **Redeploy:** `railway up`

5. Visit your Railway domain — you should see the login screen.

## Google OAuth setup

1. Go to https://console.cloud.google.com/apis/credentials
2. Pick (or create) a project for internal Eupry tools
3. **OAuth consent screen**
   - User type: **Internal** (restricts to your Workspace = automatic @eupry.com gate)
   - App name: "Eupry Keyword Portal"
   - Support email: your @eupry.com address
   - Scopes: `openid`, `email`, `profile`
4. **Credentials → Create credentials → OAuth client ID**
   - Application type: Web application
   - Authorized redirect URIs:
     - `http://localhost:8000/auth/callback`
     - `https://YOUR-RAILWAY-DOMAIN/auth/callback`
5. Copy Client ID + Client secret into Railway env vars.

> Using **Internal** user type is the strongest gate — Google won't even let non-Workspace users see the consent screen. The portal's `hd` parameter + server-side email-domain check are belt-and-braces.

## Google Sheets service account (for competitor list)

1. https://console.cloud.google.com/iam-admin/serviceaccounts → Create
2. Name: `keyword-portal-sheets`. No roles needed at project level.
3. Create a JSON key, download it.
4. Open the competitor sheet → Share → paste the service account email (e.g. `keyword-portal-sheets@your-project.iam.gserviceaccount.com`) → Viewer.
5. Paste the entire JSON file contents into the `GOOGLE_SERVICE_ACCOUNT_JSON` env var on Railway.

## Domain (optional)

Railway → Settings → Domains → add `keywords.eupry.com` (CNAME to the railway domain). Then update `APP_BASE_URL` and re-add the redirect URI in Google Cloud Console.

## Smoke test after deploy

1. Visit `https://YOUR-DOMAIN/` → should redirect to login
2. Click "Sign in with Google" → only @eupry.com accepted
3. Submit a 1-seed run (e.g. topic "Smoke test", seed "data logger") to verify the pipeline
4. Wait 3-5 min, download the .md file
