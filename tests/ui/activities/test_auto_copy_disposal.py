"""Immediate exit must dispose timers and reject late worker callbacks."""
import pytest

import actstack
from activity_main import AutoCopyActivity
from _constants import KEY_PWR
from tests.ui.conftest import MockCanvas


HF = {'found': True, 'return': 0, 'type': 2, 'uid': '04112233445566'}


def reading(read_flow):
    activity = actstack.start_activity(AutoCopyActivity)
    read_flow.scan_result(activity, HF.copy())
    assert activity.state == 'reading'
    return activity


def test_immediate_exit_after_partial_read_cancels_owned_animation(read_flow):
    activity = reading(read_flow)
    canvas = activity.getCanvas()
    # A real progress callback schedules animation; do not advance it away.
    read_flow.reader.call_reading({'progress': 75})
    read_flow.reader.call_reading({'success': True, 'partial': True, 'bundle': 'partial.bin'})
    assert activity.state == 'place_card'
    assert canvas._timers, 'Negative control must have an unfinished animation'
    activity.callKeyEvent(KEY_PWR)
    assert not canvas._timers
    canvas.advance(1000)
    assert canvas.get_all_text() == []


@pytest.mark.parametrize('phase', ['scan', 'read'])
def test_destroy_stops_owned_worker_and_fake_timer(read_flow, monkeypatch, phase):
    root = MockCanvas()
    monkeypatch.setattr(actstack, '_root', root)
    activity = actstack.start_activity(AutoCopyActivity)
    if phase == 'read':
        read_flow.scanner.call_resulted(HF.copy())
        root.advance(0)
        activity.getCanvas().advance(250)
        assert root._timers
    activity.callKeyEvent(KEY_PWR)
    assert not root._timers
    read_flow.scanner.scan_stop.assert_called_once_with()
    if phase == 'read':
        read_flow.reader.stop.assert_called_once_with()
    assert activity.state == 'cancelled'


@pytest.mark.parametrize('callback', ['scan', 'read', 'error'])
def test_late_callback_cannot_revive_destroyed_activity(read_flow, callback):
    activity = reading(read_flow)
    canvas = activity.getCanvas()
    activity.callKeyEvent(KEY_PWR)
    before = (activity.state, read_flow.reader.start.call_count)
    if callback == 'scan':
        activity.onScanFinish(HF.copy())
    elif callback == 'read':
        activity.onReading({'success': False, 'return': -3})
    else:
        activity._onReadException(RuntimeError('late error'))
    canvas.advance(500)
    assert actstack.get_current_activity() is None
    assert not canvas._timers and not canvas.get_all_text()
    assert (activity.state, read_flow.reader.start.call_count) == before


def test_completion_queued_before_exit_is_rechecked_on_ui_delivery(read_flow, monkeypatch):
    activity = reading(read_flow)
    root = MockCanvas()
    monkeypatch.setattr(actstack, '_root', root)
    activity.onReading({'success': False, 'return': -3})
    assert root._timers
    activity.callKeyEvent(KEY_PWR)
    root.advance(1000)
    assert actstack.get_current_activity() is None
    assert activity.state == 'cancelled'


def test_finished_activity_releases_timers_if_scanner_stop_raises(read_flow):
    activity = reading(read_flow)
    read_flow.reader.call_reading({'progress': 75})
    read_flow.scanner.scan_stop.side_effect = RuntimeError('stop failed')
    activity.callKeyEvent(KEY_PWR)
    assert activity.life.destroyed
    assert not activity.getCanvas()._timers
    read_flow.reader.stop.assert_called_once_with()
