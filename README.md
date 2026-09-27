# Daily Google Drive → Instagram Poster

Automatically post a 10-photo Instagram carousel once a day, sourced from a Google Drive folder — no server to maintain, just a scheduled GitHub Actions workflow.

Every run:
1. Picks the next 10 unposted JPEG photos from a Drive folder (alphabetical order).
2. Temporarily uploads them to a Cloudflare R2 bucket so Instagram can fetch them by public URL.
3. Publishes them as a single carousel post through Meta's official Instagram Graph API.
4. Records the posted photo IDs in a small ledger file, so nothing gets posted twice.
5. Deletes the temporary R2 copies. Your Drive originals are never modified or deleted.

No AI captioning, no third-party posting service, no server — it's a self-contained script plus a cron schedule.

---

## How it works, in plain terms

This is a Python script (`poster/__main__.py`) that GitHub Actions runs on a daily timer. It talks directly to three services using their official APIs:

- **Google Drive API** — to list and download your photos (read-only access).
- **Instagram Graph API** — to publish the carousel.
- **Cloudflare R2** (S3-compatible storage) — used only as temporary public hosting, since Instagram's API requires a public image URL rather than accepting an uploaded file directly.

Every post uses the same fixed caption, defined near the top of `poster/__main__.py`:

```python
CAPTION = "A few favorites from lately. 📸"
```

Change that line (and commit) if you want different wording. There's no AI-generated captioning — that was deliberately left out to avoid an extra point of failure; a photo caption doesn't need to be unique every day to be worth automating.

---

## Before you start: what you'll need

You will end up with **five sets of credentials** by the end of this guide:

| # | What | Used for |
|---|------|----------|
| 1 | A GitHub repository | Hosts the code and runs the schedule |
| 2 | A Meta developer app + Instagram access token | Publishing the post |
| 3 | A Google Cloud service account | Reading your Drive photos |
| 4 | A Cloudflare R2 bucket + API keys | Temporary public image hosting |
| 5 | Photos already cropped to a supported aspect ratio | Avoiding Instagram upload errors |

None of this costs money for typical personal use — GitHub Actions, Cloudflare R2, and the Instagram Graph API all have generous free tiers for this workload (see **Cost notes** at the end).

---

## Step 1 — Get this code into your own GitHub repository

1. Click **"Use this template"** on this repository's page (if you're viewing this as a template), or download/clone it and push the files into a **new repository** of your own.
2. You can make the repo public or private — either works. If your Drive folder ID or bucket name feels sensitive to you, keep it private; nothing in the code itself is secret, since every credential is stored as an encrypted GitHub Secret (Step 6), never hardcoded.
3. Make sure the workflow file lives at `.github/workflows/post-daily.yml` on your repository's **default branch** (usually `main`). GitHub only evaluates scheduled (`cron`) triggers on the default branch — a workflow sitting on any other branch will never fire automatically.

---

## Step 2 — Set up the Instagram side (Meta Developer App)

