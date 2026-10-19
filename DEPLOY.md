# Deploying to Railway + Cloudflare R2

This is the exact recipe for putting the whole system online. Follow it top to bottom.
Nothing here has been done for you: deploying is a human-only step (see `MANUAL_TODO.md`).

**What you will end up with**

| Railway service | Built from | Start command | Public URL? |
|---|---|---|---|
| `messaging-api` | `services/messaging/Dockerfile` | (default) | ✅ yes: Meta calls `/webhook` |
| `messaging-worker` | `services/messaging/Dockerfile` | `python -m app.worker` | ❌ |
| `messaging-notifier` | `services/messaging/Dockerfile` | `python -m app.notifier` | ❌ |
| `money-api` | `services/money/Dockerfile` | (default, runs migrations first) | ❌ **never** |
| `money-jobs` | `services/money/Dockerfile` | `python -m app.jobs` | ❌ |
| `identity-api` | `services/identity/Dockerfile` | (default, runs migrations first) | ✅ yes: approval + upload pages |
| `identity-jobs` | `services/identity/Dockerfile` | `python -m app.jobs` | ❌ |

Plus two Postgres databases (`pg-money`, `pg-identity`) and one Redis, all from Railway.

> **Small plan (5 services per project)?** Use this layout instead: one Postgres (`pg-money`)
> with two databases `money` and `identity`, plus `Redis`, `money-api`, `identity-api`,
> `messaging-api`. Set `RUN_JOBS_IN_API=true` on the three `-api` services: each one then also
> runs its jobs (money: rates/rules/reconcile; identity: purge; messaging: worker + notifier),
> and a crashed job restarts by itself. Skip the four jobs/worker/notifier services, and point
> identity's `DATABASE_URL` at `.../identity` on `pg-money`. Never set `RUN_JOBS_IN_API=true`
> AND run the separate jobs service, or every job runs twice.

> ⚠️ Money still moves **only** through the fake `MockProvider`. This deployment is a demo.
> Real money needs the "Before REAL money" list in the handbook (section 11).

---

## Step 0: Before you start

1. The code you want to deploy is pushed to GitHub. Railway deploys from **one branch**: use
   `main` after you've reviewed and merged `build/integration` (you choose the branch in
   step 4.2).
2. You have accounts on **railway.app** (sign in with GitHub) and **cloudflare.com**.
3. Have a password manager open. Every secret below goes there and into Railway only:
   never into git, chat, or a file in the repo.

## Step 1: Make NEW production secrets

Never reuse the values from your laptop's `.env` files. In PowerShell (any folder):

```powershell
# Run 3 times: INTERNAL_API_KEY, PHONE_HASH_KEY, WA_VERIFY_TOKEN
python -c "import secrets; print(secrets.token_hex(32))"
```
```powershell
# Run 3 times: FERNET_KEY, VAULT_MASTER_KEY, SESSION_SECRET (three DIFFERENT values)
# (from services\identity with its venv active, because it needs the cryptography package)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Save all six in the password manager with their names.

> `VAULT_MASTER_KEY` protects every uploaded document. If you lose it, the documents can't be
> read anymore. If it leaks, change it (old documents are then unreadable, which is safe).

## Step 2: Cloudflare R2 (private document storage)

1. Cloudflare dashboard → left menu **R2 Object Storage**. (The first time, R2 asks you to
   accept the plan; the free tier is enough.)
2. **Create bucket** → name `student-money-vault` → location Automatic → **Create bucket**.
3. Open the bucket → **Settings**: make sure **Public access** is **Disabled** (the default)
   and there is **no** custom domain and **no** r2.dev URL. Documents must never be public.
4. Back on the R2 page → **Manage R2 API Tokens** (or **API** → **Manage API tokens**) →
   **Create API token**:
   - Permissions: **Object Read & Write**
   - Specify bucket: **Apply to specific buckets only** → `student-money-vault`
   - TTL: Forever (or a date you'll remember to renew)
   - **Create API Token**
5. The next page shows the values **once**. Copy into the password manager:
   - **Access Key ID** → this is `S3_ACCESS_KEY`
   - **Secret Access Key** → this is `S3_SECRET_KEY`
   - The S3 endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` → this is `S3_ENDPOINT`
     (no bucket name at the end)
6. `S3_BUCKET` = `student-money-vault`.

**Check:** the bucket page says "Public access: Disabled".

## Step 3: Railway project, databases and Redis

1. railway.app → **New Project** → **Empty Project**. Rename it `student-money-agent`
   (Project **Settings** → name).
2. On the project canvas: **+ Create** (or **New**) → **Database** → **Add PostgreSQL**.
   Click it → **Settings** → rename the service to `pg-money`.
3. Again **+ Create** → **Database** → **Add PostgreSQL** → rename to `pg-identity`.
   (Two separate databases: Money and Identity never share tables.)
4. **+ Create** → **Database** → **Add Redis** → keep the name `Redis`.
5. Project **Settings** → **Shared Variables** → add (values from step 1):
   `INTERNAL_API_KEY`, `PHONE_HASH_KEY`. These are used by several services.

