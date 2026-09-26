import pytest

from jobsearch_mcp_server.seek_identifiers import normalize_seek_job_id, seek_job_view_url


class TestNormalizeSeekJobId:
    def test_full_job_url(self):
        url = "https://www.seek.com.au/job/93326286"
        assert normalize_seek_job_id(url) == "93326286"

    def test_bare_id_passthrough(self):
        assert normalize_seek_job_id("93326286") == "93326286"

    def test_bare_path_fragment(self):
        assert normalize_seek_job_id("job/93326286") == "93326286"

    def test_bare_path_fragment_with_leading_slash(self):
        assert normalize_seek_job_id("/job/93326286") == "93326286"

    def test_full_url_with_trailing_slash(self):
        assert normalize_seek_job_id("https://www.seek.com.au/job/93326286/") == "93326286"

    def test_different_domain(self):
        url = "https://au.seek.com/job/11112222"
        assert normalize_seek_job_id(url) == "11112222"

    def test_url_with_non_numeric_id_raises(self):
        with pytest.raises(ValueError):
            normalize_seek_job_id("https://www.seek.com.au/job/abc123")

    def test_garbage_input_raises(self):
        with pytest.raises(ValueError):
            normalize_seek_job_id("this is not a job id at all!!")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            normalize_seek_job_id("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            normalize_seek_job_id("   ")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            normalize_seek_job_id(None)  # type: ignore[arg-type]

    def test_strips_surrounding_whitespace(self):
        assert normalize_seek_job_id("  93326286  ") == "93326286"

    def test_path_traversal_shaped_url_raises(self):
        # regression: mirrors identifiers.py's Indeed path-traversal
        # regression test. The id-like segment right after "job/" being
        # clean numeric digits is not enough - trailing path segments after
        # it must not be silently discarded. This must raise, not return
        # "93326286" or "123" and drop the rest on the floor.
        with pytest.raises(ValueError):
            normalize_seek_job_id("https://www.seek.com.au/job/93326286/../../etc/passwd")

    def test_path_traversal_shaped_url_raises_short_id(self):
        with pytest.raises(ValueError):
            normalize_seek_job_id("https://www.seek.com.au/job/123/../../etc/passwd")

    def test_path_traversal_shaped_fragment_raises(self):
        with pytest.raises(ValueError):
            normalize_seek_job_id("job/93326286/../../etc/passwd")

    def test_bare_id_with_trailing_traversal_raises(self):
        # not a URL, doesn't contain "job/" - falls through to the bare-id
        # branch, which must reject anything not purely numeric.
        with pytest.raises(ValueError):
            normalize_seek_job_id("93326286/../../etc/passwd")

    def test_id_with_special_characters_raises(self):
        with pytest.raises(ValueError):
            normalize_seek_job_id("https://www.seek.com.au/job/abc%00def")


class TestSeekJobViewUrl:
    def test_default_domain(self):
        assert seek_job_view_url("93326286") == "https://www.seek.com.au/job/93326286"

    def test_custom_domain(self):
        assert (
            seek_job_view_url("93326286", domain="au.seek.com")
            == "https://au.seek.com/job/93326286"
        )

    def test_empty_job_id_raises(self):
        with pytest.raises(ValueError):
            seek_job_view_url("")

    def test_empty_domain_raises(self):
        with pytest.raises(ValueError):
            seek_job_view_url("93326286", domain="")

    def test_unlisted_domain_raises(self):
        with pytest.raises(ValueError):
            seek_job_view_url("93326286", domain="evil.example.com")

    def test_roundtrip_with_normalize_seek_job_id(self):
        original_url = "https://www.seek.com.au/job/93326286"
        job_id = normalize_seek_job_id(original_url)
        rebuilt = seek_job_view_url(job_id)
        assert normalize_seek_job_id(rebuilt) == job_id
