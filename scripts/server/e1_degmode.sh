#!/bin/bash
# E1 (server): degree-aware probe/scan test. code/authz_deg.cpp is generated from the unchanged
# code/authz.cpp by code/make_degmode.py; each run times AZX (degree-aware) and BFS in one process
# with the arguments of the main runs. Outputs in results_e1/main/.
R=/root/authz_graph_icde27/rev1; cd $R
[ "$(md5sum code/authz.cpp | cut -c1-12)" = cdb396bf191d ] || { echo "authz.cpp changed"; exit 1; }
python3 code/make_degmode.py || exit 1
H=$(md5sum code/authz_deg.cpp | cut -c1-12)
g++ -O2 -std=c++17 -DAUTHZ_SRC_HASH="\"deg-$H\"" -o code/authz_deg code/authz_deg.cpp || exit 1
g++ -O2 -std=c++17 -DAUTHZ_WIDE -DAUTHZ_SRC_HASH="\"deg-$H-wide\"" -o code/authz_deg_wide code/authz_deg.cpp || exit 1
O=results_e1/main; mkdir -p $O; export AUTHZ_DEFER_B=64
run() { out=$O/$1.txt; shift; [ -s $out ] && grep -q "exit=0" $out && return
  { "$@"; echo "# exit=$?"; echo "# cmd: $*"; } > $out 2>&1; echo "$(date +%T) $out"; }
for f in $(ls results_rev2/main/*.txt | grep -v enwiki); do
  b=$(basename $f .txt); ds=${b%_s[0-9]}; s=${b##*_s}
  run $b code/authz_deg data/$ds AZX,BFS 10 -1 $s 100 1000 64
done
for s in 1 2 3; do run real_enwiki_s$s code/authz_deg_wide data/real_enwiki AZX,BFS 10 -1 $s; done
echo ALLDONE
