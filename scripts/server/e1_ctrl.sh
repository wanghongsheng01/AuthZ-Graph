#!/bin/bash
# E1 control (server): the unchanged code/authz (build cdb396bf191d) with the same arguments and the
# same two methods as results_e1/main, so that the degree-aware test is compared under equal conditions.
R=/root/authz_graph_icde27/rev1; cd $R
while ! grep -q ALLDONE e1.log; do sleep 30; done
H=$(md5sum code/authz.cpp | cut -c1-12); [ $H = cdb396bf191d ] || exit 1
./code/authz 2>/dev/null | head -0; strings code/authz | grep -q "^$H$" || g++ -O2 -std=c++17 -DAUTHZ_SRC_HASH="\"$H\"" -o code/authz code/authz.cpp
strings code/authz_wide | grep -q "^$H-wide$" || g++ -O2 -std=c++17 -DAUTHZ_WIDE -DAUTHZ_SRC_HASH="\"$H-wide\"" -o code/authz_wide code/authz.cpp
O=results_e1/ctrl; mkdir -p $O; export AUTHZ_DEFER_B=64
run() { out=$O/$1.txt; shift; [ -s $out ] && grep -q "exit=0" $out && return
  { "$@"; echo "# exit=$?"; echo "# cmd: $*"; } > $out 2>&1; echo "$(date +%T) $out"; }
for f in $(ls results_e1/main/*.txt | grep -v enwiki); do
  b=$(basename $f .txt); ds=${b%_s[0-9]}; s=${b##*_s}
  run $b code/authz data/$ds AZX,BFS 10 -1 $s 100 1000 64
done
for s in 1 2 3; do run real_enwiki_s$s code/authz_wide data/real_enwiki AZX,BFS 10 -1 $s; done
echo CTRLDONE
