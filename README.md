# Web Design Outreach Tool

Finds local businesses with no website (or a bad one), scores their web presence,
drafts a personalized outreach message with Gemini, and either sends it automatically
(via Composio) or flags it for you to send manually when the best channel can't be
determined or isn't connected.

## Pipeline

```
Google Places API  ──▶  Website Scorer  ──▶  Channel Detector  ──▶  Gemini Outreach Writer  ──▶  Sender (Composio / manual queue)
 (find businesses)      (0-10 score,          (email / socials /      (personalized message           (auto-send or
                          per-category           "manual only")         using score weaknesses           CSV for manual)
                          breakdown)                                    as talking points)
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium   # one-time browser download, ~300MB, needed for design screenshots
cp .env.example .env
# fill in your keys in .env
```

You need:
- `GOOGLE_PLACES_API_KEY` — Google Cloud project with the Places API enabled.
- `GEMINI_API_KEY` — Google AI Studio key.
- `COMPOSIO_API_KEY` — optional. Only needed if you want automatic sending.
  Without it, the tool still does everything else and writes ready-to-send
  drafts to `output/manual_outreach.csv`.

## Usage

```bash
python main.py --query "hairdresser" --location "Berwick, VIC" --radius 8000 --max-score 5
```

- `--query` — type of business (e.g. "plumber", "cafe", "hairdresser").
- `--location` — an address or suburb; it's geocoded automatically via Places.
- `--radius` — search radius in meters (default 5000).
- `--max-score` — only outreach businesses scoring at or below this (0-10 scale). Businesses
  with no website at all always qualify regardless of this setting.
- `--limit` — max number of businesses to process (default 20, keeps API usage predictable).
- `--dry-run` — score and draft messages but never send, even if Composio is configured.

## Output

- `output/leads.csv` — every business checked, with full score breakdown.
- `output/manual_outreach.csv` — drafted messages for leads where auto-send wasn't
  possible (no Composio, or channel couldn't be determined) — these are ready to
  copy/paste or send yourself.
- `output/sent_log.csv` — record of what was auto-sent, when, and via what channel.

## Files

- `models.py` — `Lead` and `ScoreBreakdown` dataclasses.
- `core/discovery.py` — Google Places search.
- `core/scorer.py` — objective checks (requests/BeautifulSoup) + Gemini design/content review.
- `core/channel_detector.py` — finds contact email + social links, picks best channel.
- `core/outreach_writer.py` — Gemini drafts the message.
- `core/sender.py` — Composio send, with manual-queue fallback.
- `main.py` — CLI orchestrator.

## Notes / things you'll likely want to tune

- Design scoring uses a real Playwright screenshot + Gemini vision review
  (`core/screenshotter.py` + `core/outreach_writer.review_design`). If a site fails
  to render (timeout, aggressive bot-blocking, broken JS), it automatically falls
  back to the older text-based read so one bad site doesn't kill the whole run.
- Channel "activity" is inferred heuristically (what's findable + Gemini's read of it),
  not measured against real engagement data — there's no reliable free API for that.
- Composio's exact connector setup (which email/social accounts are linked) happens on
  their dashboard, not in this code — `core/sender.py` just calls whatever actions your
  Composio account has connected.

## Cost — what's actually free here

- **Playwright**: always free, runs locally. Only cost is disk space for the
  Chromium download (`playwright install chromium`, ~300MB, one-time).
- **Gemini API**: free tier easily covers this. Each business costs 2 Gemini calls
  (design review + outreach draft) — even a 50-business run is ~100 calls, well
  under the free daily cap. Uses `gemini-3.6-flash`; if you ever hit a rate limit,
  swap `MODEL_NAME` in `core/outreach_writer.py` to `gemini-3.5-flash-lite` for a
  higher free quota at slightly lower quality. Google retires/renames Gemini
  models every few months (this project has already been bumped twice) — if you
  see a `404 NOT_FOUND` mentioning a model name, that's what happened; the error
  message itself tells you the current replacement model to switch to.
- **Google Places API**: the one real limit. Because this tool requests rating and
  review count (useful for prioritizing leads), Google bills those searches at
  its "Enterprise" tier, which gets **1,000 free searches/month** (each search
  returns up to 20 businesses). Fine for a few searches a day, but not unlimited —
  if you blow past it, dropping `places.rating` and `places.userRatingCount` from
  the `FIELD_MASK` in `core/discovery.py` moves you to the "Pro" tier's 5,000
  free/month instead, at the cost of losing rating data for prioritization.
- **Composio**: only used if you configure it (auto-send). Not needed to run the
  tool at all — everything falls back to the manual-outreach CSV without it.


- This tool can run entirely free, but two of its three APIs have real caps: Google Places gives 1,000 free searches/month (each returns up to 20 businesses), because the rating/review-count fields this tool requests put it in Google's pricier "Enterprise" SKU tier — so a few --query/--location runs a day is fine, but don't loop it hundreds of times in one sitting. Gemini (gemini-3.6-flash) is free with rate limits — each business costs 2 calls (design review + draft), so even a 50-business run stays well under the daily cap, though you may occasionally hit a 429/503 from momentary overload (the tool auto-retries these). Playwright and the manual-send fallback (no Composio) are always free with no cap. If you ever do want a hard ceiling, --limit caps businesses processed per run, and watch for 404 errors mentioning a Gemini model name — Google retires/renames free models every few months, and the error message tells you the current replacement to swap into MODEL_NAME in core/outreach_writer.py.
