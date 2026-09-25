import pytest

from indeed_mcp_server.identifiers import job_view_url, normalize_job_id


class TestNormalizeJobId:
    def test_full_viewjob_url(self):
        url = "https://au.indeed.com/viewjob?jk=abc123def456&tk=xyz"
        assert normalize_job_id(url) == "abc123def456"

    def test_bare_id_passthrough(self):
        assert normalize_job_id("abc123def456") == "abc123def456"

    def test_bare_query_fragment(self):
        assert normalize_job_id("jk=abc123def456&tk=xyz") == "abc123def456"

    def test_query_fragment_with_leading_question_mark(self):
        assert normalize_job_id("?jk=abc123def456&tk=xyz") == "abc123def456"

    def test_url_with_only_jk_param(self):
        assert (
            normalize_job_id("https://au.indeed.com/viewjob?jk=deadbeef0011")
            == "deadbeef0011"
        )

    def test_different_domain(self):
        url = "https://www.indeed.com/viewjob?jk=ffff0000aaaa&tk=abc"
        assert normalize_job_id(url) == "ffff0000aaaa"

    def test_url_missing_jk_param_raises(self):
        with pytest.raises(ValueError):
            normalize_job_id("https://au.indeed.com/viewjob?tk=xyz")

    def test_garbage_input_raises(self):
        with pytest.raises(ValueError):
            normalize_job_id("this is not a job id at all!!")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            normalize_job_id("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            normalize_job_id("   ")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            normalize_job_id(None)  # type: ignore[arg-type]

    def test_strips_surrounding_whitespace(self):
        assert normalize_job_id("  abc123def456  ") == "abc123def456"


class TestJobViewUrl:
    def test_default_domain(self):
        assert (
            job_view_url("abc123def456")
            == "https://au.indeed.com/viewjob?jk=abc123def456"
        )

    def test_custom_domain(self):
        assert (
            job_view_url("abc123def456", domain="www.indeed.com")
            == "https://www.indeed.com/viewjob?jk=abc123def456"
        )

    def test_empty_job_id_raises(self):
        with pytest.raises(ValueError):
            job_view_url("")

    def test_empty_domain_raises(self):
        with pytest.raises(ValueError):
            job_view_url("abc123def456", domain="")

    def test_unlisted_domain_raises(self):
        with pytest.raises(ValueError):
            job_view_url("abc123def456", domain="evil.example.com")

    def test_roundtrip_with_normalize_job_id(self):
        from indeed_mcp_server.identifiers import normalize_job_id

        original_url = "https://au.indeed.com/viewjob?jk=abc123def456&tk=xyz"
        job_id = normalize_job_id(original_url)
        rebuilt = job_view_url(job_id)
        assert normalize_job_id(rebuilt) == job_id