The names matter: the variables below refer to `pg-money`, `pg-identity` and `Redis`.

## Step 4: Create the 7 services

Do this **7 times**, once for each row of the table at the top.

1. **+ Create** → **GitHub Repo** → pick this repository. (First time: allow Railway to
   access the repo.) A new service appears and may start a build. That's fine, it will
   fail until the variables are set.
2. Click the service → **Settings**:
   - **Service name**: exactly the name from the table (e.g. `money-api`). The private
     address becomes `<name>.railway.internal`, which other services use.
   - **Source** → **Branch**: the branch you deploy (e.g. `main`).
   - **Source** → **Root Directory**: leave **empty** (the repo root: the Dockerfiles need
     `packages/contracts`).
   - **Build** → **Builder**: **Dockerfile**, and **Dockerfile Path**: e.g.
     `services/money/Dockerfile`. (Same effect: a variable
     `RAILWAY_DOCKERFILE_PATH=services/money/Dockerfile`.)
   - **Deploy** → **Custom Start Command**: only for `messaging-worker`,
     `messaging-notifier`, `money-jobs`, `identity-jobs` (see the table). Leave it empty
     for the three `-api` services.
   - **Deploy** → **Healthcheck Path**: `/health` for the three `-api` services only.
   - **Networking**: do NOT generate a domain yet (step 5 does it for the two public ones).

## Step 5: Public domains (only 2 services)

1. `identity-api` → **Settings** → **Networking** → **Public Networking** →
   **Generate Domain** → port **8003**. You get e.g. `identity-api-production-ab12.up.railway.app`.
2. `messaging-api` → same → port **8001**.
3. `money-api` and every worker/jobs service: **no domain**, ever.

Write both domains down. (Using your own domain instead: **Custom Domain** in the same
place, then add the CNAME record it shows at your domain registrar. Do this BEFORE anyone
registers a passkey: passkeys are tied to the domain.)

## Step 6: Variables

Click each service → **Variables** → **Raw Editor**, paste its block, replace every
`<...>`, then **Update Variables**. `${{...}}` are Railway references: type them exactly
as written and Railway fills in the real value.

### `messaging-api`, `messaging-worker`, `messaging-notifier` (same block for all three)
```
INTERNAL_API_KEY=${{shared.INTERNAL_API_KEY}}
PHONE_HASH_KEY=${{shared.PHONE_HASH_KEY}}
REDIS_URL=${{Redis.REDIS_URL}}
MONEY_URL=http://money-api.railway.internal:8002
IDENTITY_URL=http://identity-api.railway.internal:8003
WA_VERIFY_TOKEN=<WA_VERIFY_TOKEN from step 1>
WA_APP_SECRET=<Meta App Secret>
WA_ACCESS_TOKEN=<Meta permanent system-user token>
WA_PHONE_NUMBER_ID=<Meta Phone Number ID>
WA_FAKE_SEND=false
LLM_PROVIDER=gemini
GEMINI_API_KEY=<Gemini API key>
# or: LLM_PROVIDER=anthropic, ANTHROPIC_API_KEY=<Anthropic API key>, LLM_MODEL=claude-opus-5-5
```
`messaging-api` only, also add: `PORT=8001`

### `money-api` and `money-jobs` (same block for both)
```
ENV=production
DATABASE_URL=postgresql+asyncpg://${{pg-money.PGUSER}}:${{pg-money.PGPASSWORD}}@${{pg-money.PGHOST}}:${{pg-money.PGPORT}}/${{pg-money.PGDATABASE}}
REDIS_URL=${{Redis.REDIS_URL}}
INTERNAL_API_KEY=${{shared.INTERNAL_API_KEY}}
IDENTITY_URL=http://identity-api.railway.internal:8003
MAX_PER_TX_BDT=5000
MAX_PER_DAY_BDT=10000
MOCK_FAILURE_RATE=0
MOCK_TIMEOUT_RATE=0
```
`money-api` only, also add: `PORT=8002` and `HOST=0.0.0.0`

- `ENV=production` turns off `/dev/seed` (no test money in production).
- The `+asyncpg` part is required: Railway's own `DATABASE_URL` starts with `postgresql://`,
  which our code can't use.
- `RATE_SOURCE_URL` stays unset: the demo uses mock rates (needed for the "nudge the rate"
  demo step).

### `identity-api` and `identity-jobs` (same block for both)
```
ENV=production
DATABASE_URL=postgresql+asyncpg://${{pg-identity.PGUSER}}:${{pg-identity.PGPASSWORD}}@${{pg-identity.PGHOST}}:${{pg-identity.PGPORT}}/${{pg-identity.PGDATABASE}}
REDIS_URL=${{Redis.REDIS_URL}}
INTERNAL_API_KEY=${{shared.INTERNAL_API_KEY}}
MONEY_URL=http://money-api.railway.internal:8002
PUBLIC_BASE_URL=https://<identity domain from step 5>
RP_ID=<identity domain from step 5, without https://>
FERNET_KEY=<FERNET_KEY from step 1>
VAULT_MASTER_KEY=<VAULT_MASTER_KEY from step 1>
SESSION_SECRET=<SESSION_SECRET from step 1>
STORAGE_BACKEND=s3
S3_ENDPOINT=<from step 2>
S3_BUCKET=student-money-vault
S3_ACCESS_KEY=<from step 2>
S3_SECRET_KEY=<from step 2>
```
`identity-api` only, also add: `PORT=8003` and `HOST=0.0.0.0`

