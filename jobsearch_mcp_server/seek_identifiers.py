"""Seek (seek.com.au) job id parsing and URL construction.

Browser-free. Must not import from any other jobsearch_mcp_server module, so
the browser/session layers built in parallel can depend on this module
without pulling in anything heavier. Mirrors identifiers.py's structure and
validation discipline for Indeed, adapted for Seek's own URL scheme.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Seek job ids are purely numeric, stricter than Indeed's alphanumeric `jk`
# ids (confirmed from real Seek job URLs, e.g.
# https://www.seek.com.au/job/93326286).
_SEEK_ID_RE = re.compile(r"^[0-9]+$")

# Real Seek domains this server is known to talk to. Kept deliberately small
# and honest rather than guessing - add to this list only when a new domain
# is actually wired through, not speculatively.
_SEEK_ALLOWED_DOMAINS = frozenset({
    "seek.com.au",
    "www.seek.com.au",
    "au.seek.com",
})


def _validate_seek_domain(domain: str) -> str:
    """Reject any domain not in the known-Seek allowlist. Mirrors identifiers._validate_domain."""
    if domain not in _SEEK_ALLOWED_DOMAINS:
        raise ValueError(f"domain {domain!r} is not an allowed Seek domain")
    return domain


def normalize_seek_job_id(raw: str) -> str:
    """Extract Seek's numeric job id from a full job URL, a bare id, or similar.

    Mirrors identifiers.normalize_job_id's three-case structure (full URL /
    bare fragment / bare id) but Seek's job id lives in the URL PATH
    (`/job/<id>`), not a query parameter like Indeed's `jk=`.

    Accepts:
      - a full job URL, e.g. "https://www.seek.com.au/job/93326286"
      - a bare path fragment, e.g. "job/93326286" or "/job/93326286"
      - an already-bare job id, e.g. "93326286"

    Each of the URL and fragment branches requires the id to be the entire
    trailing path component (an optional trailing slash aside) - not just
    the first path segment after "job/". This is deliberate: a candidate
    like ".../job/123/../../etc/passwd" must not extract "123" and call it
    done, because that leaves the suspicious trailing segments unvalidated
    and silently discarded rather than rejected.

    This is the exact lesson learned from a real bug found in this repo's
    history: Indeed's normalize_job_id originally validated its bare-id case
    but NOT the ids it extracted from URL/fragment branches, letting a
    path-traversal-shaped value slip through un-validated all the way to a
    filesystem path. Do not repeat that mistake here: validate the FINAL
    extracted string against `_SEEK_ID_RE` before returning it, on every
    branch, not just one.

    Raises:
        ValueError: if no plausible job id can be extracted.
    """
    if raw is None:
        raise ValueError("job id input must not be None")

    candidate = raw.strip()
    if not candidate:
        raise ValueError("job id input must not be empty")

    # Case 1: a full URL - the job id must be the entire trailing path
    # component under /job/.
    if "://" in candidate or candidate.startswith("//"):
        parsed = urlparse(candidate)
        match = re.fullmatch(r"/job/([^/]+)/?", parsed.path)
        if match and _SEEK_ID_RE.fullmatch(match.group(1)):
            return match.group(1)
        raise ValueError(f"no plausible Seek job id found in URL: {raw!r}")

    # Case 2: a bare path fragment containing "job/".
    if "job/" in candidate:
        fragment = candidate if candidate.startswith("/") else f"/{candidate}"
        match = re.fullmatch(r"/job/([^/]+)/?", fragment)
        if match and _SEEK_ID_RE.fullmatch(match.group(1)):
            return match.group(1)
        raise ValueError(f"no plausible Seek job id found in fragment: {raw!r}")

    # Case 3: already a bare id - accept only plausible numeric tokens.
    if _SEEK_ID_RE.fullmatch(candidate):
        return candidate

    raise ValueError(f"could not extract a Seek job id from: {raw!r}")


def seek_job_view_url(job_id: str, domain: str = "www.seek.com.au") -> str:
    """Build the canonical Seek job URL: https://{domain}/job/{job_id}."""
    if job_id is None or not job_id.strip():
        raise ValueError("job_id must not be empty")
    if not domain or not domain.strip():
        raise ValueError("domain must not be empty")
    _validate_seek_domain(domain)
    return f"https://{domain}/job/{job_id}"
