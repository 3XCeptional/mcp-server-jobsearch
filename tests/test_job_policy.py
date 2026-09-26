"""Unit tests for the pure pagination-math helpers in job_policy.py.

Coverage gap closed here: `next_start_offset` and `pages_needed_for` had no
test file at all before this leaf, including their `ValueError` guard
clauses on invalid input.
"""

from __future__ import annotations

import pytest

from jobsearch_mcp_server.job_policy import (
    RESULTS_PER_PAGE,
    next_start_offset,
    pages_needed_for,
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


class TestPagesNeededFor:
    def test_exact_multiple_of_page_size(self):
        assert pages_needed_for(30, results_per_page=15) == 2

    def test_rounds_up_for_a_remainder(self):
        assert pages_needed_for(31, results_per_page=15) == 3

    def test_single_result_needs_one_page(self):
        assert pages_needed_for(1) == 1

    def test_zero_max_results_raises(self):
        with pytest.raises(ValueError):
            pages_needed_for(0)

    def test_negative_max_results_raises(self):
        with pytest.raises(ValueError):
            pages_needed_for(-3)

    def test_zero_results_per_page_raises(self):
        with pytest.raises(ValueError):
            pages_needed_for(10, results_per_page=0)
