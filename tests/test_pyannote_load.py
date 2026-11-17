"""Shared-machine measurement policy, without waiting in unit tests."""
import _setup  # noqa: F401
from contextlib import redirect_stdout
import io
import sys
import unittest

sys.path.insert(0, str(_setup.ROOT / 'tools'))
import pyannote_load as load  # noqa: E402


class LoadTests(unittest.TestCase):
    def test_exploratory_measurements_can_record_load_without_waiting(self):
        from unittest.mock import patch
        with patch.object(load, 'wait_for_idle', return_value={}) as wait:
            with load.MeasurementLoad(idle_limit=0):
                pass
        self.assertEqual(wait.call_args.kwargs['limit'], 0)

    def test_linux_cpu_totals_do_not_count_guest_ticks_twice(self):
        self.assertEqual(load.linux_cpu_times('cpu 10 2 3 40 5 1 2 7 4 1'), (45, 70))

    def test_linux_process_counters_handle_parentheses_in_names(self):
        fields = ['S'] + ['0'] * 30
        fields[11], fields[12], fields[19] = '100', '50', '1234'
        pid, row = load.linux_process('42 (worker (batch)) ' + ' '.join(fields), 100)
        self.assertEqual(pid, 42)
        self.assertEqual(row, {'ProcessName': 'worker (batch)', 'CPU': 1.5, 'Created': 1234})

    def test_heavy_processes_that_exit_during_a_measurement_are_retained(self):
        before = {1: {'ProcessName': 'worker', 'CPU': 10., 'Created': 1}}
        middle = {1: {'ProcessName': 'worker', 'CPU': 12., 'Created': 1},
                  2: {'ProcessName': 'compiler', 'CPU': 9., 'Created': 2}}
        after = {1: {'ProcessName': 'worker', 'CPU': 14., 'Created': 1}}
        result = load.process_load([before, middle, after], 10., 2, 1)
        self.assertEqual([(p['name'], p['mean_percent']) for p in result],
                         [('compiler', 45.), ('worker', 20.)])
        self.assertTrue(result[0]['exited_before_end'])

    def test_high_load_waits_until_it_falls(self):
        values = iter([55., 40., 25.])
        waited = []
        with redirect_stdout(io.StringIO()):
            result = load.wait_for_idle(lambda: next(values), waited.append, interval=10)
        self.assertEqual(waited, [10, 10])
        self.assertEqual(result, {'initial_percent': 55., 'before_percent': 25., 'wait_seconds': 20})

    def test_high_load_proceeds_after_twenty_minutes_and_records_it(self):
        waited = []
        with redirect_stdout(io.StringIO()):
            result = load.wait_for_idle(lambda: 55., waited.append, interval=10)
        self.assertEqual(sum(waited), 1200)
        self.assertEqual(result['before_percent'], 55.)
        self.assertEqual(result['wait_seconds'], 1200)

    def test_thirty_percent_does_not_wait(self):
        result = load.wait_for_idle(lambda: 30., lambda seconds: self.fail('unexpected wait'))
        self.assertEqual(result['wait_seconds'], 0)
