"""Smoke tests verifying the real MCP tools are registered on `server.mcp`.

This exercises the actual constructed `MCPServer` (aliased `FastMCP`)
object built by `indeed_mcp_server.server`, not the source text - grepping
`server.py` for `@mcp.tool(name=...)` decorators was already the leaf-1.2.2
gate. `mcp==2.2.0`'s `MCPServer.list_tools()` is the real, documented
introspection surface (confirmed via `inspect.signature` against the
installed package): it is an async method returning a list of `MCPTool`
objects with `.name` and `.input_schema` attributes, which is what these
tests call.

No `pytest-asyncio` plugin is installed for this project (see the other
test files' docstrings), so each test drives `list_tools()` through a plain
`asyncio.run()` rather than an `async def test_...` function.
"""

from __future__ import annotations

import asyncio

from indeed_mcp_server.server import mcp

EXPECTED_TOOL_NAMES = {"search_jobs", "get_job_details", "apply_to_job", "close_session"}


def _list_tools():
    return asyncio.run(mcp.list_tools())


def test_all_four_tools_are_registered_with_correct_names():
    names = {tool.name for tool in _list_tools()}
    assert names == EXPECTED_TOOL_NAMES


def test_no_unexpected_extra_tools_registered():
    # A future tool addition should update EXPECTED_TOOL_NAMES deliberately,
    # rather than this test silently passing with 5+ registered tools.
    assert len(_list_tools()) == len(EXPECTED_TOOL_NAMES)


def test_search_jobs_requires_keywords_only():
    tool = next(t for t in _list_tools() if t.name == "search_jobs")
    assert tool.input_schema["required"] == ["keywords"]
    assert "location" in tool.input_schema["properties"]
    assert "max_results" in tool.input_schema["properties"]


def test_get_job_details_requires_job_id():
    tool = next(t for t in _list_tools() if t.name == "get_job_details")
    assert tool.input_schema["required"] == ["job_id"]


def test_apply_to_job_requires_the_mandatory_applicant_fields():
    tool = next(t for t in _list_tools() if t.name == "apply_to_job")
    required = set(tool.input_schema["required"])
    assert required == {"job_id", "full_name", "email", "phone", "resume_path"}
    # Optional fields with real defaults must not be forced onto the caller.
    optional_properties = set(tool.input_schema["properties"]) - required
    assert optional_properties == {"location", "cover_letter_path", "screening_answers_json"}


def test_close_session_takes_no_parameters():
    tool = next(t for t in _list_tools() if t.name == "close_session")
    assert tool.input_schema["properties"] == {}
    assert tool.input_schema.get("required", []) == []


def test_tool_descriptions_are_non_empty():
    # Every tool's docstring becomes its MCP description; an empty one means
    # a future refactor accidentally dropped a tool's docstring.
    for tool in _list_tools():
        assert tool.description
        assert tool.description.strip() != ""
