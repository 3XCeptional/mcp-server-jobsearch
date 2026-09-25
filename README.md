# indeed-mcp-server

MCP server that gives AI assistants like Claude access to Indeed job search,
job postings, and listings through the user's own browser session
(Playwright-based automation, modeled on the `mcp-server-linkedin` daemon
architecture).

**Status: work in progress.** The browser-free utility layer (job id
normalization, search URL building, page text cleanup, and the shared data
contracts) is implemented and tested. The daemon, browser session management,
and job-scraping tool layers are being built out in parallel leaves of this
project's build plan. This README is a stub and will be rewritten with full
usage/setup docs once the server is functional end to end.
