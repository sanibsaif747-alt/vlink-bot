#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import time

APP_DIR = "/root"
BOT_CMD = [sys.executable, os.path.join(APP_DIR, "vlink_bypass.py")]
PID_FILE = os.path.join(APP_DIR, ".vlink-supervisor.pid")
LOG_FILE = os.path.join(APP_DIR, "vlink-bot.log")
ENV_FILE = os.path.join(APP_DIR, ".vlink-bot.env")
RESTART_DELAY = 5
HEARTBEAT = 30


def load_env(path):
    env = dict(os.environ)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"')
    except FileNotFoundError:
        pass
    return env


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--stop":
        try:
            with open(PID_FILE) as fh:
                os.kill(int(fh.read().strip()), signal.SIGTERM)
            log("supervisor stopping")
        except (FileNotFoundError, ProcessLookupError, ValueError):
            pass
        return

    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as fh:
                pid = int(fh.read().strip())
            os.kill(pid, 0)
            log("supervisor already running as pid {}".format(pid))
            return
        except (ProcessLookupError, ValueError):
            pass

    with open(PID_FILE, "w") as fh:
        fh.write(str(os.getpid()))

    env = load_env(ENV_FILE)
    child = None
    shutting_down = False

    def on_term(signum, frame):
        nonlocal shutting_down
        shutting_down = True
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    log("supervisor up, pid {}".format(os.getpid()))
    while not shutting_down:
        if child is None or child.poll() is not None:
            log("spawning vlink_bypass.py")
            child = subprocess.Popen(BOT_CMD, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            log("child pid {}".format(child.pid))
        time.sleep(HEARTBEAT)

    if child and child.poll() is None:
        child.wait(timeout=10)
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass
    log("supervisor exit")


if __name__ == "__main__":
    main()
