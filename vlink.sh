#!/usr/bin/env bash
set -u
APP_DIR="/root"

proc_count() {
  pgrep -f "vlink_supervisor.py" 2>/dev/null | wc -l
}

start() {
  if [ "$(proc_count)" -ne 0 ]; then
    echo "already running: $(pgrep -f vlink_supervisor.py | tr '\n' ' ')"
    return 1
  fi
  setsid nohup python3 "$APP_DIR/vlink_supervisor.py" >>"$APP_DIR/vlink-bot.log" 2>&1 < /dev/null &
  sleep 2
  echo "started — log: $APP_DIR/vlink-bot.log"
}

stop() {
  for pid in $(pgrep -f "vlink_supervisor.py" 2>/dev/null); do
    python3 "$APP_DIR/vlink_supervisor.py" --stop
    sleep 1
    kill -9 "$pid" 2>/dev/null
  done
  for pid in $(pgrep -f "vlink_bypass.py" 2>/dev/null); do
    kill -9 "$pid" 2>/dev/null
  done
  rm -f "$APP_DIR/.vlink-supervisor.pid"
  echo "stopped"
}

status() {
  local n
  n=$(proc_count)
  echo "supervisors: $n"
  pgrep -f "vlink_bypass.py" 2>/dev/null | wc -l | sed 's/^/bots: /'
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|restart|status}" ;;
esac
