"""Device selection on synthetic lists, including defaults that point to
silent speakers or a distant webcam instead of the call headset."""

import unittest
from dataclasses import replace

import _setup  # noqa: F401

from scriba.core import devices as dev
from scriba.core.devices import INPUT, OUTPUT, Device

NONE = "00000000-0000-0000-ffff-ffffffffffff"
SPEAKERS = Device("out-speakers", "Altoparlanti (Realtek(R) Audio)", OUTPUT, default=True, container=NONE)
HEADSET = Device("out-headset", "Cuffie (Headset BT)", OUTPUT, communications=True, container="headset", bluetooth=True,
                 programs=(r"C:\Program Files\WindowsApps\MSTeams\ms-teams.exe",))
WEBCAM = Device("in-webcam", "Microfono (HD Webcam)", INPUT, default=True, container="webcam")
ARRAY = Device("in-array", "Gruppo microfoni (Tecnologia Intel® Smart Sound)", INPUT, container=NONE)
HEADSET_MIC = Device("in-headset", "Cuffia auricolare (Headset BT)", INPUT, communications=True, container="headset",
                     bluetooth=True, programs=("ms-teams.exe",))
THE_CALL = [SPEAKERS, HEADSET, ARRAY, HEADSET_MIC, WEBCAM]


class TheLiveCallTest(unittest.TestCase):
    def test_the_call_s_output_is_the_headset_teams_plays_on_not_the_silent_default(self):
        choice = dev.choose_output(THE_CALL, peaks={"out-speakers": -700.0, "out-headset": -18.0})
        self.assertEqual(choice.device, HEADSET)
        self.assertEqual(choice.reasons[0], dev.R_CALL)
        self.assertIn(dev.R_SOUND, choice.reasons)

    def test_the_microphone_is_the_headset_s_not_the_webcam_that_is_the_default(self):
        choice = dev.choose_input(THE_CALL, HEADSET)
        self.assertEqual(choice.device, HEADSET_MIC)
        self.assertEqual(choice.reasons[:2], (dev.R_CALL, dev.R_SAME_DEVICE))

    def test_every_output_is_recorded_with_the_call_s_first(self):
        plan = dev.plan(THE_CALL, peaks={"out-headset": -18.0})
        self.assertEqual(plan.outputs, (HEADSET, SPEAKERS))
        self.assertEqual(plan.input, HEADSET_MIC)
        self.assertTrue(plan.automatic_input and plan.automatic_output)

    def test_without_teams_the_headset_is_still_found_by_its_sound_and_its_microphone_by_the_device(self):
        quiet = [SPEAKERS, Device("out-headset", "Cuffie (Headset BT)", OUTPUT, container="headset"),
                 WEBCAM, Device("in-headset", "Cuffia auricolare (Headset BT)", INPUT, container="headset")]
        output = dev.choose_output(quiet, peaks={"out-headset": -25.0, "out-speakers": -700.0}).device
        self.assertEqual(output.id, "out-headset")
        self.assertEqual(dev.choose_input(quiet, output).device.id, "in-headset")

    def test_with_no_container_the_name_in_brackets_tells_one_device(self):
        a = Device("a", "Cuffie (Headset BT)", OUTPUT)
        b = Device("b", "Cuffia auricolare (Headset BT)", INPUT)
        c = Device("c", "Microfono (HD Webcam)", INPUT)
        self.assertTrue(dev.same_device(a, b))
        self.assertFalse(dev.same_device(a, c))
        self.assertFalse(dev.same_device(Device("x", "Microphone", INPUT), Device("y", "Speakers", OUTPUT)))


