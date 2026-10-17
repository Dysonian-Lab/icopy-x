"""Regressions for scan-result classification and ReadActivity cancellation."""

import pytest
import actstack
import scan
from activity_read import ReadActivity
from _constants import KEY_PWR


def start_read():
    return actstack.start_activity(ReadActivity, {'tag_type': 1})


@pytest.mark.parametrize('result', [
    # This is scan.createTagMulti()'s actual contract: found is True.
    {'progress': 100, 'return': -3, 'found': True, 'type': -1},
    {'return': -3, 'found': False, 'type': -1},
    # Preserve the legacy callback shape too.
    {'found': False, 'hasMulti': True},
])
def test_multiple_tags_never_start_a_reader(read_flow, result):
    activity = start_read()
    read_flow.scan_result(activity, result)
    read_flow.reader.start.assert_not_called()
    assert activity.state == 'no_tag'
    assert 'Multiple tags detected!' in activity.getCanvas().get_all_text()
    assert 'Rescan' in activity.getCanvas().get_all_text()
    assert not activity.isbusy()


def test_scan_multi_fixture_matches_middleware_contract():
    assert scan.createTagMulti(100) == {
        'progress': 100, 'return': -3, 'found': True, 'type': -1,
    }


def test_power_cancels_scanner_using_its_public_api(read_flow):
    activity = start_read()
    activity.callKeyEvent(KEY_PWR)
    read_flow.scanner.scan_stop.assert_called_once_with()
    read_flow.stop_pm3.assert_called_once_with()
    assert activity._scanner is None
    assert actstack.get_current_activity() is None


def test_repeated_scan_cancellation_is_idempotent(read_flow):
    activity = start_read()
    activity._cancelScan()
    activity._cancelScan()
    read_flow.scanner.scan_stop.assert_called_once_with()
    assert activity._scanner is None


def test_scan_cancellation_error_still_releases_activity(read_flow):
    read_flow.scanner.scan_stop.side_effect = RuntimeError('scanner disconnected')
    activity = start_read()
    activity.callKeyEvent(KEY_PWR)
    read_flow.scanner.scan_stop.assert_called_once_with()
    assert activity._scanner is None
    assert actstack.get_current_activity() is None
