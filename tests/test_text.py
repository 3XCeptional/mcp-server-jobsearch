from jobsearch_mcp_server.text import filter_indeed_noise_lines, strip_indeed_noise


class TestFilterIndeedNoiseLines:
    def test_removes_sponsored_label(self):
        lines = ["Sponsored", "Security Analyst", "Acme Corp"]
        assert filter_indeed_noise_lines(lines) == ["Security Analyst", "Acme Corp"]

    def test_removes_ad_label_case_insensitive(self):
        lines = ["ad", "AD", "Ad", "Junior Developer"]
        assert filter_indeed_noise_lines(lines) == ["Junior Developer"]

    def test_removes_save_this_job(self):
        lines = ["Security Analyst", "Save this job", "Acme Corp"]
        assert filter_indeed_noise_lines(lines) == ["Security Analyst", "Acme Corp"]

    def test_removes_report_this_job(self):
        lines = ["Security Analyst", "Report this job", "$80,000 a year"]
        assert filter_indeed_noise_lines(lines) == [
            "Security Analyst",
            "$80,000 a year",
        ]

    def test_removes_cookie_banner_substring(self):
        lines = [
            "Security Analyst",
            "We use cookies to improve your experience on Indeed.",
            "Acme Corp",
        ]
        assert filter_indeed_noise_lines(lines) == ["Security Analyst", "Acme Corp"]

    def test_keeps_legitimate_content_mentioning_similar_words(self):
        # "sponsored" appearing as content inside a real sentence (not the
        # standalone chrome label) should not be treated as an exact-match
        # noise line, since it doesn't fully match the boilerplate phrase.
        lines = ["We offer a sponsored visa pathway for this role."]
        assert filter_indeed_noise_lines(lines) == lines

    def test_keeps_sentence_mentioning_cookies_mid_clause(self):
        # "we use cookies" appears verbatim inside a real, unrelated
        # sentence here - the substring match must not delete the whole
        # line just because the noise phrase happens to appear in it.
        lines = ["We use cookies in our daily bread recipe, not just on websites."]
        assert filter_indeed_noise_lines(lines) == lines

    def test_keeps_sentence_mentioning_report_this_listing_mid_clause(self):
        lines = ["This report this listing service helps job seekers flag issues."]
        assert filter_indeed_noise_lines(lines) == lines

    def test_keeps_blank_lines(self):
        lines = ["Security Analyst", "", "Acme Corp"]
        assert filter_indeed_noise_lines(lines) == lines

    def test_empty_list(self):
        assert filter_indeed_noise_lines([]) == []


class TestStripIndeedNoise:
    def test_strips_multiple_noise_lines(self):
        text = (
            "Sponsored\n"
            "Security Analyst\n"
            "Acme Corp - Sydney NSW\n"
            "Save this job\n"
            "Report this job\n"
            "$90,000 - $110,000 a year\n"
        )
        result = strip_indeed_noise(text)
        assert "Sponsored" not in result
        assert "Save this job" not in result
        assert "Report this job" not in result
        assert "Security Analyst" in result
        assert "Acme Corp - Sydney NSW" in result
        assert "$90,000 - $110,000 a year" in result

    def test_strips_cookie_consent_banner(self):
        text = (
            "Manage cookies to personalize your experience.\n"
            "Accept all cookies\n"
            "Security Analyst role description follows.\n"
        )
        result = strip_indeed_noise(text)
        assert "cookies" not in result.lower()
        assert "Security Analyst role description follows." in result

    def test_no_noise_leaves_text_unchanged(self):
        text = "Security Analyst\nAcme Corp\nGreat role."
        assert strip_indeed_noise(text) == text

    def test_empty_string(self):
        assert strip_indeed_noise("") == ""
