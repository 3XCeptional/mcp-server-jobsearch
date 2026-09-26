"""Unit tests for the pure pagination-math helpers in job_policy.py.

Coverage gap closed here: `next_start_offset` had no test file at all
before this leaf, including its `ValueError` guard clauses on invalid
input.
"""

from __future__ import annotations

import pytest

from jobsearch_mcp_server.job_policy import (
    RESULTS_PER_PAGE,
    next_start_offset,
)


class TestNextStartOffset:
    def test_advances_by_results_per_page(self):
        assert next_start_offset(0) == RESULTS_PER_PAGE

    def test_advances_by_custom_page_size(self):
        assert next_start_offset(10, results_per_page=5) == 15

    def test_negative_current_start_raises(self):
        with pytest.raises(ValueError):
            next_start_offset(-1)

    def test_zero_results_per_page_raises(self):
        with pytest.raises(ValueError):
            next_start_offset(0, results_per_page=0)

    def test_negative_results_per_page_raises(self):
        with pytest.raises(ValueError):
            next_start_offset(0, results_per_page=-5)
