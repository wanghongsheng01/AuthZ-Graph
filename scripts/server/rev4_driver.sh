#!/bin/bash
# rev4 driver (server): BIBFS and the probe/scan constant sweep (code/run_rev4.py), then enwiki separately.
# One source file, two builds; memory watchdog and re-runs of watchdog-killed jobs at lower parallelism. nice 10.
R=/root/authz_graph_icde27/rev4; cd $R
export AUTHZ_OUTDIR=results_rev4
H=$(md5sum code/authz.cpp | cut -c1-12)
g++ -O2 -std=c++17 -DAUTHZ_SRC_HASH="\"$H\"" -o code/authz code/authz.cpp || exit 1
g++ -O2 -std=c++17 -DAUTHZ_WIDE -DAUTHZ_SRC_HASH="\"$H-wide\"" -o code/authz_wide code/authz.cpp || exit 1
echo "H=$H"
[ -L data/real_enwiki ] && mv data/real_enwiki data/.real_enwiki_hold
for round in 1 2 3; do
  W=$([ $round = 1 ] && echo 8 || echo 3)
  nice -n 10 python3 code/run_rev4.py $R $W > run_rev4_round$round.log 2>&1 &
  sleep 3; bash rev4_watchdog.sh; wait
  n=0; for f in $(grep -lE "exit=(137|-9)" -r results_rev4 --include=*.txt); do mv $f $f.killed_r$round; n=$((n+1)); done
  echo "round $round done, killed=$n"; [ $n = 0 ] && break
done
mv data/.real_enwiki_hold data/real_enwiki
# enwiki (128-bit build), phased with a guard (MemAvailable < 22 GB or RSS > 80 GB => kill)
B=$R/code/authz_wide; D=$R/data/real_enwiki; O=$R/results_rev4; export AUTHZ_DEFER_B=64 AUTHZ_SHUFFLE=1
mkdir -p $O/bibfs $O/kconst
run() { out=$1; cap=$2; shift 2
  [ -s $out ] && grep -q "exit=0" $out && return
  { timeout $cap /usr/bin/time -v nice -n 10 $B "$@"; echo "# exit=$?"; echo "# cmd: $* env=AUTHZ_DEFER_B=64,AUTHZ_SHUFFLE=1 (enwiki phase)"; } > $out 2>&1
  echo "$(date +%T) done $out"; }
guard() { while pgrep -f "authz_wide $D" >/dev/null; do
    av=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
    for p in $(pgrep -f "^$B $D"); do rss=$(awk '/VmRSS/{print int($2/1048576)}' /proc/$p/status 2>/dev/null)
      if [ "${av:-99}" -lt 22 ] || [ "${rss:-0}" -gt 80 ]; then echo "$(date +%T) guard kill $p avail=$av rss=$rss"; kill -9 $p; fi; done
    sleep 5; done; }
run $O/bibfs/real_enwiki_s1.txt 7200 $D AZX,BFS,BIBFS 10 -1 1 &
run $O/bibfs/real_enwiki_s2.txt 7200 $D AZX,BFS,BIBFS 10 -1 2 & sleep 5; guard; wait
run $O/bibfs/real_enwiki_s3.txt 7200 $D AZX,BFS,BIBFS 10 -1 3 & sleep 5; guard; wait
# two AZX instances per process at most fit next to each other (about 14 GB of labels each)
run $O/kconst/real_enwiki_a_s1.txt 7200 $D AZX,AZX-K1,AZX-K2,BFS 10 -1 1 & sleep 5; guard; wait
run $O/kconst/real_enwiki_b_s1.txt 7200 $D AZX,AZX-K8,AZX-K16,BFS 10 -1 1 & sleep 5; guard; wait
echo REV4_ALL_DONE
