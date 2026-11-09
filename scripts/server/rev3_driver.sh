#!/bin/bash
# rev3 driver (server): the rev2 matrix rerun with the degree-aware probe/scan test as the AZX default
# and the ablation group abl (AZX, AZX-NODEG, BFS; code/run_rev3.py). One source file, two builds; memory
# watchdog and re-runs of watchdog-killed jobs at lower parallelism; enwiki jobs separately. nice 10.
R=/root/authz_graph_icde27/rev3; cd $R
export AUTHZ_OUTDIR=results_rev3
H=$(md5sum code/authz.cpp | cut -c1-12)
g++ -O2 -std=c++17 -DAUTHZ_SRC_HASH="\"$H\"" -o code/authz code/authz.cpp || exit 1
g++ -O2 -std=c++17 -DAUTHZ_WIDE -DAUTHZ_SRC_HASH="\"$H-wide\"" -o code/authz_wide code/authz.cpp || exit 1
echo "H=$H"
[ -L data/real_enwiki ] && mv data/real_enwiki data/.real_enwiki_hold
for round in 1 2 3; do
  W=$([ $round = 1 ] && echo 8 || echo 3)
  nice -n 10 python3 code/run_rev3.py $R $W > run_rev3_round$round.log 2>&1 &
  sleep 3; bash rev3_watchdog.sh; wait
  n=0; for f in $(grep -lE "exit=(137|-9)" -r results_rev3 --include=*.txt); do mv $f $f.killed_r$round; n=$((n+1)); done
  echo "round $round done, killed=$n"; [ $n = 0 ] && break
done
mv data/.real_enwiki_hold data/real_enwiki
# enwiki (128-bit build), phased with a guard (MemAvailable < 22 GB or RSS > 80 GB => kill)
B=$R/code/authz_wide; D=$R/data/real_enwiki; O=$R/results_rev3; export AUTHZ_DEFER_B=64
mkdir -p $O/main $O/main_sv $O/heavy
run() { out=$1; cap=$2; shift 2
  [ -s $out ] && grep -q "exit=0" $out && return
  { timeout $cap /usr/bin/time -v nice -n 10 $B "$@"; echo "# exit=$?"; echo "# cmd: $* env=AUTHZ_DEFER_B=64 (enwiki phase)"; } > $out 2>&1
  echo "$(date +%T) done $out"; }
guard() { while pgrep -f "authz_wide $D" >/dev/null; do
    av=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
    for p in $(pgrep -f "^$B $D"); do rss=$(awk '/VmRSS/{print int($2/1048576)}' /proc/$p/status 2>/dev/null)
      if [ "${av:-99}" -lt 22 ] || [ "${rss:-0}" -gt 80 ]; then echo "$(date +%T) guard kill $p avail=$av rss=$rss"; kill -9 $p; fi; done
    sleep 5; done; }
run $O/main/real_enwiki_s1.txt 7200 $D AZX,BFS,SPICE-ML,SPICE-FC 10 -1 1 &
run $O/main_sv/real_enwiki_s1.txt 7200 $D AZX,BFS,SV-DTC,SV-DTC-deg64 10 2000 1 &
sleep 5; guard; wait
run $O/main_defer_enwiki.txt 7200 $D AZX,DEFER-SAFE,DEFER-NAIVE 10 -1 1 & sleep 5; guard; wait
run $O/heavy/real_enwiki.txt 7200 $D AZX,BFS,PLL,LCR 10 100 1 & sleep 5; guard; wait
for s in 2 3; do run $O/main/real_enwiki_s$s.txt 7200 $D AZX,BFS,SPICE-ML,SPICE-FC 10 -1 $s & done; sleep 5; guard; wait
run $O/main/real_enwiki_closure_s1.txt 3600 $D AZX,CLOSURE 10 2000 1 & sleep 5; guard; wait
mkdir -p $O/abl
run $O/abl/real_enwiki_s1.txt 7200 $D AZX,AZX-NODEG,BFS 10 -1 1 &
run $O/abl/real_enwiki_s2.txt 7200 $D AZX,AZX-NODEG,BFS 10 -1 2 & sleep 5; guard; wait
run $O/abl/real_enwiki_s3.txt 7200 $D AZX,AZX-NODEG,BFS 10 -1 3 & sleep 5; guard; wait
echo REV3_ALL_DONE
