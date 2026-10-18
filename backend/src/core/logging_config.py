import logging


DEFAULT_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


def normalize_log_format(value: object) -> str:
    candidate = str(value or '').strip()
    if not candidate or candidate.lower() in {'default', 'json', 'plain', 'text'}:
        return DEFAULT_LOG_FORMAT

    try:
        logging.Formatter(candidate)
    except (TypeError, ValueError):
        return DEFAULT_LOG_FORMAT
    return candidate