1. Make sure your Instagram account is a **Professional account** — either **Business** or **Creator** (Settings → Account type in the Instagram app).
2. Go to **[developers.facebook.com](https://developers.facebook.com/)** → **My Apps** → **Create App**. Choose a use case that includes **Instagram** (e.g. "Other" → Business, then add the **Instagram** product from the app dashboard).
3. In the app's **Instagram → API setup with Instagram Login** section, add these permissions/scopes:
   - `instagram_business_basic`
   - `instagram_business_content_publish`
4. Complete the Instagram Login flow for your own account to generate a **long-lived access token**, and note your **Instagram User ID** (also shown in this setup flow).
5. **If your app is still in Development mode** (i.e. you haven't submitted it for Meta's App Review), go to **App Roles → Roles** and add your Instagram account as an **Instagram Tester**. The account must accept that invite (usually via a link or notification) before publishing will work. Skipping this step is the single most common cause of a mysterious `"API access blocked"` error.
6. Long-lived tokens expire (typically after ~60 days) and need to be refreshed periodically — Meta's documentation covers refreshing before expiry. When you get a new token, update the `INSTAGRAM_ACCESS_TOKEN` secret (Step 6).

---

## Step 3 — Set up Google Drive access (service account)

1. Go to **[console.cloud.google.com](https://console.cloud.google.com/)**, create a project (or reuse one), and enable the **Google Drive API** under **APIs & Services → Library**.
2. Go to **APIs & Services → Credentials → Create Credentials → Service Account**. Give it any name; you don't need to grant it project-level roles.
3. Open the new service account, go to **Keys → Add Key → Create new key → JSON**, and download the file. This file is a secret — treat it like a password.
4. In Google Drive, create (or choose) the folder that will hold your photos. Right-click → **Share** → paste the service account's email address (looks like `something@your-project.iam.gserviceaccount.com`) → give it **Viewer** access.
5. Copy the folder's **ID** from its URL: `https://drive.google.com/drive/folders/`**`THIS_PART_IS_THE_ID`**.

---

## Step 4 — Set up Cloudflare R2 (temporary image hosting)

1. In the **Cloudflare dashboard**, go to **R2 Object Storage** → **Create bucket**. Name it something dedicated to this purpose (e.g. `ig-temp-images`) — don't reuse a bucket that stores anything else.
2. Enable **public access** for the bucket, either via its built-in `r2.dev` subdomain or a custom domain you control. Note the resulting public base URL.
3. Go to **R2 → Manage API Tokens → Create API Token**, scoped to this bucket, with read/write permissions. Note the **Access Key ID** and **Secret Access Key**.
4. Note your Cloudflare **Account ID** (visible on the R2 overview page or account dashboard sidebar).

> A public bucket URL means anyone with the exact random URL could view a file while it's there — that's normal for this design. Each run uploads up to 10 images and deletes them immediately after the publish attempt, so the exposure window is brief. Don't store anything else in this bucket.

---

## Step 5 — Prepare your photos

- **Format:** JPEG only. Convert PNGs or other formats before adding them to the Drive folder — the workflow only looks for `image/jpeg` files.
- **Aspect ratio:** Instagram's Graph API only accepts images between **4:5 (portrait)** and **1.91:1 (landscape)**. This workflow does **not** validate or resize images automatically — a single photo outside that range will cause the *entire day's carousel* to fail. Crop your photos to a safe ratio (4:5 is the safest universal choice) before uploading them to Drive.
- **Naming:** Photos are selected in ascending filename order, so name them in the order you want them posted (e.g. `01.jpg`, `02.jpg`, ...).
- **Batch size:** The workflow only posts once **10 or more** unposted eligible photos are sitting in the folder. Fewer than 10 → it quietly does nothing that day and waits for more.

---

## Step 6 — Add your secrets and variables to GitHub

In your repository, go to **Settings → Secrets and variables → Actions**.

### Repository secrets (Secrets tab → New repository secret)

| Secret name | Value |
|---|---|
| `GOOGLE_DRIVE_FOLDER_ID` | The folder ID from Step 3.5 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The entire contents of the JSON key file from Step 3.3, pasted as-is |
| `INSTAGRAM_USER_ID` | Instagram User ID from Step 2.4 |
| `INSTAGRAM_ACCESS_TOKEN` | Long-lived access token from Step 2.4 |
| `R2_ACCOUNT_ID` | Cloudflare Account ID from Step 4.4 |
| `R2_ACCESS_KEY_ID` | R2 API access key ID from Step 4.3 |
| `R2_SECRET_ACCESS_KEY` | R2 API secret from Step 4.3 |
| `R2_BUCKET` | The bucket name from Step 4.1 |
| `R2_PUBLIC_BASE_URL` | The public base URL from Step 4.2, **without** a trailing slash |

### Repository variables (Variables tab → New repository variable)

| Variable name | Value |
|---|---|
| `INSTAGRAM_API_VERSION` | A currently supported Graph API version, e.g. `v23.0` — check Meta's changelog for the current default |

Never commit any of these values directly into the code or `.env` files — the `.gitignore` already excludes common secret file patterns as a safety net.

---

## Step 7 — Set your posting time

Open `.github/workflows/post-daily.yml`. Near the top:

```yaml
on:
  schedule:
    - cron: '30 18 * * *'   # UTC time — adjust for your desired local time
  workflow_dispatch: {}
```

GitHub Actions schedules always run in **UTC**, so you'll need to convert your desired local time. Also update the matching wait step further down:

```yaml
      - name: Wait until 12:05 a.m. IST
        if: github.event_name == 'schedule'
        run: |
          python - <<'PY'
          ...
          target = datetime.now(zone).replace(
              hour=0, minute=5, second=0, microsecond=0
          )
          ...
```

Change `ZoneInfo("Asia/Kolkata")` to your own [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (e.g. `"America/New_York"`, `"Europe/London"`), and set `hour=`/`minute=` to your target local time. The cron line should point to roughly 5 minutes *before* that target time, converted to UTC — this buffer absorbs GitHub's typical scheduling delay so the post still lands close to your intended time.

**Testing tip:** you don't have to wait for the schedule to test this. Go to your repo's **Actions** tab → **"Daily Instagram post"** in the sidebar → **Run workflow** button. Manual runs skip the timing wait entirely and publish immediately, which is the fastest way to confirm everything's wired up correctly.

---

## Behavior and recovery

- If fewer than 10 unposted eligible JPEGs exist, the run exits quietly without posting or erroring — it just waits for more photos.
- If any step fails (upload, publish, etc.), your Drive originals are left untouched, and any temporary R2 copies are still cleaned up.
- A scheduled run that starts late (GitHub doesn't guarantee exact cron timing) still posts as soon as it can, rather than skipping the day.
- Once Instagram confirms a successful post, the workflow commits an updated `state/posted.json` ledger back to the repo — this is what prevents the same photos from being posted again. Deleting an already-posted photo from Drive afterward is safe; the ledger entry is just a harmless historical record.
- If a run reports `"API access blocked"` (an `OAuthException`), that's a Meta-side account or app restriction — commonly a pending account verification requirement, a missing App Review permission, or an Instagram Tester invite that needs re-accepting. Check the Meta Developer Dashboard for your app for a specific restriction notice before assuming the code is at fault.

---

## Cost notes

- **GitHub Actions:** Free personal accounts include a monthly Actions-minutes allowance that comfortably covers one short daily workflow.
- **Cloudflare R2:** Has a free tier; this workflow's usage (a handful of small image uploads/deletes per day) is far below typical free-tier limits.
- **Instagram Graph API:** Not billed per post.

Always check each provider's current terms and quotas — free tiers and limits can change over time.

---

## License

This project is provided under the MIT License — see `LICENSE` for details. Use it, modify it, and adapt it to your own workflow.