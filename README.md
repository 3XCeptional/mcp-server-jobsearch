# jobsearch-mcp-server

![Tests](https://github.com/3xceptional/mcp-server-jobsearch/actions/workflows/test.yml/badge.svg)

MCP server that gives AI assistants like Claude access to Indeed and Seek
job search, job postings, and job applications through the user's own local
browser session. Built with Playwright, modeled on the `mcp-server-linkedin`
architecture (session/browser layer, page-owning vs. browser-free module
split, MCP tool surface).

Seek support (`seek_search_jobs`, `seek_get_job_details`,
`seek_close_session`) reuses the same generic session/browser layer with its
own separate browser profile, so Indeed and Seek never share cookies.

The server drives a real, visible Chromium window on the user's machine. It
does not use Indeed's private API and does not require Indeed login for
search or job-detail lookups.

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/3xceptional/mcp-server-jobsearch.git
cd mcp-server-jobsearch
uv sync            # or: pip install -e .
uv run playwright install chromium   # or: playwright install chromium
```

`playwright install chromium` downloads a Playwright-managed Chromium build.
This is required once per machine; the MCP server will fail to launch a
browser without it.

## Running

As a standalone process (stdio MCP transport, blocks waiting for input):

```bash
python -m jobsearch_mcp_server
```

Or, once installed, via the console script:

```bash
jobsearch-mcp-server
```

To register it with Claude Code, add a `.mcp.json` in the project directory
you want it available in:

```json
{
  "mcpServers": {
    "jobsearch-mcp-server": {
      "command": "/absolute/path/to/mcp-server-jobsearch/.venv/bin/python3",
      "args": ["-m", "jobsearch_mcp_server"],
      "type": "stdio"
    }
  }
}
```

Replace `/absolute/path/to/mcp-server-jobsearch` with wherever you cloned
this repo. `.mcp.json` is gitignored here since the path is machine-specific
by nature.

## Tools

All eight tools are registered on a single `FastMCP` server (`jobsearch-mcp-server`).
Indeed's four tools share one long-lived browser session; Seek's four tools share a
second, separate one with its own browser profile, so the two never mix cookies (see
`docs/architecture.md` for the session model).

### `search_jobs(keywords: str, location: str = "", max_results: int = 20) -> list[dict]`

Searches Indeed for jobs matching `keywords`, optionally narrowed to
`location`. Paginates automatically until `max_results` unique jobs are
collected, a page returns no new results, or a 50-page safety ceiling is
hit. Returns a list of dicts with `job_id`, `title`, `company`, `location`,
`snippet`, `url`, and `posted`.

### `get_job_details(job_id: str) -> dict`

Fetches full detail (description, salary, job type where available) for one
job. `job_id` accepts a bare Indeed job id, a full `viewjob` URL, or a bare
`jk=...` query fragment; all forms are normalized to the bare id internally.

### `apply_to_job(job_id, full_name, email, phone, resume_path, location="Sydney, NSW", cover_letter_path="", screening_answers_json="{}") -> dict`

Fills and submits Indeed's native "Indeed Apply" form for `job_id`, using
only the facts supplied in these parameters. `screening_answers_json` is a
JSON object string (e.g. `'{"Do you have a driver'"'"'s license?": "Yes"}'`)
mapping a screening question's field label to its answer, since MCP tool
parameters must be flat JSON-primitive types rather than a nested dict.

This tool is mechanical only. It never decides whether the candidate should
apply, never fact-checks anything, and never invents an answer for a field
it wasn't given a value for. See Limitations below for exactly what it does
and does not handle.

Returns a dict shaped like `ApplyResult`: `job_id`, `submitted` (bool), and
on a non-submission, `blocked_reason` (one of `captcha_wall`,
`account_creation_required`, `external_ats_login_required`,
`unsupported_apply_flow`) plus `unanswered_fields` naming which required
fields it could not fill. On success, `screenshot_path` points to a local
audit-trail screenshot taken immediately before the submit click.

### `close_session() -> str`

Closes Indeed's underlying browser session and releases its Chromium process.
Call this when done; otherwise the browser stays open for the life of the
MCP server process.

### `seek_search_jobs(keywords: str, location: str = "", max_results: int = 20) -> list[dict]`

Same behavior as `search_jobs`, against Seek instead of Indeed. Seek's search
query parameters are `[INFERENCE]`, sourced from public third-party
Seek-scraper documentation rather than a verified reference implementation
the way Indeed's scheme was grounded (see Limitations).

### `seek_get_job_details(job_id: str) -> dict`

Same behavior as `get_job_details`, against Seek instead of Indeed. `job_id`
accepts a bare numeric Seek job id or a full `/job/<id>` URL; both are
normalized internally.

### `seek_apply_to_job(job_id, full_name, email, phone, resume_path, location="Sydney, NSW", cover_letter_path="", screening_answers_json="{}") -> dict`

Same behavior, parameters, and return shape as `apply_to_job`, against
Seek's apply flow instead of Indeed's. Shares its safety logic (attachment
validation, CAPTCHA/account-wall detection, fail-closed on missing required
fields) with `apply_to_job` via a common internal module; only the Seek-specific
form selectors differ, and those are `[INFERENCE]`, unverified against a live
Seek response.

### `seek_close_session() -> str`

Closes Seek's underlying browser session independently of Indeed's.

## Limitations

This project is functional for its intended scope, not a general-purpose
Indeed or Seek automation tool. Specifically, and without overselling it:

- **Seek's selectors are unverified against a live response.** Indeed's DOM
  selectors and URL scheme were grounded against real evidence (job ids
  already present in a production database, a live-reachability check).
  Seek's job-detail URL scheme (`/job/<numeric_id>`) is confirmed the same
  way, but Seek's search query parameters and every CSS selector in
  `seek_job_pages.py`/`seek_apply.py` are `[INFERENCE]`, built from Seek's
  documented `data-automation` attribute convention and public third-party
  scraper documentation, not tested against Seek's actual live markup. All
  four Seek tools share the exact same fail-closed behavior as their Indeed
  counterparts (raise/block rather than fabricate a result on a selector
  miss), so a mismatch surfaces as an honest error, not a wrong answer - but
  expect Seek's selectors to need real-world correction sooner than
  Indeed's.

- **Indeed's bot protection can block this outright.** Indeed fronts its
  pages with Cloudflare, and a challenge (interstitial, CAPTCHA) can appear
  on any page at any time. This server does not solve, bypass, or attempt to
  click through a CAPTCHA anywhere in the codebase. `authentication.py`
  detects a challenge or login wall and gives a human at the visible browser
  window up to five minutes to clear it manually; if it's still blocked when
  that timer runs out, the call fails or `apply_to_job` reports
  `blocked_reason="captcha_wall"`. There is no automated workaround, and
  there isn't meant to be one.

- **The apply flow only handles a single-page apply form.** As
  `apply.py`'s module docstring for `_run_apply_flow` puts it: "v1 does not
  detect or advance through a multi-step apply wizard (Indeed's real Apply
  flow can be multiple pages). If a required core field (name, email,
  phone, or resume) isn't found on the first form snapshot - whether from
  page-structure drift or because the field actually lives on a later,
  un-navigated-to step - this returns `blocked_reason="unsupported_apply_flow"`
  rather than guessing or reporting a false `submitted=True`." In practice,
  this means many real Indeed Apply postings, which commonly split contact
  info, resume upload, and screening questions across several screens, will
  come back blocked rather than submitted.

- **A totally unrecognized search-results page raises an error, not an
  empty list.** If none of the known result-card selectors match anything
  on a `search_jobs` page, `job_pages.py` raises an `ExtractionError`
  instead of returning `[]`. This is deliberate: a Cloudflare interstitial
  or a login wall renders no result cards either, and there is currently no
  independent signal (such as Indeed's own "no jobs match your search"
  markup) that distinguishes a genuine zero-results search from a page that
  failed to load as expected. A real, legitimate zero-results search that
  happens to also render none of the known selectors will therefore also
  raise here. This is a known, honest gap in the current selector coverage,
  not an attempt to paper over it.

- **Search and job-detail lookups are unauthenticated by design.** Indeed
  does not require a logged-in session to browse job search or job-detail
  pages, so this server never types a username or password to reach them.
  The apply flow is different: if Indeed responds to an apply attempt with
  an account-login wall (a password field, a "Sign in with Google" control,
  or similar) or a CAPTCHA, `apply_to_job` detects it and stops, reporting
  `blocked_reason="account_creation_required"` or `blocked_reason="captcha_wall"`
  rather than attempting to push through it. It also refuses (rather than
  attempting) any handoff to a third-party ATS via an "Apply on company
  site" link, reporting `blocked_reason="external_ats_login_required"` in
  that case.

## Security considerations

This tool assumes a trusted, single-operator caller (an agent you run
yourself, on your own behalf), not a shared or multi-tenant deployment.
Two consequences worth knowing before you wire it into an agent:

- **Scraped job postings are untrusted, attacker-postable content.**
  `search_jobs`/`get_job_details` (and their Seek equivalents) return the
  raw text of a real job listing, with no semantic filtering beyond basic
  noise stripping. Anyone can post a job on Indeed or Seek, so a listing's
  text could contain a prompt-injection attempt aimed at whatever LLM
  consumes this server's output. This server does not and cannot defend
  against that: it returns what the page says. Do not let an agent take
  an irreversible action (submitting an application, sending a message)
  based purely on scraped text without a human or a separate gate in the
  loop.
- **`apply_to_job`/`seek_apply_to_job` submit real applications to real
  employers, with no built-in rate limit or per-session cap.** A
  misconfigured or looping caller can submit far faster than a human
  would, which has real-world consequences beyond this process's own
  resource usage. Add your own throttling or a confirmation step at the
  calling-agent level if that caller isn't fully trusted.

## Development

```bash
uv run pytest         # or: .venv/bin/python3 -m pytest
```

Some tests (the DOM-parsing and apply-flow tests) launch a real headless
Chromium instance via Playwright and will fail under a filesystem/process
sandbox that blocks browser process spawning; run them with sandboxing
disabled if that applies to your environment.

## License

Apache-2.0. See `LICENSE`.
