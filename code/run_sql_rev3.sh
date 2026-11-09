#!/bin/bash
# SQL cross-check with the build of the main runs: replay each stream dumped by `authz` (AUTHZ_DUMP)
# through a recursive SQL formulation of Definition 2 on SQLite and compare every answer with the oracle.
# Same settings as the earlier pilot (300 updates, 10 checks per update, first 3000 checks).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BIN=${AUTHZ_BIN:-$ROOT/code/authz}
OUT=$ROOT/${AUTHZ_RESULTS:-results_rev3}/sql
TMP=${TMPDIR:-/tmp}/authz_sql_$$
mkdir -p "$OUT" "$TMP"
for ds in ent syn10k syn100k syn1m amzn youtube dblp lj deep_L8 deep_L12 deep_L16; do
  [ -f "$OUT/$ds.json" ] && continue
  build=$(AUTHZ_DUMP=$TMP/$ds.dump nice "$BIN" "$ROOT/data/$ds" BFS 10 300 1 | sed -n 's/^# build=\([0-9a-f]*\).*/\1/p')
  nice python3 "$ROOT/code/sql_cte.py" "$ROOT/data/$ds" "$TMP/$ds.dump" 3000 "$OUT/$ds.json.tmp"
  python3 - "$OUT/$ds.json.tmp" "$build" "$OUT/$ds.json" <<'PY'
import json, sys
j = json.load(open(sys.argv[1])); j['build'] = sys.argv[2]; j['data'] = j['data'].split('/')[-1]
json.dump(j, open(sys.argv[3], 'w'), indent=1)
PY
  mv "$OUT/$ds.json.tmp" "$TMP/"
  echo "sql $ds build=$build mismatches=$(python3 -c "import json;print(json.load(open('$OUT/$ds.json'))['mismatch_vs_oracle'])")"
done
echo SQL_DONE
