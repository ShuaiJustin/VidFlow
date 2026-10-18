import logging

from src.core.logging_config import DEFAULT_LOG_FORMAT, normalize_log_format


def test_legacy_json_log_format_falls_back_to_a_valid_formatter():
    normalized = normalize_log_format('json')

    assert normalized == DEFAULT_LOG_FORMAT
    logging.Formatter(normalized)


def test_custom_percent_log_format_is_preserved():
    custom = '%(levelname)s:%(message)s'

    assert normalize_log_format(custom) == custom
