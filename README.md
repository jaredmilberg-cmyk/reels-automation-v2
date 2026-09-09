# reels-automation-v2

Posts one Reel to Instagram every hour, entirely on GitHub — no Render, no Cloud Run,
no server to keep alive. GitHub Actions runs the job on a schedule, builds the video,
hosts it briefly as a GitHub Release asset (so Meta's API can fetch it), publishes it,
then deletes the release.

## What's different from the old version
- No persistent server / background thread that can silently die
- No Cloudflare R2 — video hosting uses a temporary GitHub Release, deleted right after posting
- No YouTube audio yet (reels are silent for now — see "Adding audio back" below)
- Clips pull from 5 categories instead of the old tropical-only set:
  **skydiving, snowboard, ski POV, cinematic tropical landscape, surfing**
  (video) and **surfing, mountain, ocean, skydive, cinematic tropical landscape** (photo)
- Every clip except the outro is sped up 1.5x

## One-time setup

### 1. Create the repo
Push this folder to a **public** GitHub repo (public is required — Meta's API needs to
fetch the video over a public URL, and public repos get unlimited free GitHub Actions
minutes).

### 2. Get a Pexels API key
Go to https://www.pexels.com/api/ → sign up / log in → generate an API key. It's free.

### 3. Add repo secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Secret name | Value |
|---|---|
| `PEXELS_API_KEY` | the key from step 2 |
| `META_ACCESS_TOKEN` | your long-lived Instagram Graph API access token |
| `IG_ACCOUNT_1_ID` | your Instagram Business Account ID |

You do **not** need to add a `GITHUB_TOKEN` secret — GitHub Actions provides one
automatically for every workflow run.

### 4. Replace the outro
Swap `outro.mp4` in the repo root for whatever outro you want — same filename, any
9:16 or wide video, the workflow re-crops it automatically.

### 5. Enable and test
- Go to the **Actions** tab → you should see "Post Reel Hourly"
- Click **Run workflow** to trigger it manually the first time and watch the logs
- If it succeeds, it will run automatically every hour after that (`0 * * * *` cron)

## How timing works
Each run: pulls clips → builds the video with ffmpeg → uploads to a temporary GitHub
Release → tells Instagram to fetch and process it → polls until Instagram says it's
ready → publishes → deletes the release. The whole thing typically finishes in a few
minutes, well inside the 25-minute safety timeout set in the workflow, and the
`concurrency` block guarantees a slow run can never overlap the next hour's run.

## Adding audio back later
The old version used `yt-dlp` + a `cookies.txt` export of a real YouTube session to
download a trending song each run, since YouTube blocks obviously-automated requests
without it. That cookie file expires periodically and needs re-exporting from your
browser — it's the most fragile part of the whole system. When you're ready to add it
back, export fresh cookies, store them as a repo secret, and I can wire that step back
into `run_once.py`.

## Files
- `run_once.py` — the whole pipeline: fetch clips → build video → upload → post → cleanup
- `.github/workflows/post-reel.yml` — the hourly trigger
- `outro.mp4` — your outro clip (swap this out any time)
- `state.json` — tracks which captions have been used recently so they don't repeat back to back; the workflow commits updates to this automatically
- `requirements.txt` — just `requests`; ffmpeg is installed by the workflow itself
