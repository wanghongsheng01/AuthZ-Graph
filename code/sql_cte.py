#!/usr/bin/env python3
"""Recursive-SQL-CTE baseline (SQLite, in-memory), replaying the exact update/query stream
dumped by `authz` (AUTHZ_DUMP=...). Every answer is compared with the C++ oracle's answer,
which doubles as a cross-implementation check of the decision semantics.

usage: sql_cte.py <datadir> <dumpfile> <max_queries> <out.json>
"""
import json, sqlite3, sys, time
import numpy as np

d, dump, maxq, outp = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
types = np.fromfile(f'{d}/types.bin', dtype=np.int8)
src = np.fromfile(f'{d}/e_src.bin', dtype=np.int32); dst = np.fromfile(f'{d}/e_dst.bin', dtype=np.int32)
msk = np.fromfile(f'{d}/e_mask.bin', dtype=np.uint64).astype(np.int64); pol = np.fromfile(f'{d}/e_pol.bin', dtype=np.int8)

db = sqlite3.connect(':memory:')
db.execute('CREATE TABLE edge(src INTEGER, dst INTEGER, mask INTEGER, pol INTEGER, ks INTEGER, kd INTEGER)')
db.executemany('INSERT INTO edge VALUES (?,?,?,?,?,?)',
               ((int(s), int(t), int(m), int(p), int(types[s]), int(types[t])) for s, t, m, p in zip(src, dst, msk, pol)))
db.execute('CREATE INDEX e_src ON edge(src, kd)')
db.execute('CREATE INDEX e_dst ON edge(dst, ks)')
db.execute('CREATE UNIQUE INDEX e_pair ON edge(src, dst)')
db.commit()

CHECK = '''
WITH RECURSIVE
 subj(n) AS (SELECT :u UNION
             SELECT e.dst FROM edge e JOIN subj ON e.src = subj.n WHERE e.kd != 3 AND (e.mask & ~:S) = 0),
 anc(a, acc) AS (SELECT :r, 0 UNION
             SELECT e.src, anc.acc | e.mask FROM edge e JOIN anc ON e.dst = anc.a
             WHERE e.ks = 3 AND ((anc.acc | e.mask) & ~:S) = 0),
 hit(p) AS (SELECT e.pol FROM edge e JOIN anc ON e.dst = anc.a JOIN subj ON e.src = subj.n
             WHERE e.ks != 3 AND ((e.mask | anc.acc) & ~:S) = 0)
SELECT COALESCE(MAX(p), 0) = 1 AND COALESCE(MIN(p), 0) = 1 FROM hit
'''
qt, ut, nq, mism, nu = [], [], 0, 0, 0
for line in open(dump):
    x = line.split()
    if x[0] == 'U':
        op, s, t, m, p = int(x[1]), int(x[2]), int(x[3]), int(x[4]), int(x[5])
        t0 = time.perf_counter()
        if op > 0:
            db.execute('INSERT INTO edge VALUES (?,?,?,?,?,?)', (s, t, m, p, int(types[s]), int(types[t])))
        else:
            db.execute('DELETE FROM edge WHERE src = ? AND dst = ?', (s, t))
        db.commit()
        ut.append(time.perf_counter() - t0); nu += 1
    else:
        if nq >= maxq:
            break
        u, r, S, truth = int(x[1]), int(x[2]), int(x[3]), int(x[4])
        t0 = time.perf_counter()
        a = db.execute(CHECK, {'u': u, 'r': r, 'S': S}).fetchone()[0]
        qt.append(time.perf_counter() - t0); nq += 1
        mism += int(bool(a) != bool(truth))
qt = np.array(qt) * 1e6; ut = np.array(ut) * 1e6
res = {'data': d, 'queries': nq, 'updates': nu, 'mismatch_vs_oracle': mism,
       'q_mean_us': float(qt.mean()), 'q_p50_us': float(np.percentile(qt, 50)), 'q_p99_us': float(np.percentile(qt, 99)),
       'u_mean_us': float(ut.mean()) if len(ut) else 0, 'u_p99_us': float(np.percentile(ut, 99)) if len(ut) else 0}
json.dump(res, open(outp, 'w'), indent=1)
print(json.dumps(res))