class ChoiceTest(unittest.TestCase):
    def test_the_voice_heard_much_louder_on_one_microphone_wins_over_the_defaults(self):
        found = [SPEAKERS, WEBCAM, ARRAY]
        self.assertEqual(dev.choose_input(found).device, WEBCAM)      # the default, nothing else known
        choice = dev.choose_input(found, voices={"in-webcam": -46.0, "in-array": -24.0})
        self.assertEqual(choice.device, ARRAY)
        self.assertEqual(choice.reasons, (dev.R_VOICE,))
        # a few dB apart is the same room, not the user's microphone
        self.assertEqual(dev.choose_input(found, voices={"in-webcam": -30.0, "in-array": -26.0}).device, WEBCAM)
        # and noise is not a voice
        self.assertEqual(dev.choose_input(found, voices={"in-webcam": -75.0, "in-array": -55.0}).device, WEBCAM)

    def test_a_browser_counts_for_less_than_a_call_program(self):
        video = Device("o1", "Altoparlanti", OUTPUT, default=True, programs=("chrome.exe",))
        call = Device("o2", "Cuffie", OUTPUT, programs=("Zoom.exe",))
        self.assertEqual(dev.choose_output([video, call]).device, call)
        self.assertEqual(dev.program_name(r"C:\Users\x\AppData\Roaming\Zoom\bin\Zoom.exe"), "zoom")

    def test_the_communications_default_beats_the_multimedia_one(self):
        a = Device("o1", "Altoparlanti", OUTPUT, default=True)
        b = Device("o2", "Cuffie", OUTPUT, communications=True)
        self.assertEqual(dev.choose_output([a, b]).device, b)

    def test_between_equals_the_system_s_order_and_one_device_is_the_only_one(self):
        a, b = Device("o1", "A", OUTPUT), Device("o2", "B", OUTPUT)
        self.assertEqual(dev.choose_output([a, b]).device, a)
        self.assertEqual(dev.choose_output([b]).reasons, (dev.R_ONLY,))
        self.assertIsNone(dev.choose_output([WEBCAM]).device)

    def test_a_device_chosen_by_hand_is_recorded_alone_and_one_that_is_gone_falls_back(self):
        plan = dev.plan(THE_CALL, input_id="in-webcam", output_id="out-speakers")
        self.assertEqual((plan.input, plan.outputs), (WEBCAM, (SPEAKERS,)))
        self.assertFalse(plan.automatic_input or plan.automatic_output)
        plan = dev.plan(THE_CALL, input_id="unplugged", output_id="unplugged")
        self.assertEqual(plan.input, HEADSET_MIC)
        self.assertTrue(plan.automatic_input and plan.automatic_output)

    def test_the_switches_leave_a_side_out(self):
        self.assertIsNone(dev.plan(THE_CALL, input_on=False).input)
        self.assertEqual(dev.plan(THE_CALL, output_on=False).outputs, ())

    def test_the_microphone_teams_has_open_beats_the_laptop_s_with_every_other_sign(self):
        # the laptop's array: same device as the loudspeakers the call plays on, default for both roles, the
        # voice loudest there; the webcam's microphone has nothing but Teams (13/11/2026: Teams was on it)
        speakers = Device("out", "Altoparlanti (Tecnologia Intel® Smart Sound)", OUTPUT, default=True,
                          programs=("ms-teams.exe",))
        array = Device("in-array", "Gruppo microfoni (Tecnologia Intel® Smart Sound)", INPUT, default=True,
                       communications=True)
        webcam = Device("in-webcam", "Microfono (HD Webcam)", INPUT, container="webcam",
                        programs=("ms-teams.exe",))
        choice = dev.choose_input([speakers, array, webcam], speakers, voices={"in-array": -20.0, "in-webcam": -40.0})
        self.assertEqual(choice.device, webcam)
        self.assertEqual(choice.reasons, (dev.R_CALL,))

    def test_a_bluetooth_microphone_nobody_uses_is_not_opened_for_its_meter(self):
        idle = Device("in-headset", "Cuffia auricolare (Headset BT)", INPUT, bluetooth=True)
        self.assertFalse(dev.may_open(idle))
        self.assertTrue(dev.may_open(HEADSET_MIC))       # Teams has it open: it is in the hands-free mode already
        self.assertTrue(dev.may_open(HEADSET))           # an output's loopback changes nothing
        self.assertTrue(dev.may_open(WEBCAM))


class FollowTheCallTest(unittest.TestCase):
    """The listings the recorder sees while recording, changing as a call begins after Record was pressed."""

    ARRAY = Device("in-array", "Gruppo microfoni (Tecnologia Intel® Smart Sound)", INPUT, default=True,
                   communications=True)
    WEBCAM = Device("in-webcam", "Microfono (HD Webcam)", INPUT)
    TEAMS = (r"C:\Program Files\WindowsApps\MSTeams\ms-teams.exe",)

    def listing(self, array=(), webcam=()):
        return [SPEAKERS, replace(self.ARRAY, programs=array), replace(self.WEBCAM, programs=webcam)]

    def test_teams_opening_the_webcam_s_microphone_is_followed_after_two_looks(self):
        follow = dev.Follow()
        timeline = [self.listing(), self.listing(), self.listing(webcam=self.TEAMS),
                    self.listing(webcam=self.TEAMS)]
        answers = [follow.look(found, "in-array") for found in timeline]
        self.assertEqual([a.id if a else None for a in answers], [None, None, None, "in-webcam"])
        self.assertEqual(dev.call_program(answers[-1]), "Teams")
        # once the recording is on the webcam, Teams' session there is no reason to move again
        self.assertIsNone(follow.look(self.listing(webcam=self.TEAMS), "in-webcam"))
        self.assertIsNone(follow.look(self.listing(webcam=self.TEAMS), "in-webcam"))

    def test_a_microphone_opened_for_a_moment_or_by_a_browser_or_by_another_program_is_not_followed(self):
        follow = dev.Follow()
        for found in (self.listing(webcam=self.TEAMS), self.listing(), self.listing(webcam=self.TEAMS),
                      self.listing(webcam=("chrome.exe",)), self.listing(webcam=("chrome.exe",)),
                      self.listing(webcam=("Scriba.exe",)), self.listing(webcam=("Scriba.exe",))):
            self.assertIsNone(follow.look(found, "in-array"))

    def test_teams_on_the_recorded_microphone_too_keeps_it(self):
        follow = dev.Follow()
        both = self.listing(array=self.TEAMS, webcam=self.TEAMS)
        self.assertIsNone(follow.look(both, "in-array"))
        self.assertIsNone(follow.look(both, "in-array"))


if __name__ == "__main__":
    unittest.main()
