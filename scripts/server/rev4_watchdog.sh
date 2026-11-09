#!/bin/bash
# Memory watchdog for the rev4 batch: if MemAvailable < 22 GB, kill the YOUNGEST rev4 authz process
# (never touches other sessions' processes) and log it, so it can be rerun with lower parallelism.
LOG=/root/authz_graph_icde27/rev4/watchdog_rev4.log
while true; do
  avail=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  if [ "$avail" -lt 22 ]; then
    pid=$(pgrep -n -f "/root/authz_graph_icde27/rev4/code/authz ")
    if [ -n "$pid" ]; then
      echo "$(date +%T) avail=${avail}GB kill $pid $(tr '\0' ' ' < /proc/$pid/cmdline | cut -c1-200)" >> $LOG
      kill -9 $pid
      sleep 20
    fi
  fi
  pgrep -f "run_rev4.py" >/dev/null || { echo "$(date +%T) runner gone, watchdog exits" >> $LOG; exit 0; }
  sleep 5
done
