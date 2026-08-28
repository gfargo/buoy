"""Tests for buoy.logging_setup."""

import logging

from buoy.logging_setup import setup_logging


class TestSetupLogging:
    def test_sets_buoy_logger_level(self):
        setup_logging("DEBUG")
        assert logging.getLogger("buoy").level == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self):
        setup_logging("NOT_A_LEVEL")
        assert logging.getLogger("buoy").level == logging.INFO

    def test_level_is_case_insensitive(self):
        setup_logging("warning")
        assert logging.getLogger("buoy").level == logging.WARNING

    def test_repeated_calls_do_not_duplicate_handlers(self):
        setup_logging("INFO")
        before = len(logging.getLogger().handlers)
        setup_logging("DEBUG")
        after = len(logging.getLogger().handlers)
        assert after == before
