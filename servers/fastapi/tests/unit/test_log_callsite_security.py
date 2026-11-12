import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from services import export_task_service


def test_export_url_log_escapes_controls_without_changing_task(monkeypatch, caplog):
    service = object.__new__(export_task_service.ExportTaskService)
    run = AsyncMock(side_effect=RuntimeError("task control"))
    monkeypatch.setattr(service, "_run_task", run)
    url = "https://presenton.example/pdf-maker?name=deck\nFORGED\r\x1b[2J"
    with caplog.at_level(logging.INFO, logger=export_task_service.LOGGER.name):
        with pytest.raises(RuntimeError, match="task control"):
            asyncio.run(service.export_from_url(url, "Control", "pdf"))
    assert run.call_args.args[0]["url"] == url
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "\\nFORGED\\r\\x1b[2J" in messages[0]
    assert all(character not in messages[0] for character in ("\n", "\r", "\x1b"))
