import logging
from io import StringIO
from unittest.mock import patch

from investigation_server.config import Settings
from investigation_server.logging_config import get_logger, setup_logging


def test_setup_logging_defaults():
    settings = Settings(log_level="INFO")
    logger = setup_logging(settings)

    assert logger.name == "investigation_server"
    assert logger.level == logging.INFO
    assert len(logger.handlers) >= 1
    assert logger.propagate is False


def test_setup_logging_level_debug():
    settings = Settings(log_level="DEBUG")
    logger = setup_logging(settings)

    assert logger.level == logging.DEBUG


def test_get_logger_prefix():
    log1 = get_logger("database.engine")
    assert log1.name == "investigation_server.database.engine"

    log2 = get_logger("investigation_server.cloudwatch.client")
    assert log2.name == "investigation_server.cloudwatch.client"


def test_logging_output_stream():
    settings = Settings(log_level="INFO", log_format="[%(levelname)s] %(message)s")

    # Reset root logger handlers for testing
    root_logger = logging.getLogger("investigation_server")
    root_logger.handlers.clear()

    setup_logging(settings)

    test_stream = StringIO()
    # Replace handler stream with test_stream
    for handler in root_logger.handlers:
        handler.stream = test_stream

    logger = get_logger("test_module")
    logger.info("Hello test log")
    logger.debug("Hidden debug log")

    output = test_stream.getvalue()
    assert "[INFO] Hello test log" in output
    assert "Hidden debug log" not in output
