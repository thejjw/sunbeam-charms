# Copyright 2022 liam
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import Mock

from charm import SunbeamOvnCentralOperatorCharm
from ops.testing import Harness


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(SunbeamOvnCentralOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_config_changed(self):
        self.assertEqual(list(self.harness.charm._stored.things), [])
        self.harness.update_config({"thing": "foo"})
        self.assertEqual(list(self.harness.charm._stored.things), ["foo"])

    def test_action(self):
        # the harness doesn't (yet!) help much with actions themselves
        action_event = Mock(params={"fail": ""})
        self.harness.charm._on_fortune_action(action_event)

        self.assertTrue(action_event.set_results.called)

    def test_action_fail(self):
        action_event = Mock(params={"fail": "fail this"})
        self.harness.charm._on_fortune_action(action_event)
