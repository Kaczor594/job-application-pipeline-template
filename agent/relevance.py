"""Cheap bulk relevance ranker.

One Haiku call per BULK_BATCH_SIZE jobs, returning a 0–100 relevance score + a
short reason per job. Rewards roles matching what the candidate pursues (from the
preference profile) and penalizes roles resembling ones they passed on. This is
the cost gate: only jobs scoring >= threshold ever reach the (pricier) apply.py
pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from agent import config, llm

_SYSTEM = (
    "You score how relevant each job posting is to a specific job-seeker, as an "
    "integer 0–100. 100 = exactly the kind of role they actively pursue AND can land "
    "now; 50 = plausible but off-target; 0 = irrelevant. Use the candidate context and "
    "their upvoted/downvoted exemplars: reward roles like the ones they pursue, and "
    "PENALIZE roles resembling downvoted ones. Honor the CURRENT OBJECTIVE in the "
    "context when present (seniority, salary, work authorization) — weight it above "
    "generic similarity. Roles far above the candidate's seniority score markedly lower. "
    "Only penalize work-authorization requirements if the context says the candidate "
    "needs sponsorship. Hard rule: if the company is in the excluded list or the role is "
    "in an excluded sector, score <= 5. Be decisive and consistent. Output ONLY a JSON "
    "array — one object {\"idx\":int,\"score\":int,\"reason\":str} per job, in the same "
    "order. reason <= 12 words."
)


@dataclass
class Score:
    score: int
    reason: str


def _coerce_score(val) -> int:
    try:
        return max(0, min(100, int(round(float(val)))))
    except (TypeError, ValueError):
        return 50


def _score_batch(pref_context: str, batch: list[dict]) -> dict[int, Score]:
    payload = [
        {"idx": j["idx"], "company": (j.get("company") or "")[:120],
         "role": (j.get("role") or "")[:160],
         "location": (j.get("location") or "")[:80],
         "snippet": (j.get("snippet") or "")[:700]}
        for j in batch
    ]
    user = (f"{pref_context}\n\nSCORE THESE JOBS "
            f"(return a JSON array, one object per job, same order):\n"
            f"{json.dumps(payload, ensure_ascii=False)}")
    raw = llm.call(_SYSTEM, user, model=config.RELEVANCE_MODEL,
                   max_tokens=1600)
    data = llm.parse_json(raw)
    out: dict[int, Score] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict) or "idx" not in item:
                continue
            try:
                idx = int(item["idx"])
            except (TypeError, ValueError):
                continue
            out[idx] = Score(_coerce_score(item.get("score")),
                             str(item.get("reason", "")).strip()[:160])
    return out


def bulk_score(jobs: list[dict], pref_context: str) -> list[Score]:
    """Score a list of jobs (each a dict with company/role/location/snippet),
    returning Scores aligned to the input order. Batched to keep each call cheap."""
    for i, j in enumerate(jobs):
        j["idx"] = i
    results: dict[int, Score] = {}
    for start in range(0, len(jobs), config.BULK_BATCH_SIZE):
        batch = jobs[start:start + config.BULK_BATCH_SIZE]
        try:
            results.update(_score_batch(pref_context, batch))
        except Exception as e:                      # one bad batch never sinks the night
            print(f"   relevance batch {start}-{start + len(batch)} failed: {e}")
    return [results.get(i, Score(50, "unscored (fallback)")) for i in range(len(jobs))]