- `PUBLIC_BASE_URL` has no `/` at the end. It must match the browser address exactly.
- `identity-jobs` needs the S3 values too: it deletes expired documents from R2.

**Why `PORT` and `HOST=0.0.0.0`?** Other services call `money-api` and `identity-api` on Railway's
private network at a fixed port. That network is IPv4 & IPv6 now, and Railway's healthcheck
uses IPv4: with `HOST=::` the healthcheck fails. (Messaging is never called by other services.)

## Step 7: Deploy, in this order

Railway redeploys a service when you save its variables. If one didn't, open it →
**Deployments** → **Deploy** (or **Redeploy**).

1. `money-api` and `identity-api` first. Open each → **Deployments** → the newest →
   **View logs**. You must see `Running upgrade ...` lines (the migrations) and then
   `Uvicorn running on http://0.0.0.0:800x`.
2. Then `money-jobs` and `identity-jobs` (they need the tables from step 1).
3. Then `messaging-api`, `messaging-worker`, `messaging-notifier`.

**Checks**
- Open `https://<identity domain>/health` → `{"status":"ok"}`.
- Open `https://<messaging domain>/health` → `{"status":"ok"}`.
- `money-api` → **Settings** → **Networking** shows no public domain (money must only be
  reachable inside Railway).
- No service log shows `ValidationError` or `Field required`. If one does, the error names
  the missing variable.

## Step 8: Point Meta at Railway

1. developers.facebook.com → your app → **WhatsApp** → **Configuration** → **Webhook** →
   **Edit**:
   - Callback URL: `https://<messaging domain>/webhook`
   - Verify token: the production `WA_VERIFY_TOKEN` from step 1
   - **Verify and save**, then make sure **messages** is subscribed.
2. Replace the 24-hour test token with a **permanent** one (business.facebook.com →
   **Settings** → **Users** → **System users** → **Add** (Admin) → **Generate new token**
   for your app with `whatsapp_business_messaging` and `whatsapp_business_management`) and
   put it in `WA_ACCESS_TOKEN` of the three messaging services.

**Check:** send "hi" to the bot from an allowed phone, with your laptop off. You get the
"Secure my account" link button, and the link opens on the identity domain.

## Step 9: Production checklist (sign it off)

- [ ] All secrets are new production values, only in Railway variables and the password manager
- [ ] `money-api` has **no** public domain; only `messaging-api` and `identity-api` have one
- [ ] Identity: `ENV=production`, `PUBLIC_BASE_URL` is https, `RP_ID` = that domain
- [ ] The R2 bucket is private (no public access, no r2.dev URL)
- [ ] Money: `ENV=production` (so `/dev/*` is off), small limits (`MAX_PER_TX_BDT=5000`)
- [ ] `WA_FAKE_SEND=false` and a permanent Meta system-user token
- [ ] A spend limit is set for the AI key (Google Cloud budget for Gemini, or console.anthropic.com)
- [ ] The handbook demo script (section 10) works on production from a real phone

## Updating and rolling back

- Every push to the deployed branch rebuilds the services that use it. Migrations run by
  themselves when `money-api` / `identity-api` start.
- Something broke? Service → **Deployments** → an older green deployment → **⋯** →
  **Redeploy**. (A migration is not undone by this; tell the team before rolling back across
  a migration.)
- Emergency stop for all money movement: Railway → `Redis` → **Data** (or the CLI) →
  set key `money:global_freeze` to `1`. Delete the key to resume.

## Troubleshooting

| Problem | Fix |
|---|---|
| A service crashes at start with `Field required` | That variable is missing on that service (step 6) |
| `asyncpg` / URL error | `DATABASE_URL` must start with `postgresql+asyncpg://` |
| Messaging says "Sorry, something went wrong" for every message | `MONEY_URL` / `IDENTITY_URL` wrong, or `money-api` / `identity-api` lack `PORT` + `HOST=0.0.0.0` |
| 401 between services | `INTERNAL_API_KEY` differs: all 7 must use `${{shared.INTERNAL_API_KEY}}` |
| User "not found" after linking | `PHONE_HASH_KEY` differs between services (use the shared one) |
| Meta "Verify and save" fails | URL must end with `/webhook`; verify token must match exactly |
| Passkey fails with "origin" error | `PUBLIC_BASE_URL` must equal the browser address; `RP_ID` the same host |
| identity-api refuses to start in production | `PUBLIC_BASE_URL` not https, or `STORAGE_BACKEND` isn't `s3` (on purpose) |
| Upload fails | Check the 4 `S3_*` values and that the R2 token is limited to this bucket with Read & Write |
