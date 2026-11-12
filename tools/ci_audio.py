#!/usr/bin/env python3
"""Run Linux GUI checks with a private PulseAudio null sink, never host audio.

No default server configuration or hardware modules are loaded. Readiness
requires a working private protocol and the expected null sink, not a delay.
Both the test command and server are reaped on failure, timeout or interruption.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SINK = "scriba_ci"


def stop(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()


def ready(server, environment, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("the private audio server stopped before it was ready")
        try:
            info = subprocess.run(["pactl", "info"], env=environment, capture_output=True, text=True, timeout=2)
            sinks = subprocess.run(["pactl", "list", "short", "sinks"], env=environment,
                                   capture_output=True, text=True, timeout=2)
            null_sink = any(len(parts := line.split("\t")) >= 3 and parts[1] == SINK
                            and parts[2] == "module-null-sink.c" for line in sinks.stdout.splitlines())
            if (info.returncode == sinks.returncode == 0 and null_sink
                    and f"Default Sink: {SINK}" in info.stdout.splitlines()):
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(.05)
    raise RuntimeError("the expected private null sink did not become ready")


def run(command, log, *, timeout=None, ready_timeout=10):
    if not command:
        raise ValueError("a command is required")
    with tempfile.TemporaryDirectory(prefix="scriba-ci-audio-") as temporary:
        root = Path(temporary)
        for name in ("home", "config", "state", "runtime"):
            (root / name).mkdir(mode=0o700)
        endpoint = "unix:" + str(root / "native")
        environment = dict(os.environ, PULSE_SERVER=endpoint, PULSE_SINK=SINK, PULSE_SOURCE=SINK + ".monitor")
        server_environment = dict(environment, HOME=str(root / "home"), XDG_CONFIG_HOME=str(root / "config"),
                                  PULSE_RUNTIME_PATH=str(root / "runtime"), PULSE_STATE_PATH=str(root / "state"))
        for name in ("PULSE_CLIENTCONFIG", "PULSE_COOKIE"):
            server_environment.pop(name, None)
        query_environment = dict(environment, LC_ALL="C")
        server = child = None
        with Path(log).open("xb") as output:
            try:
                server = subprocess.Popen([
                    "pulseaudio", "-n", "--daemonize=no", "--use-pid-file=no", "--exit-idle-time=-1",
                    "--log-target=stderr", "--load=module-native-protocol-unix socket=" + str(root / "native")
                    + " auth-anonymous=1 auth-cookie-enabled=0",
                    # PulseAudio's null sink otherwise buffers up to two seconds.
                    # norewinds selects its 50 ms block without changing the tests.
                    "--load=module-null-sink sink_name=" + SINK + " norewinds=1"],
                    env=server_environment, stdout=output, stderr=subprocess.STDOUT)
                ready(server, query_environment, ready_timeout)
                child = subprocess.Popen(command, env=environment)
                return child.wait(timeout=timeout)
            finally:
                try:
                    stop(child)
                finally:
                    stop(server)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command after -- is required")
    interrupted = [signal.SIGINT]

    def cancel(signum, _frame):
        interrupted[0] = signum
        raise KeyboardInterrupt

    previous = {signum: signal.signal(signum, cancel) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        code = run(command, args.log, timeout=args.timeout)
        return code if code >= 0 else 128 - code
    except subprocess.TimeoutExpired:
        print("The GUI check timed out; its command and private audio server were stopped.", file=sys.stderr)
        return 124
    except KeyboardInterrupt:
        return 128 + interrupted[0]
    except (OSError, RuntimeError, ValueError) as error:
        print(f"The private CI audio fixture failed: {error}", file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
