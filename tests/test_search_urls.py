from urllib.parse import parse_qs, urlparse

import pytest

from jobsearch_mcp_server.search_urls import (
    DATE_POSTED_MAP,
    JOB_TYPE_MAP,
    build_job_search_url,
)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


class TestMaps:
    def test_job_type_map_has_expected_keys(self):
        for key in ("internship", "full_time", "part_time", "contract"):
            assert key in JOB_TYPE_MAP

    def test_job_type_map_uses_indeed_param_values(self):
        assert JOB_TYPE_MAP["full_time"] == "fulltime"
        assert JOB_TYPE_MAP["part_time"] == "parttime"

    def test_date_posted_map_has_expected_keys(self):
        for key in ("today", "past_3_days", "past_week", "past_month"):
            assert key in DATE_POSTED_MAP

    def test_date_posted_map_uses_day_counts(self):
        assert DATE_POSTED_MAP["today"] == "1"
        assert DATE_POSTED_MAP["past_week"] == "7"


class TestBuildJobSearchUrl:
    def test_keywords_only(self):
        url = build_job_search_url("cyber security")
        assert url.startswith("https://au.indeed.com/jobs?")
        q = _query(url)
        assert q["q"] == ["cyber security"]
        assert "l" not in q

    def test_includes_location(self):
        url = build_job_search_url("cyber security", "Sydney NSW")
        q = _query(url)
        assert q["q"] == ["cyber security"]
        assert q["l"] == ["Sydney NSW"]

    def test_job_type_maps_to_jt_param(self):
        url = build_job_search_url("cyber security", job_type="internship")
        q = _query(url)
        assert q["jt"] == ["internship"]

    def test_full_time_job_type(self):
        url = build_job_search_url("engineer", job_type="full_time")
        q = _query(url)
        assert q["jt"] == ["fulltime"]

    def test_unknown_job_type_raises(self):
        with pytest.raises(ValueError):
            build_job_search_url("engineer", job_type="freelance-ish")

    def test_date_posted_maps_to_fromage_param(self):
        url = build_job_search_url("engineer", date_posted="past_week")
        q = _query(url)
        assert q["fromage"] == ["7"]

    def test_unknown_date_posted_raises(self):
        with pytest.raises(ValueError):
            build_job_search_url("engineer", date_posted="last_year")

    def test_radius_km_included(self):
        url = build_job_search_url("engineer", radius_km=25)
        q = _query(url)
        assert q["radius"] == ["25"]

    def test_radius_omitted_when_none(self):
        url = build_job_search_url("engineer")
        q = _query(url)
        assert "radius" not in q

    def test_start_param_included(self):
        url = build_job_search_url("engineer", start=20)
        q = _query(url)
        assert q["start"] == ["20"]

    def test_custom_domain(self):
        url = build_job_search_url("engineer", domain="www.indeed.com")
        assert url.startswith("https://www.indeed.com/jobs?")

    def test_unlisted_domain_raises(self):
        with pytest.raises(ValueError):
            build_job_search_url("engineer", domain="evil.example.com")

    def test_empty_keywords_raises(self):
        with pytest.raises(ValueError):
            build_job_search_url("")

    def test_whitespace_only_keywords_raises(self):
        with pytest.raises(ValueError):
            build_job_search_url("   ")

    def test_lone_utf16_surrogate_in_keywords_raises_value_error(self):
        # A lone surrogate (reachable via a JSON escape like "\ud800" over the
        # MCP transport) is a valid Python string but can't be UTF-8 encoded.
        # urlencode() raises UnicodeEncodeError for this - that must be
        # translated into the ValueError this function's docstring promises,
        # not leaked as a raw encoding exception.
        with pytest.raises(ValueError):
            build_job_search_url("abc\ud800def")

    def test_lone_utf16_surrogate_does_not_raise_unicode_encode_error(self):
        try:
            build_job_search_url("abc\ud800def")
        except ValueError:
            pass
        except UnicodeEncodeError:
            pytest.fail("UnicodeEncodeError leaked instead of being wrapped as ValueError")

    def test_keywords_with_spaces_and_special_chars_are_encoded(self):
        url = build_job_search_url("C++ / AI security & risk", "Sydney, NSW")
        # Raw spaces must not appear unescaped in the URL, and the literal
        # '&' inside the keywords must be percent-encoded rather than being
        # misread as a query-param separator.
        assert " " not in url
        assert "%26" in url
        q = _query(url)
        # parse_qs decodes correctly only if encoding round-trips cleanly.
        assert q["q"] == ["C++ / AI security & risk"]
        assert q["l"] == ["Sydney, NSW"]
        assert len(q["q"]) == 1
        assert len(q["l"]) == 1

    def test_all_params_together(self):
        url = build_job_search_url(
            "cyber security",
            "Sydney NSW",
            job_type="internship",
            date_posted="past_month",
            radius_km=50,
            start=10,
        )
        q = _query(url)
        assert q["q"] == ["cyber security"]
        assert q["l"] == ["Sydney NSW"]
        assert q["jt"] == ["internship"]
        assert q["fromage"] == ["14"]
        assert q["radius"] == ["50"]
        assert q["start"] == ["10"]
