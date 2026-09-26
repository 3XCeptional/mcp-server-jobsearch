"""Indeed job id parsing and URL construction.

Browser-free. Must not import from any other jobsearch_mcp_server module, so
the browser/session layers built in parallel can depend on this module
without pulling in anything heavier.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_BARE_ID_RE = re.compile(r"^[A-Za-z0-9]+$")

# Real Indeed country subdomains this server is known to talk to. Kept
# deliberately small and honest rather than guessing at every country Indeed
# operates in - add to this list only when a new domain is actually wired
# through, not speculatively.
_ALLOWED_DOMAINS = frozenset({
    "indeed.com",
    "www.indeed.com",
    "au.indeed.com",
    "uk.indeed.com",
    "ca.indeed.com",
    "nz.indeed.com",
})


def _validate_domain(domain: str) -> str:
    """Reject any domain not in the known-Indeed allowlist.

    Defense-in-depth: no MCP tool currently exposes `domain` as a parameter,
    so this isn't reachable today, but it guards against a future
    contributor wiring `domain` through to a tool without realizing it would
    let a caller point this server's browser (and any autofilled candidate
    PII) at an arbitrary attacker-controlled URL.
    """
    if domain not in _ALLOWED_DOMAINS:
        raise ValueError(f"domain {domain!r} is not an allowed Indeed domain")
    return domain


def normalize_job_id(raw: str) -> str:
    """Extract Indeed's job id (the `jk` query param) from various inputs.

    Accepts:
      - a full viewjob URL, e.g.
        "https://au.indeed.com/viewjob?jk=abc123def456&tk=xyz"
      - a bare query fragment, e.g. "jk=abc123def456&tk=xyz" or "?jk=abc123def456"
      - an already-bare job id, e.g. "abc123def456"

    Raises:
        ValueError: if no plausible job id can be extracted.
    """
    if raw is None:
        raise ValueError("job id input must not be None")

    candidate = raw.strip()
    if not candidate:
        raise ValueError("job id input must not be empty")

    # Case 1: a full URL - parse its query string for `jk`.
    if "://" in candidate or candidate.startswith("//"):
        parsed = urlparse(candidate)
        query_params = parse_qs(parsed.query)
        jk_values = query_params.get("jk")
        if jk_values and jk_values[0] and _BARE_ID_RE.fullmatch(jk_values[0]):
            return jk_values[0]
        raise ValueError(f"no plausible 'jk' query parameter found in URL: {raw!r}")

    # Case 2: a bare query fragment containing "jk=".
    if "jk=" in candidate:
        query_params = parse_qs(candidate.lstrip("?"))
        jk_values = query_params.get("jk")
        if jk_values and jk_values[0] and _BARE_ID_RE.fullmatch(jk_values[0]):
            return jk_values[0]
        raise ValueError(f"no plausible 'jk' query parameter found in fragment: {raw!r}")

    # Case 3: already a bare id - accept only plausible alphanumeric tokens.
    if _BARE_ID_RE.fullmatch(candidate):
        return candidate

    raise ValueError(f"could not extract an Indeed job id from: {raw!r}")


def job_view_url(job_id: str, domain: str = "au.indeed.com") -> str:
    """Build the canonical viewjob URL for a normalized Indeed job id."""
    if job_id is None or not job_id.strip():
        raise ValueError("job_id must not be empty")
    if not domain or not domain.strip():
        raise ValueError("domain must not be empty")
    _validate_domain(domain)
    return f"https://{domain}/viewjob?jk={job_id}"
