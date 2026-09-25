# indeed-mcp-server

MCP server that gives AI assistants like Claude access to Indeed job search,
job postings, and job applications through the user's own local browser
session. Built with Playwright, modeled on the `mcp-server-linkedin`
architecture (session/browser layer, page-owning vs. browser-free module
split, MCP tool surface).

The server drives a real, visible Chromium window on the user's machine. It
does not use Indeed's private API and does not require Indeed login for
search or job-detail lookups.

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/3xceptional/mcp-server-indeed.git
cd mcp-server-indeed
uv sync            # or: pip install -e .
uv run playwright install chromium   # or: playwright install chromium
```

`playwright install chromium` downloads a Playwright-managed Chromium build.
This is required once per machine; the MCP server will fail to launch a
browser without it.

## Running

As a standalone process (stdio MCP transport, blocks waiting for input):

```bash
python -m indeed_mcp_server
```

Or, once installed, via the console script:

```bash
indeed-mcp-server
```

To register it with Claude Code, add a `.mcp.json` (see the one committed in
this repo) pointing `command` at this project's `.venv/bin/python3` with
`args: ["-m", "indeed_mcp_server"]`.

## Tools

All four tools are registered on a single `FastMCP` server (`indeed-mcp-server`)
and share one long-lived browser session for the life of the process (see
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

Closes the underlying browser session and releases the Chromium process.
Call this when done; otherwise the browser stays open for the life of the
MCP server process.

## Limitations

This project is functional for its intended scope, not a general-purpose
Indeed automation tool. Specifically, and without overselling it:

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
