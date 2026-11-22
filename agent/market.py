"""The candidate's job market, read from profile.json → job_search.

One place that answers "is this job in my area?" and "is this title in my
field?" for every source connector, the de-dupe logic and the ranker. Nothing
here is hardcoded to a city or a profession: it all comes from the profile, with
sensible fallbacks derived from `target_roles` when a field is missing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from agent import config

# Words that signal a remote / location-independent posting (EN + DE).
REMOTE_TOKENS = ("remote", "homeoffice", "home office", "home-office", "anywhere",
                 "work from home", "wfh", "fully remote", "remote-first",
                 "deutschlandweit", "bundesweit", "telecommute")

# Generic noise ignored when comparing two location strings for de-dupe.
GENERIC_LOC_NOISE = {"kreis", "stadt", "area", "region", "greater", "metropolitan",
                     "district", "remote", "hybrid", "city", "metro"}

_STOP = {"and", "or", "of", "the", "for", "in", "a", "an", "&", "m", "w", "d", "f",
         "junior", "senior", "lead", "head", "principal", "staff", "mid", "level",
         "manager", "intern", "working", "student"}


def _words(s: str) -> list[str]:
    return re.findall(r"[a-zäöüßéèàçñ0-9+#]+", (s or "").lower())


def _german_forms(words: set[str]) -> set[str]:
    """'analyst' → also 'analystin' (German postings often use the feminine form)."""
    return words | {w + "in" for w in words if len(w) > 4 and not w.endswith("in")}


@dataclass
class Market:
    city: str = ""
    city_tokens: tuple[str, ...] = ()
    country: str = ""
    country_code: str = ""
    country_tokens: tuple[str, ...] = ()
    remote: str = "country"                      # none | country | any
    search_terms: list[str] = field(default_factory=list)
    domain_words: set[str] = field(default_factory=set)
    domain_phrases: tuple[str, ...] = ()
    role_words: set[str] = field(default_factory=set)
    excluded_companies: list[str] = field(default_factory=list)

    # ── location ────────────────────────────────────────────────────────────
    def _has_country(self, loc: str) -> bool:
        if any(t in loc for t in self.country_tokens if len(t) > 3):
            return True
        short = {t for t in self.country_tokens if len(t) <= 3}
        return bool(short & set(re.findall(r"[a-z]+", loc)))

    def location_in_scope(self, location: str) -> bool:
        """Keep jobs in the candidate's city; keep remote jobs when allowed
        ('country' = remote within the candidate's country, 'any' = anywhere).
        An unknown/empty location is kept — the ranker sees the full posting."""
        loc = (location or "").lower().strip()
        if not loc:
            return True
        if self.city_tokens and any(t in loc for t in self.city_tokens):
            return True
        if not self.city_tokens and self.country_tokens and self._has_country(loc):
            return True                          # no city set → whole country is in scope
        is_remote = any(t in loc for t in REMOTE_TOKENS)
        if is_remote and self.remote == "any":
            return True
        if is_remote and self.remote == "country":
            return self._has_country(loc)
        return False

    # ── title relevance (coarse pre-filter; the LLM ranker is the real gate) ──
    def title_is_relevant(self, title: str) -> bool:
        if not self.domain_words and not self.role_words:
            return True
        low = (title or "").lower()
        words = set(_words(low))

        def hit(vocab: set[str]) -> bool:
            # exact word, or inside a German compound ("Datenanalystin" ⊃ "analyst")
            return bool(words & vocab) or any(
                v in w for v in vocab if len(v) >= 4 for w in words if len(w) > len(v))

        has_domain = (not self.domain_words or hit(self.domain_words)
                      or any(p in low for p in self.domain_phrases))
        has_role = not self.role_words or hit(self.role_words)
        return has_domain and has_role

    def url_maybe_relevant(self, url: str) -> bool:
        """Cheap check on a job URL slug before fetching the page."""
        low = (url or "").lower()
        keys = (self.domain_words | self.role_words)
        return not keys or any(k in low for k in keys if len(k) > 2)

    def location_noise(self) -> set[str]:
        """Country tokens are noise when comparing two locations for de-dupe."""
        return GENERIC_LOC_NOISE | {t for t in self.country_tokens} | {self.country_code.lower()}


def _clean_terms(raw: list[str]) -> list[str]:
    """Turn messy role strings ('Pricing / Rate Analyst (insurance)') into atomic
    search phrases ('Pricing', 'Rate Analyst')."""
    out: list[str] = []
    seen: set[str] = set()
    for t in raw:
        t = re.sub(r"\(.*?\)", " ", t)
        for part in re.split(r"[/,]", t):
            p = " ".join(part.split())
            if len(p) >= 3 and p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
    return out


def from_profile(prof: dict) -> Market:
    js = prof.get("job_search", {}) or {}
    loc = js.get("location", {}) or {}
    city = (loc.get("preferred") or "").strip()
    city_aliases = loc.get("city_aliases") or ([re.split(r"[,(/]| or | area", city)[0].strip()] if city else [])
    remote = loc.get("remote")
    if remote is None:                           # older profiles: remote_ok bool
        remote = "country" if loc.get("remote_ok", True) else "none"
    country = (loc.get("country") or "").strip()
    code = (loc.get("country_code") or "").strip()
    country_tokens = {a.lower() for a in (loc.get("country_aliases") or []) if a}
    if country:
        country_tokens.add(country.lower())
    if code:
        country_tokens.add(code.lower())

    target_roles = js.get("target_roles") or \
        (prof.get("positioning", {}) or {}).get("roles_that_fit_well", []) or []
    terms = _clean_terms(config.DISCOVER_TERMS or js.get("search_terms") or target_roles)

    role_words = {w.lower() for w in js.get("role_words") or []}
    domain_words = {w.lower() for w in js.get("domain_words") or []}
    if not role_words or not domain_words:      # derive from target roles
        for r in _clean_terms(target_roles):
            ws = [w for w in _words(r) if w not in _STOP]
            if not ws:
                continue
            if not js.get("role_words"):
                role_words.add(ws[-1])
            if not js.get("domain_words"):
                domain_words.update(w for w in ws[:-1] if len(w) > 1)
    phrases = tuple(p.lower() for p in js.get("domain_phrases") or [] if p)

    excl = ((js.get("exclusions") or {}).get("companies") or [])
    return Market(
        city=city,
        city_tokens=tuple(a.lower() for a in city_aliases if a),
        country=country, country_code=code,
        country_tokens=tuple(sorted(country_tokens)),
        remote=remote if remote in ("none", "country", "any") else "country",
        search_terms=terms,
        domain_words=domain_words,
        domain_phrases=phrases,
        role_words=_german_forms(role_words),
        excluded_companies=[c.lower() for c in excl],
    )


def currency_symbol() -> str:
    """Symbol for the salary currency in profile.json (default EUR)."""
    try:
        prof = json.loads(config.PROFILE_JSON.read_text(encoding="utf-8"))
        cur = ((prof.get("job_search", {}) or {}).get("salary", {}) or {}).get("currency") or "EUR"
    except (OSError, json.JSONDecodeError):
        cur = "EUR"
    return {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF "}.get(cur, f"{cur} ")


@lru_cache(maxsize=1)
def load() -> Market:
    try:
        prof = json.loads(config.PROFILE_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prof = {}
    return from_profile(prof)
