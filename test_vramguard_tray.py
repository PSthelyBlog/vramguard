"""Tests for what vramguard-tray shows for each service state.

These need neither GTK nor systemd.
Run with:  python3 -m unittest -v
"""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

import vramguard_tray as tray


class DescribeTest(unittest.TestCase):
    def test_running_shows_the_service_status(self):
        view = tray.describe({"ActiveState": "active", "StatusText": "Holding 1.88 GiB"})
        self.assertEqual(view, ("vramguard-on", "Holding 1.88 GiB", tray.TURN_OFF, True))

    def test_stopped_offers_to_turn_on(self):
        view = tray.describe({"ActiveState": "inactive"})
        self.assertEqual((view.icon, view.action, view.enabled), ("vramguard-off", tray.TURN_ON, True))

    def test_no_action_while_changing_state(self):
        for state in ("activating", "deactivating"):
            with self.subTest(state=state):
                view = tray.describe({"ActiveState": state})
                self.assertEqual(view.icon, "vramguard-busy")
                self.assertFalse(view.enabled)

    def test_failure_can_be_retried(self):
        view = tray.describe({"ActiveState": "failed"})
        self.assertEqual((view.icon, view.action, view.enabled), ("vramguard-failed", tray.TURN_ON, True))

    def test_missing_service_reads_as_off(self):
        self.assertEqual(tray.describe({}).icon, "vramguard-off")


class ParseTest(unittest.TestCase):
    def test_parse_show_keeps_equals_signs_in_values(self):
        props = tray.parse_show("ActiveState=active\nStatusText=a=b\n\n")
        self.assertEqual(props, {"ActiveState": "active", "StatusText": "a=b"})


class IconTest(unittest.TestCase):
    def test_every_state_has_a_valid_icon(self):
        with tempfile.TemporaryDirectory() as d:
            tray.write_icons(d)
            for state in ("active", "activating", "deactivating", "failed", "inactive"):
                ET.parse(os.path.join(d, tray.describe({"ActiveState": state}).icon + ".svg"))


if __name__ == "__main__":
    unittest.main()
