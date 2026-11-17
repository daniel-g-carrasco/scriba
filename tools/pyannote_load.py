"""Development benchmark load sampling on Windows and Linux.

Read-only OS queries: system CPU once per second, process CPU totals every
five seconds. No command lines, user files or process control.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading
import time
import sys


def wait_for_idle(sample, sleep=time.sleep, interval=10, limit=1200):
    initial = current = sample()
    waited = 0
    while current > 30 and waited < limit:
        print(f'Shared CPU {current:.1f}%, waiting ({waited}/{limit}s)', flush=True)
        delay = min(interval, limit - waited)
        sleep(delay)
        waited += delay
        current = sample()
    return {'initial_percent': initial, 'before_percent': current, 'wait_seconds': waited}


def linux_cpu_times(line):
    # https://docs.kernel.org/filesystems/proc.html (CPU accounting fields).
    fields = line.split()
    if fields[0] != 'cpu' or len(fields) < 9:
        raise ValueError('Invalid /proc/stat CPU counters')
    values = [int(value) for value in fields[1:9]]
    return values[3] + values[4], sum(values)


def linux_process(line, ticks):
    prefix, name_and_fields = line.split('(', 1)
    name, fields = name_and_fields.rsplit(')', 1)
    fields = fields.split()
    if len(fields) < 20:
        raise ValueError('Invalid /proc process counters')
    return int(prefix), {'ProcessName': name, 'CPU': (int(fields[11]) + int(fields[12])) / ticks,
                         'Created': int(fields[19])}


def cpu_times():
    if sys.platform != 'win32':
        with Path('/proc/stat').open() as stream:
            return linux_cpu_times(stream.readline())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemTimes.argtypes = [ctypes.POINTER(wintypes.FILETIME)] * 3
    kernel.GetSystemTimes.restype = wintypes.BOOL
    idle, system, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(system), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    def number(value):
        return (value.dwHighDateTime << 32) + value.dwLowDateTime
    return number(idle), number(system) + number(user)


def cpu_percent(left, right):
    total = right[1] - left[1]
    return 100 * (1 - (right[0] - left[0]) / total) if total else 0.


def processes():
    if sys.platform != 'win32':
        result, ticks = {}, os.sysconf('SC_CLK_TCK')
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                pid, row = linux_process((entry / 'stat').read_text(errors='replace'), ticks)
            except (OSError, ValueError):
                continue  # Protected processes and processes exiting during sampling.
            result[pid] = row
        return result
    kernel, psapi = ctypes.WinDLL('kernel32'), ctypes.WinDLL('psapi')
    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    psapi.EnumProcesses.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    capacity = 4096
    while True:
        ids, needed = (wintypes.DWORD * capacity)(), wintypes.DWORD()
        if not psapi.EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(needed)):
            raise ctypes.WinError()
        if needed.value < ctypes.sizeof(ids):
            break
        capacity *= 2
    result = {}
    for pid in ids[:needed.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            continue  # Protected system processes are not inspectable.
        try:
            name, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            created, ended, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                continue
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(system), ctypes.byref(user)):
                continue
            def number(value):
                return (value.dwHighDateTime << 32) + value.dwLowDateTime
            result[pid] = {'ProcessName': Path(name.value).stem, 'CPU': (number(system) + number(user)) / 1e7,
                           'Created': number(created)}
        finally:
            kernel.CloseHandle(handle)
    return result


def update_processes(totals, snapshot, initial=False):
    for pid, process in snapshot.items():
        key = pid, process['Created']
        if key not in totals:
            totals[key] = [process['CPU'] if initial else 0., process]
        totals[key][1] = process


def summarize_processes(totals, final_snapshot, elapsed, cpus, benchmark_pid):
    result = []
    for (pid, created), (baseline, process) in totals.items():
        percent = 100 * max(0., process['CPU'] - baseline) / elapsed / cpus
        if percent >= 1:
            final = final_snapshot.get(pid)
            result.append({'pid': pid, 'name': process['ProcessName'], 'mean_percent': percent,
                           'benchmark_process': pid == benchmark_pid,
                           'exited_before_end': final is None or final['Created'] != created})
    return sorted(result, key=lambda p: -p['mean_percent'])


def process_load(snapshots, elapsed, cpus, benchmark_pid):
    totals = {}
    for index, snapshot in enumerate(snapshots):
        update_processes(totals, snapshot, initial=index == 0)
    return summarize_processes(totals, snapshots[-1], elapsed, cpus, benchmark_pid)


class MeasurementLoad:
    def __init__(self, idle_limit=1200):
        self.idle_limit = idle_limit

    def __enter__(self):
        previous = [cpu_times(), time.perf_counter()]
        def sample():
            # A later poll already spans the wait interval. Do not add a second
            # of sleep at each poll on top of the twenty-minute wait budget.
            remaining = 1 - (time.perf_counter() - previous[1])
            if remaining > 0:
                time.sleep(remaining)
            now = cpu_times()
            value = cpu_percent(previous[0], now)
            previous[:] = [now, time.perf_counter()]
            return value
        self.report = wait_for_idle(sample, limit=self.idle_limit)
        self.process_totals = {}
        update_processes(self.process_totals, processes(), initial=True)
        self.before = cpu_times()
        self.started = time.perf_counter()
        self.samples = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.thread.start()
        return self

    def _watch(self):
        while not self.stop.wait(1):
            self.samples.append(cpu_times())
            if len(self.samples) % 5 == 0:
                update_processes(self.process_totals, processes())

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()
        after = cpu_times()
        elapsed = time.perf_counter() - self.started
        self.report.update(mean_percent=cpu_percent(self.before, after),
                           sampled_seconds=elapsed, samples=len(self.samples))
        final = processes()
        update_processes(self.process_totals, final)
        self.report['heavy_processes'] = summarize_processes(self.process_totals, final, elapsed, os.cpu_count(), os.getpid())
        self.report['process_sampling'] = '5 second native read-only snapshots; exited process CPU is a lower bound; sub-5s and protected processes may be missed'
