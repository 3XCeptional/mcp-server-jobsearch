"""Cleanup for text extracted from rendered Indeed pages.

Browser-free, no imports from other jobsearch_mcp_server modules (or anything
else) so it can be unit tested against fixture strings without a browser.
"""

from __future__ import annotations

# Lines that are noise only when the *entire* line (after stripping and
# lowercasing) matches one of these boilerplate labels. Using an exact-match
# set (rather than a substring match) avoids stripping legitimate job-posting
# text that happens to contain one of these words as part of a longer sentence.
_EXACT_NOISE_LINES: frozenset[str] = frozenset(
    {
        "sponsored",
        "ad",
        "save",
        "saved",
        "save this job",
        "save job",
        "report this job",
        "report job",
        "not interested",
        "skip to job details",
        "skip to main content",
        "employer active",
        "urgently hiring",
        "hiring ongoing",
        "responded to 75% or more of applications in the past 30 days",
    }
)

# Substrings that mark a line as noise wherever they appear, used for the
# longer, more free-form banners (cookie consent, expiry notices) that don't
# have one single canonical phrasing.
_NOISE_SUBSTRINGS: tuple[str, ...] = (
    "we use cookies",
    "use cookies to",
    "cookie policy",
    "cookie preferences",
    "manage cookies",
    "accept all cookies",
    "by clicking accept",
    "this job has expired",
    "view all jobs at",
    "report this listing",
)


# How many words a line is allowed to have left over, after the matched noise
# substring is removed, before we still call it noise. The `_NOISE_SUBSTRINGS`
# banners are short boilerplate phrases padded only by a handful of connector
# words ("to", "on", "at", "in our experience" ...), so a genuine banner line
# never has more than a few words left once the phrase itself is stripped
# out. A legitimate sentence that merely *contains* one of these phrases mid
# clause (e.g. "We use cookies in our daily bread recipe...") keeps talking
# well past the phrase, leaving a much longer remainder - that's the signal
# used to tell the two apart instead of a plain "substring anywhere" match.
_MAX_NOISE_REMAINDER_WORDS = 6


def _is_noise_line(line: str) -> bool:
    stripped = line.strip().lower()
    if not stripped:
        return False
    if stripped in _EXACT_NOISE_LINES:
        return True
    for substring in _NOISE_SUBSTRINGS:
        if substring not in stripped:
            continue
        remainder = stripped.replace(substring, " ", 1)
        if len(remainder.split()) <= _MAX_NOISE_REMAINDER_WORDS:
            return True
    return False


def filter_indeed_noise_lines(lines: list[str]) -> list[str]:
    """Filter Indeed page chrome noise out of a pre-split list of lines."""
    return [line for line in lines if not _is_noise_line(line)]


def strip_indeed_noise(text: str) -> str:
    """Strip Indeed page chrome noise from extracted page text.

    Removes cookie-consent banner text, "Sponsored"/"Ad" labels, "Save this
    job", "report this job" boilerplate, and similar chrome, line by line.
    """
    lines = text.splitlines()
    filtered = filter_indeed_noise_lines(lines)
    return "\n".join(filtered)
