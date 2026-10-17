"""Explicit device doubles for callback-driven Read and Auto Copy tests."""

from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest

from tests.ui.conftest import MockCanvas


@pytest.fixture(autouse=True)
def _isolate_activity_workers(activity_workers):
    """Apply the explicit worker scheduler to this activity test directory."""


@pytest.fixture
def read_flow(monkeypatch, activity_workers):
    """Exercise real activities/widgets while holding device work at the API.

    Tests deliver results through the callbacks registered on Scanner/Reader.
    Polling threads are recorded, not executed: these tests cover callback
    routing, not thread timing, reader fallback timeouts or device operations.
    """
    import actstack
    import appfiles
    import audio
    import audio_copy
    import executor
    import hmi_driver
    import read
    import scan

    actstack._reset()
    monkeypatch.setattr(actstack, '_canvas_factory', MockCanvas)
    scanner = create_autospec(scan.Scanner, instance=True)
    reader = create_autospec(read.Reader, instance=True)
    monkeypatch.setattr(scan, 'Scanner', Mock(return_value=scanner))
    monkeypatch.setattr(read, 'Reader', Mock(return_value=reader))
    monkeypatch.setattr(scan, 'INFOS', None)
    monkeypatch.setattr(appfiles, 'mkdirs_on_icopy', Mock())
    for module in (audio, audio_copy):
        for name in dir(module):
            if name.startswith('play') and callable(getattr(module, name)):
                monkeypatch.setattr(module, name, Mock())
    stop_pm3 = Mock()
    monkeypatch.setattr(executor, 'stopPM3Task', stop_pm3)
    monkeypatch.setattr(hmi_driver, 'presspm3', Mock())

    def scan_result(activity, result):
        scan.setScanCache(result)
        scanner.call_resulted(result)
        activity.getCanvas().advance(250)

    def read_result(activity, result):
        reader.call_reading(result)
        activity.getCanvas().advance(250)

    yield SimpleNamespace(scanner=scanner, reader=reader, workers=activity_workers,
                          scan_result=scan_result, read_result=read_result,
                          stop_pm3=stop_pm3)
    actstack._reset()
