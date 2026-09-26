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

import pytest

from indeed_mcp_server.server import extractor, mcp, seek_extractor

EXPECTED_TOOL_NAMES = {
    "search_jobs",
    "get_job_details",
    "apply_to_job",
    "close_session",
    "seek_search_jobs",
    "seek_get_job_details",
    "seek_close_session",
}


def _list_tools():
    return asyncio.run(mcp.list_tools())


def test_all_tools_are_registered_with_correct_names():
    names = {tool.name for tool in _list_tools()}
    assert names == EXPECTED_TOOL_NAMES


def test_no_unexpected_extra_tools_registered():
    # A future tool addition should update EXPECTED_TOOL_NAMES deliberately,
    # rather than this test silently passing with 7+ registered tools.
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


def test_seek_search_jobs_requires_keywords_only():
    tool = next(t for t in _list_tools() if t.name == "seek_search_jobs")
    assert tool.input_schema["required"] == ["keywords"]
    assert "location" in tool.input_schema["properties"]
    assert "max_results" in tool.input_schema["properties"]


def test_seek_get_job_details_requires_job_id():
    tool = next(t for t in _list_tools() if t.name == "seek_get_job_details")
    assert tool.input_schema["required"] == ["job_id"]


def test_tool_descriptions_are_non_empty():
    # Every tool's docstring becomes its MCP description; an empty one means
    # a future refactor accidentally dropped a tool's docstring.
    for tool in _list_tools():
        assert tool.description
        assert tool.description.strip() != ""


class TestJobIdNormalizedAtToolBoundary:
    """`get_job_details`/`apply_to_job` must run the raw `job_id` through
    `normalize_job_id()` before it ever reaches the extractor, so a
    path-traversal-shaped or URL-shaped id can never leak downstream
    unvalidated. Verified by monkeypatching the extractor to record what it
    was actually called with, and asserting a malformed id fails loudly
    rather than being swallowed into a fabricated result.
    """

    def test_get_job_details_normalizes_before_calling_extractor(self, monkeypatch):
        received = {}

        async def fake_get_job_details(job_id):
            received["job_id"] = job_id
            return {"job_id": job_id}

        monkeypatch.setattr(extractor, "get_job_details", fake_get_job_details)

        asyncio.run(
            mcp.call_tool(
                "get_job_details",
                {"job_id": "https://au.indeed.com/viewjob?jk=abc123def456&tk=xyz"},
            )
        )
        assert received["job_id"] == "abc123def456"

    def test_get_job_details_rejects_malformed_job_id_without_calling_extractor(
        self, monkeypatch
    ):
        called = False

        async def fake_get_job_details(job_id):
            nonlocal called
            called = True
            return {"job_id": job_id}

        monkeypatch.setattr(extractor, "get_job_details", fake_get_job_details)

        with pytest.raises(Exception):
            asyncio.run(
                mcp.call_tool("get_job_details", {"job_id": "../../etc/passwd"})
            )
        assert called is False

    def test_apply_to_job_normalizes_before_calling_extractor(self, monkeypatch):
        received = {}

        async def fake_apply_to_job(job_id, profile):
            received["job_id"] = job_id
            from indeed_mcp_server.contracts import ApplyResult

            return ApplyResult(job_id=job_id, submitted=True)

        monkeypatch.setattr(extractor, "apply_to_job", fake_apply_to_job)

        asyncio.run(
            mcp.call_tool(
                "apply_to_job",
                {
                    "job_id": "https://au.indeed.com/viewjob?jk=zzz999&tk=abc",
                    "full_name": "Test Candidate",
                    "email": "test@example.com",
                    "phone": "0400000000",
                    "resume_path": "/tmp/resume.pdf",
                },
            )
        )
        assert received["job_id"] == "zzz999"

    def test_apply_to_job_rejects_malformed_job_id_without_calling_extractor(
        self, monkeypatch
    ):
        called = False

        async def fake_apply_to_job(job_id, profile):
            nonlocal called
            called = True
            from indeed_mcp_server.contracts import ApplyResult

            return ApplyResult(job_id=job_id, submitted=True)

        monkeypatch.setattr(extractor, "apply_to_job", fake_apply_to_job)

        with pytest.raises(Exception):
            asyncio.run(
                mcp.call_tool(
                    "apply_to_job",
                    {
                        "job_id": "not a valid id!!",
                        "full_name": "Test Candidate",
                        "email": "test@example.com",
                        "phone": "0400000000",
                        "resume_path": "/tmp/resume.pdf",
                    },
                )
            )
        assert called is False


class TestSeekJobIdNormalizedAtToolBoundary:
    """`seek_get_job_details` must run the raw `job_id` through
    `normalize_seek_job_id()` before it ever reaches `seek_extractor`,
    exactly mirroring the discipline already enforced for Indeed's
    `get_job_details` above. This is the exact omission this leaf was
    told not to repeat: normalizing at the tool boundary, not just deep
    inside `seek_identifiers.py`, so a path-traversal-shaped or
    URL-shaped id can never leak downstream unvalidated.
    """

    def test_seek_get_job_details_normalizes_before_calling_extractor(self, monkeypatch):
        received = {}

        async def fake_get_job_details(job_id):
            received["job_id"] = job_id
            return {"job_id": job_id}

        monkeypatch.setattr(seek_extractor, "get_job_details", fake_get_job_details)

        asyncio.run(
            mcp.call_tool(
                "seek_get_job_details",
                {"job_id": "https://www.seek.com.au/job/93326286"},
            )
        )
        assert received["job_id"] == "93326286"

    def test_seek_get_job_details_rejects_malformed_job_id_without_calling_extractor(
        self, monkeypatch
    ):
        called = False

        async def fake_get_job_details(job_id):
            nonlocal called
            called = True
            return {"job_id": job_id}

        monkeypatch.setattr(seek_extractor, "get_job_details", fake_get_job_details)

        with pytest.raises(Exception):
            asyncio.run(
                mcp.call_tool("seek_get_job_details", {"job_id": "../../etc/passwd"})
            )
        assert called is False


class TestScreeningAnswersJsonTypeChecked:
    """apply_to_job must reject a screening_answers_json that decodes to
    something other than a JSON object, rather than letting a non-dict
    (int/list/str) reach ApplicantProfile and crash deep inside
    _answer_screening_questions with a confusing AttributeError - a real
    bug found by an adversarial execution-based fuzzing pass.
    """

    @pytest.mark.parametrize("bad_json", ["42", "[1, 2, 3]", '"just a string"', "null"])
    def test_non_dict_screening_answers_json_raises_before_extractor_call(
        self, monkeypatch, bad_json
    ):
        called = False

        async def fake_apply_to_job(job_id, profile):
            nonlocal called
            called = True
            return None

        monkeypatch.setattr(extractor, "apply_to_job", fake_apply_to_job)

        with pytest.raises(Exception):
            asyncio.run(
                mcp.call_tool(
                    "apply_to_job",
                    {
                        "job_id": "abc123",
                        "full_name": "Jamie Rivers",
                        "email": "jamie@example.test",
                        "phone": "0400000000",
                        "resume_path": "/tmp/does-not-matter.pdf",
                        "screening_answers_json": bad_json,
                    },
                )
            )
        assert called is False
