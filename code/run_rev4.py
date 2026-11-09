#!/usr/bin/env python3
"""Revision-4 additions to the matrix of run_rev3.py, run with one binary built from code/authz.cpp (which
makes the same decisions and counters as the rev3 build for every method of rev3; it only adds BIBFS, the
AZX-K<c> instances and AUTHZ_SHUFFLE):
  bibfs        AZX, BFS, BIBFS (online bidirectional BFS) on every graph and seed of the main runs
  kconst       AZX (constant 4) and AZX-K1/K2/K8/K16 (the probe/scan test with constants 1, 2, 8, 16, each with
               an index of its own) and BFS, on every graph of the main runs (enwiki: code/server/rev4_driver.sh)
  sweep_depth_bibfs  AZX, BFS, BIBFS on the depth sweep
All groups set AUTHZ_SHUFFLE=1: the methods are timed in a fresh random order for every update.
Outputs go to results_rev4/<group>/<name>.txt.

usage: run_rev4.py <root> [jobs]      (root holds code/ and data/; run on the server)
"""
import os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(sys.argv[1]); JOBS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
BIN = f'{ROOT}/code/authz'
OUTDIR = os.environ.get('AUTHZ_OUTDIR', 'results_rev4')
BIB = 'AZX,BFS,BIBFS'
KC = 'AZX,AZX-K1,AZX-K2,AZX-K8,AZX-K16,BFS'
DATA = ['ent', 'syn10k', 'amzn', 'youtube', 'dblp', 'deep_L8', 'deep_L12', 'deep_L16', 'syn100k', 'syn1m', 'lj',
        'real_go', 'real_hpo', 'real_simplewiki', 'real_go_m4', 'real_hpo_m4', 'real_simplewiki_m4', 'real_enwiki', 'deep1m_L12', 'deep1m_L16', 'lj_full', 'orkut', 'friendster', 'syn10m']
BIG = {'syn1m', 'lj', 'deep1m_L12', 'deep1m_L16', 'lj_full', 'orkut', 'friendster', 'real_enwiki', 'syn10m'}
NO_TC = {'deep_L16', 'deep1m_L12', 'deep1m_L16', 'lj_full', 'orkut', 'friendster', 'syn1m', 'real_enwiki', 'syn10m'}

jobs = []
def job(group, name, ds, methods, qpu=10, nupd=-1, seed=1, W=100, B=1000, K=64, env=None, timeout=7200):
    if os.path.isdir(f'{ROOT}/data/{ds}'):
        jobs.append((group, name, ds, methods, qpu, nupd, seed, W, B, K, env or {}, timeout))

ROT = {'AUTHZ_DEFER_B': '64', 'AUTHZ_SHUFFLE': '1'}
for ds in DATA:
    for seed in ([1] if ds in BIG else [1, 2, 3]):
        job('bibfs', f'{ds}_s{seed}', ds, BIB, seed=seed, env=ROT)
        job('kconst', f'{ds}_s{seed}', ds, KC, seed=seed, env=ROT)
for L in [2, 4, 8, 12, 16]:
    for seed in [1, 2, 3]:
        job('sweep_depth_bibfs', f'deep_L{L}_s{seed}', f'deep_L{L}', BIB, nupd=2000, seed=seed, env=ROT)
# enwiki runs use the exact 128-bit build of the same source (path counts exceed 2^63)
WIDE = {'real_enwiki'}


def run(j):
    group, name, ds, methods, qpu, nupd, seed, W, B, K, env, timeout = j
    os.makedirs(f'{ROOT}/{OUTDIR}/{group}', exist_ok=True)
    out = f'{ROOT}/{OUTDIR}/{group}/{name}.txt'
    if os.path.exists(out) and os.path.getsize(out) > 300 and 'TIMEOUT' not in open(out).read():
        return
    e = dict(os.environ); e.update(env)
    cmd = ['/usr/bin/time', '-v', BIN + ('_wide' if ds in WIDE else ''), f'{ROOT}/data/{ds}', methods, str(qpu), str(nupd), str(seed), str(W), str(B), str(K)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)
        rss = [l for l in r.stderr.splitlines() if 'Maximum resident' in l]
        err = [l for l in r.stderr.splitlines() if l.strip() and 'Command being timed' not in l and ':' not in l[:40]]
        txt = r.stdout + (f'\n# {rss[0].strip()}' if rss else '') + (f'\n# exit={r.returncode}')
    except subprocess.TimeoutExpired as ex:
        txt = (ex.stdout.decode() if isinstance(ex.stdout, bytes) else (ex.stdout or '')) + f'\n# TIMEOUT after {timeout}s'
    txt += f'\n# cmd: {" ".join(cmd[2:])} env={env}\n# wall_s: {time.time() - t0:.1f}\n'
    open(out, 'w').write(txt)
    print(group, name, f'{time.time() - t0:.0f}s', flush=True)


if __name__ == '__main__':
    print(len(jobs), "jobs", flush=True)
    # big jobs first so they overlap with the many small ones
    jobs.sort(key=lambda j: (j[2] not in BIG, j[0] != 'main'))
    with ThreadPoolExecutor(JOBS) as ex:
        list(ex.map(run, jobs))
    print('ALL DONE', flush=True)
