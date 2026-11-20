import json

from capture.control import StartError
from capture.preview import preview
from capture.session import SessionStateError
from device.events import ControlEvent, SampleEvent
from device.playback import PlaybackStep, run_playback
from device.port import DeviceRejectedError
from device.source import SampleRejectedError
from jobs.enrollment import prepare_start, register
from jobs.validation import RegistrationError
from tests.support import BenchCase, CHANNEL


class RunTests(BenchCase):
    def test_live_preview(self):
        self.register('live')
        session_id = self.start('live')
        self.assertEqual(self.feed(4, 9, 9), (1, 2, 3))
        first = preview(self.sessions, self.device, session_id)
        second = preview(self.sessions, self.device, session_id)
        expected = {'count': 3, 'first': 1, 'last': 3, 'peak': 9, 'peak_number': 2}

        def meter_fields(snapshot):
            return {
                'count': snapshot.count, 'first': snapshot.first_sample_no,
                'last': snapshot.last_sample_no, 'peak': snapshot.peak,
                'peak_number': snapshot.peak_sample_no,
            }

        self.assertEqual(meter_fields(first), expected)
        self.assertEqual(first, second)
        self.assertEqual(first.peak_sample.value, 9)
        self.assertEqual(first.peak_sample.channel, CHANNEL)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v1', 'input': [4, 9, 9], 'expected': expected,
            'actual': meter_fields(first), 'downstream_expected': expected,
            'downstream_actual': meter_fields(second),
            'code_paths': ['device/source.py', 'device/registers.py', 'capture/preview.py'],
            'oracle': '三条读数，第二条首次达到最大值 9。两次读表之间无新输入。',
        }, sort_keys=True))

    def test_sample_rejection(self):
        self.register('samples')
        session_id = self.start('samples')
        with self.device.subscribe() as subscription:
            self.assertEqual(self.feed(4), (1,))
            before = self.device.read_slot()
            for channel, value in [('other', 7), (CHANNEL, -1), (CHANNEL, 3.5), (CHANNEL, True)]:
                with self.subTest(channel=channel, value=value):
                    with self.assertRaises(SampleRejectedError):
                        self.device.sample(channel, value)
                    self.assertEqual(self.device.read_slot(), before)
                    self.assertEqual(self.device.last_sample_no, 1)
            self.assertEqual(self.feed(9), (2,))
            evidence = self.stop(session_id)
            with self.assertRaises(SampleRejectedError):
                self.feed(20)
            self.assertEqual(self.device.last_sample_no, 2)
            self.assertEqual(self.device.read_slot(), evidence.original.snapshot)
            events = subscription.drain()
        samples = [event for event in events if isinstance(event, SampleEvent)]
        self.assertEqual([(event.sample_no, event.value) for event in samples], [(1, 4), (2, 9)])
        self.assertEqual([event.action for event in events if isinstance(event, ControlEvent)], ['stop'])

    def test_device_primitives(self):
        with self.device.subscribe() as from_start:
            start_event = self.device.start_sampling(CHANNEL)
            played = run_playback(self.device, (
                PlaybackStep('sample', CHANNEL, 4), PlaybackStep('sample', CHANNEL, 9),
                PlaybackStep('sample', CHANNEL, 9), PlaybackStep('read', slot=0),
                PlaybackStep('switch', slot=1), PlaybackStep('sample', CHANNEL, 12),
                PlaybackStep('sample', CHANNEL, 5), PlaybackStep('read', slot=0),
                PlaybackStep('read', slot=1),
            ))
            first_read, inactive, active = played.reads
            self.assertEqual(first_read, inactive)
            self.assertEqual((inactive.count, inactive.peak, inactive.peak_sample_no), (3, 9, 2))
            self.assertEqual((active.count, active.first_sample_no, active.peak, active.peak_sample_no), (2, 4, 12, 4))
            events = from_start.drain()
            self.assertEqual(events, (start_event,) + played.events)
            self.assertEqual([event.sequence for event in events], list(range(1, 8)))
            edge = events[4]
            self.assertIsInstance(edge, ControlEvent)
            self.assertEqual((edge.action, edge.last_sample_no, edge.previous_slot, edge.slot), ('switch', 3, 0, 1))
            with self.device.subscribe() as late:
                self.assertEqual(late.drain(), ())
                with self.assertRaises(DeviceRejectedError):
                    self.device.switch_write_slot(0)
                with self.assertRaises(DeviceRejectedError):
                    self.device.reset_idle_slot(1)
                reset_event = self.device.reset_idle_slot(0)
                self.assertEqual(self.device.read_slot(0).count, 0)
                self.assertEqual(self.device.read_slot(1), active)
                self.assertEqual(late.drain(), (reset_event,))
            self.assertEqual(from_start.drain(), (reset_event,))
        self.assertEqual(self.device.actions, (start_event, edge, reset_event))
        self.assertEqual(self.feed(8), (6,))

    def test_preview_state(self):
        self.register('preview-state')
        session_id = self.start('preview-state')
        self.feed(2)
        before = self.device.actions
        with self.device.subscribe() as subscription:
            meter = preview(self.sessions, self.device, session_id)
            self.assertEqual(meter.count, 1)
            with self.assertRaises(SessionStateError):
                preview(self.sessions, self.device, 'unknown')
            self.assertEqual(subscription.drain(), ())
        self.assertEqual(self.device.actions, before)
        self.assertEqual(self.device.last_sample_no, 1)
        self.stop(session_id)
        with self.assertRaises(SessionStateError):
            preview(self.sessions, self.device, session_id)

    def test_invalid_limit(self):
        with self.assertRaises(RegistrationError):
            self.register('corrected', -1)
        self.assertIsNone(self.catalog.get('corrected'))
        self.assertEqual(self.register('corrected', 9).limit, 9)

    def test_input_checks(self):
        for channel, limit in [('not-registered', 9), (CHANNEL, True), (CHANNEL, 9.0), (CHANNEL, '9')]:
            with self.subTest(channel=channel, limit=limit):
                with self.assertRaises(RegistrationError):
                    register(self.catalog, self.channels, 'input-check', channel, limit)
                self.assertIsNone(self.catalog.get('input-check'))
        original = self.register('input-check', 0)
        with self.assertRaises(RegistrationError):
            self.register('input-check', 100)
        self.assertEqual(self.catalog.get('input-check'), original)
        self.assertEqual(original.channel, CHANNEL)

    def test_device_busy(self):
        self.register('first')
        self.register('second')
        first = self.start('first')
        with self.assertRaises(StartError):
            self.start('second')
        self.assertEqual(self.feed(1), (1,))
        self.assertEqual(preview(self.sessions, self.device, first).count, 1)

    def test_active_owner(self):
        self.register('A')
        self.register('B')
        active = self.start('A')
        self.feed(4)
        before = self.device.actions
        with self.assertRaises(StartError):
            self.start('B')
        self.assertEqual(self.sessions.current.session_id, active)
        self.assertEqual(self.device.actions, before)
        self.assertEqual(self.feed(12), (2,))
        self.assertEqual(preview(self.sessions, self.device, active).count, 2)
        self.assertEqual(prepare_start(self.catalog, 'B').trial_id, 'B')
        self.issue(active, self.stop(active))
        self.reset(active)
        second = self.start('B')
        self.assertEqual(self.sessions.current.session_id, second)
        self.assertEqual(preview(self.sessions, self.device, second).count, 0)

    def test_start_rejections(self):
        with self.assertRaises(StartError):
            self.start('unknown')
        self.assertIsNone(self.sessions.current)
        self.device.start_sampling(CHANNEL)
        self.feed(6)
        self.device.switch_write_slot(1)
        self.device.release_stopped_slot(self.device.stop_sampling())
        self.assertEqual(self.device.read_slot(0).count, 1)
        self.register('after-cleaning')
        before = self.device.actions
        with self.assertRaises(StartError):
            self.start('after-cleaning')
        self.assertEqual(self.device.actions, before)
        self.assertIsNone(self.sessions.current)
        self.assertFalse(self.catalog.has_started('after-cleaning'))
        self.device.reset_idle_slot(0)
        started = self.start('after-cleaning')
        self.assertEqual(preview(self.sessions, self.device, started).count, 0)
        self.assertEqual(self.feed(7), (2,))
