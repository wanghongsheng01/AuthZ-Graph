#!/usr/bin/env python3
"""Check that every graph and update stream keeps subject identifiers in topological order:
each edge between two non-user subjects (groups, roles) goes from a higher to a lower id.
usage: check_topo.py <data_dir> [...]"""
import sys
import numpy as np

for d in sys.argv[1:]:
    t = np.fromfile(f'{d}/types.bin', np.int8)
    inner = (t == 1) | (t == 2)
    bad = 0; n = 0
    for p in ('e', 'u'):
        s = np.fromfile(f'{d}/{p}_src.bin', np.int32); v = np.fromfile(f'{d}/{p}_dst.bin', np.int32)
        k = inner[s] & inner[v]
        if p == 'u':
            k &= np.fromfile(f'{d}/u_op.bin', np.int8)[:len(s)] > 0   # insertions only
        n += int(k.sum()); bad += int((s[k] <= v[k]).sum())
    print(f'{d}\tsubject_edges={n}\tviolations={bad}')
