from urllib.parse import parse_qs, urlparse

import pytest

from jobsearch_mcp_server.seek_search_urls import (
    DATE_POSTED_MAP,
    WORK_TYPE_MAP,
    build_seek_search_url,
)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


class TestMaps:
    def test_work_type_map_has_expected_keys(self):
        for key in ("full_time", "part_time", "contract", "casual"):
            assert key in WORK_TYPE_MAP

    def test_work_type_map_uses_seek_param_values(self):
        assert WORK_TYPE_MAP["full_time"] == "full-time"
        assert WORK_TYPE_MAP["part_time"] == "part-time"

    def test_date_posted_map_has_expected_keys(self):
        for key in ("today", "past_3_days", "past_week", "past_month"):
            assert key in DATE_POSTED_MAP

    def test_date_posted_map_uses_day_counts(self):
        assert DATE_POSTED_MAP["today"] == "1"
        assert DATE_POSTED_MAP["past_week"] == "7"


class TestBuildSeekSearchUrl:
    def test_keywords_only(self):
        url = build_seek_search_url("cyber security")
        assert url.startswith("https://www.seek.com.au/jobs?")
        q = _query(url)
        assert q["keywords"] == ["cyber security"]
        assert "where" not in q

    def test_includes_location(self):
        url = build_seek_search_url("cyber security", "Sydney NSW")
        q = _query(url)
        assert q["keywords"] == ["cyber security"]
        assert q["where"] == ["Sydney NSW"]

    def test_work_type_maps_to_worktype_param(self):
        url = build_seek_search_url("cyber security", work_type="casual")
        q = _query(url)
        assert q["worktype"] == ["casual"]

    def test_full_time_work_type(self):
        url = build_seek_search_url("engineer", work_type="full_time")
        q = _query(url)
        assert q["worktype"] == ["full-time"]

    def test_unknown_work_type_raises(self):
        with pytest.raises(ValueError):
            build_seek_search_url("engineer", work_type="freelance-ish")

    def test_date_posted_maps_to_dateposted_param(self):
        url = build_seek_search_url("engineer", date_posted="past_week")
        q = _query(url)
        assert q["dateposted"] == ["7"]

    def test_unknown_date_posted_raises(self):
        with pytest.raises(ValueError):
            build_seek_search_url("engineer", date_posted="last_year")

    def test_classification_included(self):
        url = build_seek_search_url("engineer", classification="6281")
        q = _query(url)
        assert q["classification"] == ["6281"]

    def test_classification_omitted_when_none(self):
        url = build_seek_search_url("engineer")
        q = _query(url)
        assert "classification" not in q

    def test_page_param_included_with_default(self):
        url = build_seek_search_url("engineer")
        q = _query(url)
        assert q["page"] == ["1"]

    def test_page_param_included_custom(self):
        url = build_seek_search_url("engineer", page=3)
        q = _query(url)
        assert q["page"] == ["3"]

    def test_custom_domain(self):
        url = build_seek_search_url("engineer", domain="au.seek.com")
        assert url.startswith("https://au.seek.com/jobs?")

    def test_unlisted_domain_raises(self):
        with pytest.raises(ValueError):
            build_seek_search_url("engineer", domain="evil.example.com")

    def test_empty_keywords_raises(self):
        with pytest.raises(ValueError):
            build_seek_search_url("")

    def test_whitespace_only_keywords_raises(self):
        with pytest.raises(ValueError):
            build_seek_search_url("   ")

    def test_keywords_with_spaces_and_special_chars_are_encoded(self):
        url = build_seek_search_url("C++ / AI security & risk", "Sydney, NSW")
        # Raw spaces must not appear unescaped in the URL, and the literal
        # '&' inside the keywords must be percent-encoded rather than being
        # misread as a query-param separator.
        assert " " not in url
        assert "%26" in url
        q = _query(url)
        # parse_qs decodes correctly only if encoding round-trips cleanly.
        assert q["keywords"] == ["C++ / AI security & risk"]
        assert q["where"] == ["Sydney, NSW"]
        assert len(q["keywords"]) == 1
        assert len(q["where"]) == 1

    def test_all_params_together(self):
        url = build_seek_search_url(
            "cyber security",
            "Sydney NSW",
            work_type="full_time",
            date_posted="past_month",
            classification="6281",
            page=2,
        )
        q = _query(url)
        assert q["keywords"] == ["cyber security"]
        assert q["where"] == ["Sydney NSW"]
        assert q["worktype"] == ["full-time"]
        assert q["dateposted"] == ["30"]
        assert q["classification"] == ["6281"]
        assert q["page"] == ["2"]
