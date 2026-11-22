# What it costs

You pay Anthropic directly for the AI calls, using your own API key with prepaid credit.
Everything else is free: the job-board search, the Adzuna key, the Gmail add-on and the
dashboard. Every API call is logged with its token counts to `data/usage.jsonl`, and each
application folder has a `usage.json`.

## Typical costs

| | Cost |
|---|---|
| One application (resume + cover letter) | ≈ $0.15–0.20 |
| Ranking a night's jobs (~60 jobs) | ≈ $0.005 |
| Nightly agent, a typical night (ranking + 1–2 applications) | ≈ $0.20–0.40 |
| Nightly agent, maximum night (default cap: 5 applications) | ≈ $0.90 |
| Manual use, ~10 applications a month | ≈ $2/month |
| Nightly agent every day | ≈ $8–25/month |
| Job boards, Adzuna, Gmail, dashboard | free |

The application figures are measured: an English test application took 5 calls (15k input,
7k output tokens) for $0.16, and a German one $0.18. Long job ads and rewrites when the resume
doesn't fit on one page push it up slightly.

## Where the money goes

| Step | Model | Price per million tokens (input / output) |
|---|---|---|
| Analyzing the ad, matching your profile, cover letter | Claude Opus 5.5 | $4 / $20 |
| Fitting the resume to one page, fit summary | Claude Sonnet 5.5 | $2 / $10 |
| Ranking jobs, reading job-alert emails, status checks | Claude Haiku 5.5 | $0.10 / $0.50 |

The nightly agent only builds documents for jobs that score above the threshold, and tops up
with lower-scored ones only when your review queue holds fewer than 3 unreviewed jobs. So
nights with nothing new cost almost nothing.

## Keeping it under control

Settings go in `.env`:

| Setting | Default | Effect |
|---|---|---|
| `JOBAGENT_NIGHTLY_CAP` | `5` | Most applications built in one night (hard ceiling ≈ $0.18 each) |
| `JOBAGENT_RELEVANCE_THRESHOLD` | `65` | Minimum score (0–100) before a job gets documents |
| `JOBAGENT_MIN_REVIEW` | `3` | Top up the review queue to this many jobs |
| `JOBAPP_MODEL_STRONG` | `claude-opus-5-5` | Model for the writing steps. `claude-sonnet-5-5` roughly halves the cost |
| `JOBAPP_MODEL_FAST` | `claude-sonnet-5-5` | Model for layout and summary steps |

You can also set a monthly spending limit in the Anthropic Console (Settings → Limits).

Prices as of November 2026. If Anthropic changes them, the token counts in
`data/usage.jsonl` still let you work out the real cost.
