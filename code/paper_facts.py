#!/usr/bin/env python3
"""Generate every number, table and figure of the manuscript from raw results.

Source of truth: results_rev2/<group>/<name>.txt (AUTHZ_RESULTS to override), all produced by one source
file whose hash every result header must carry (AUTHZ_EXPECT_BUILD). No fallback to older trees; missing
inputs become \\todo{...} placeholders, listed at the end. Sources per macro: paper/numbers_sources.txt.
Writes paper/numbers.tex, paper/tables/*.tex and paper/figs/*.pdf.
Re-run after new results arrive:  python3 code/paper_facts.py
"""
import glob, json, math, os, re, statistics as stt, struct

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER = f'{ROOT}/paper'
REV = f"{ROOT}/{os.environ.get('AUTHZ_RESULTS', 'results_rev2')}"
OLD = f'{ROOT}/results'
os.makedirs(f'{PAPER}/tables', exist_ok=True)
os.makedirs(f'{PAPER}/figs', exist_ok=True)
TODO = r'\todo{TBD}'

SHALLOW = ['ent', 'syn10k', 'syn100k', 'syn1m', 'amzn', 'youtube', 'dblp', 'lj']
DEEP = ['deep_L8', 'deep_L12', 'deep_L16']
DATA = SHALLOW + DEEP
REAL = ['real_go', 'real_hpo', 'real_simplewiki', 'real_enwiki']
SERVER = ['deep1m_L12', 'deep1m_L16', 'syn10m', 'lj_full', 'orkut', 'friendster']
NAME = {'ent': 'ENT', 'syn10k': 'SYN-10K', 'syn100k': 'SYN-100K', 'syn1m': 'SYN-1M', 'amzn': 'AMZN',
        'youtube': 'YouTube', 'dblp': 'DBLP', 'lj': 'LJ', 'deep_L8': 'DEEP-8', 'deep_L12': 'DEEP-12',
        'deep_L16': 'DEEP-16', 'deep_L2': 'DEEP-2', 'deep_L4': 'DEEP-4',
        'real_go': 'GO', 'real_hpo': 'HPO', 'real_simplewiki': 'Wiki-Cat', 'real_enwiki': 'Wiki-Cat-en',
        'deep1m_L12': 'DEEP1M-12', 'deep1m_L16': 'DEEP1M-16', 'syn10m': 'SYN-10M', 'lj_full': 'LJ-full', 'orkut': 'Orkut',
        'friendster': 'Friendster'}


def name(ds):
    if ds.endswith('_m4'):
        return NAME.get(ds[:-3], ds[:-3]) + '$^{m}$'
    return NAME.get(ds, ds)


# ------------------------------------------------------------------ parsing
def meta(ds):
    # dataset descriptions (not results): generator metadata from any location
    for p in [f'{ROOT}/data/{ds}/meta.txt', f'{REV}/meta/{ds}.meta.txt', f'{OLD}/server/meta/{ds}.meta.txt', f'{OLD}/real/{ds}_meta.txt']:
        if os.path.exists(p):
            m = {}
            for line in open(p):
                k, _, v = line.strip().partition(' ')
                m[k] = v
            return m
    # last resort: the '# data=' header of a result file (n, m, users only)
    for p in glob.glob(f'{REV}/main/{ds}_s1.txt'):
        hm = re.search(r'# data=\S+ n=(\d+) m=(\d+) users=(\d+)', open(p).read())
        hr_ = re.search(r'res=(\d+)', open(p).read())
        if hm:
            subj = int(hm[1]) - int(hm[3]) - int(hr_[1]) if hr_ else None
            return {'n': hm[1], 'm': hm[2], 'users': hm[3], 'subjects': str(subj) if subj is not None else '',
                    'updates': re.search(r'updates=(\d+)', open(p).read())[1], 'partial': '1'}
    return {}


def parse(path):
    rows, info = {}, {}
    if not os.path.exists(path):
        return rows, info
    txt = open(path).read()
    info['timeout'] = 'TIMEOUT' in txt
    for pat, keys, conv in [
        (r'closure_mean=([\d.]+) closure_max=(\d+) hubs_mean=([\d.]+)', ('closure', 'closure_max', 'hubs'), (float, int, float)),
        (r'truth_allow_rate=([\d.]+)', ('allow_rate',), (float,)),
        (r'# build=(\S+)', ('build',), (str,)),
        (r'defer_B=(\S+)', ('defer_B',), (str,))]:
        m = re.search(pat, txt)
        if m:
            for k, c, v in zip(keys, conv, m.groups()):
                info[k] = c(v)
    m = re.search(r'# who: AZX mean ([\d.]+) us p99 ([\d.]+) \| BFS mean ([\d.]+) us p99 ([\d.]+)', txt)
    if m:
        info['who'] = tuple(map(float, m.groups()))
    m = re.search(r'# what: AZX mean ([\d.]+) us p99 ([\d.]+) \| BFS mean ([\d.]+) us p99 ([\d.]+) \| who/what mismatches (\d+)', txt)
    if m:
        info['what'] = tuple(map(float, m.groups()[:4])); info['ww_mism'] = int(m[5])
    hdr = None
    for line in txt.splitlines():
        if line.startswith('method\t'):
            hdr = line.split('\t'); continue
        if hdr and line and not line.startswith('#'):
            fs = line.split('\t')
            if len(fs) < len(hdr) - 1:
                continue
            r = dict(zip(hdr, fs))
            try:
                for k in hdr[1:13]:
                    r[k] = float(r[k])
            except ValueError:
                continue
            ex = {}
            for kv in r.get('extra', '').split(';'):
                if '=' in kv:
                    a, b = kv.split('=', 1)
                    try: ex[a] = float(b)
                    except ValueError: ex[a] = b
            r['x'] = ex
            rows[fs[0]] = r
    return rows, info


# ------------------------------------------------------------------ source tracking
CUR = set()      # sources read since the last section() call
SRC = {}         # macro -> sources
M = {}           # macro -> value


def section():
    CUR.clear()


def mac(nm, val):
    assert re.fullmatch(r'[A-Za-z]+', nm), nm
    M[nm] = val
    SRC[nm] = sorted(CUR) if CUR else ['derived/none']


# Results are read from ONE tree only (default results_rev2/, produced by one source file).
# There is no fallback to older trees: a missing file becomes a \todo placeholder, and a file whose
# '# build=' header does not match EXPECT_BUILD aborts the script.
EXPECT_BUILD = os.environ.get('AUTHZ_EXPECT_BUILD', 'cdb396bf191d')
TREE = os.path.relpath(REV, ROOT)
BUILDS = {}   # build string -> number of files


def check_build(path, info):
    b = info.get('build')
    if b is None:
        raise SystemExit(f'paper_facts: {path} has no "# build=" header')
    BUILDS[b] = BUILDS.get(b, 0) + 1
    if EXPECT_BUILD and not (b == EXPECT_BUILD or b == EXPECT_BUILD + '-wide'):
        raise SystemExit(f'paper_facts: {path} was produced by build {b}, expected {EXPECT_BUILD}')


MISSING = []


def load(group, nm, need=None):
    """Return (rows, info) for <TREE>/<group>/<nm>.txt, or ({}, {}) if missing or killed."""
    p = f'{REV}/{group}/{nm}.txt'
    r, info = parse(p)
    if r and (need is None or need in r):
        check_build(p, info)
        CUR.add(f'{TREE}/{group}')
        info['src'] = 'rev'; info['tag'] = f'{TREE}/{group}'
        return r, info
    MISSING.append(f'{group}/{nm}')
    return {}, {}


SHORT = {}   # dataset -> number of usable seeds when below the requested number


def runs(group, ds, seeds=(1, 2, 3), need='AZX'):
    """All parsable seeds of one dataset (runs killed on the server, e.g. exit 137, are skipped)."""
    out = []
    for s_ in seeds:
        r, info = load(group, f'{ds}_s{s_}', need)
        if r:
            out.append((r, info))
    if out and len(out) < len(seeds):
        SHORT[ds] = len(out)
    return out


# ------------------------------------------------------------------ formatting
def f(x, nd=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return '--'
    if x == 0:
        return '0'
    ax = abs(x)
    if ax >= 1e3:
        return f'{x:,.0f}'.replace(',', '{,}')
    if ax >= 100:
        return f'{x:.0f}'
    if ax >= 10:
        return f'{x:.1f}'
    if ax >= 1:
        return f'{x:.2f}'
    return f'{x:.{nd}f}'


def fi(n):
    return f'{int(n):,}'.replace(',', '{,}')


def pct(x):
    return f'{100 * x:.0f}' if x >= 0.01 or x == 0 else f'{100 * x:.1f}'


TQ = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776}   # two-sided 95% t quantiles, df = n - 1


def ci95(v):
    """Half-width of the 95% t-based confidence interval of the mean (n is 2 or 3 seeds here)."""
    return TQ.get(len(v), 1.96) * stt.stdev(v) / math.sqrt(len(v)) if len(v) > 1 else 0.0


def mean_of(rs, meth, key='q_mean_us'):
    v = [r[meth][key] for r, _ in rs if meth in r]
    return stt.mean(v) if v else float('nan')


def xmean(rs, meth, key):
    v = [r[meth]['x'][key] for r, _ in rs if meth in r and key in r[meth]['x']]
    return stt.mean(v) if v else float('nan')


def wtab(nm, s):
    open(f'{PAPER}/tables/{nm}.tex', 'w').write('% generated by code/paper_facts.py\n' + s)


def opt(cond, val):
    return val if cond else TODO


# ================================================================== main runs
section()
main = {ds: runs('main', ds, (1,) if ds in ('syn1m', 'lj') else (1, 2, 3)) for ds in DATA}
main = {k: v for k, v in main.items() if v}
srv = {ds: runs('main', ds, (1,)) for ds in SERVER}
srv = {k: v for k, v in srv.items() if v}
realm = {}
for ds in REAL:
    for v in (ds, ds + '_m4'):
        rs = runs('main', v, (1, 2, 3), 'BFS')
        if rs:
            realm[v] = rs

tot = {}
nq = 0
for ds, rs in main.items():
    for r, _ in rs:
        nq += r['AZX']['queries']
        for meth in ['AZX', 'CLOSURE', 'SPICE-ML100', 'SPICE-FC', 'AZX-DEFER-SAFE', 'AZX-DEFER-NAIVE']:
            if meth in r:
                a = tot.setdefault(meth, [0, 0, 0])
                a[0] += int(r[meth]['false_allow']); a[1] += int(r[meth]['false_deny'])
                a[2] += int(r[meth]['x'].get('fa_targeted', 0))
mac('NumQueries', fi(nq)); mac('NumDatasets', str(len(main)))
mac('FAazx', fi(tot['AZX'][0])); mac('FDazx', fi(tot['AZX'][1])); mac('FAclosure', fi(tot['CLOSURE'][0]))
mac('FAspice', fi(tot['SPICE-ML100'][0])); mac('FDspice', fi(tot['SPICE-ML100'][1])); mac('FAspicefc', fi(tot['SPICE-FC'][0]))
mac('FAspiceProbe', fi(tot['SPICE-ML100'][2]))
mac('FAsafe', fi(tot['AZX-DEFER-SAFE'][0])); mac('FDsafe', fi(tot['AZX-DEFER-SAFE'][1]))
mac('FDsafePct', f"{100 * tot['AZX-DEFER-SAFE'][1] / nq:.1f}")
mac('FAnaive', fi(tot['AZX-DEFER-NAIVE'][0])); mac('FAnaiveProbe', fi(tot['AZX-DEFER-NAIVE'][2]))
mac('FAnaiveBg', fi(tot['AZX-DEFER-NAIVE'][0] - tot['AZX-DEFER-NAIVE'][2]))
mac('FDnaive', fi(tot['AZX-DEFER-NAIVE'][1]))
mac('NaiveDatasets', str(sum(1 for rs in main.values() if sum(int(r['AZX-DEFER-NAIVE']['false_allow']) for r, _ in rs) > 0)))
mac('SpiceHitPct', f"{100 * stt.mean(r['SPICE-ML100']['x'].get('hit_rate', 0) for rs in main.values() for r, _ in rs):.0f}")
mac('SpiceFCHitPct', f"{100 * stt.mean(r['SPICE-FC']['x'].get('hit_rate', 0) for rs in main.values() for r, _ in rs):.1f}")

ratio = {ds: mean_of(rs, 'BFS') / mean_of(rs, 'AZX') for ds, rs in main.items()}
sh = [ratio[d] for d in SHALLOW if d in ratio]; dp = [ratio[d] for d in DEEP if d in ratio]
mac('ShallowRatioMin', f(min(sh))); mac('ShallowRatioMax', f(max(sh)))
mac('DeepRatioMin', f(min(dp))); mac('DeepRatioMax', f(max(dp)))
mac('ShallowAbsMax', f(max(max(mean_of(main[d], 'AZX'), mean_of(main[d], 'BFS')) for d in SHALLOW if d in main)))
mac('ShallowSlowerList', ', '.join(name(d) for d in SHALLOW if d in ratio and ratio[d] < 1))
mac('ShallowSlowerCount', str(sum(1 for d in SHALLOW if d in ratio and ratio[d] < 1)))
mac('ShallowFasterCount', str(sum(1 for d in SHALLOW if d in ratio and ratio[d] >= 1)))
for d, word in [('deep_L8', 'Eight'), ('deep_L12', 'Twelve'), ('deep_L16', 'Sixteen')]:
    rs = main[d]
    per = [r['BFS']['q_mean_us'] / r['AZX']['q_mean_us'] for r, _ in rs]
    mac(f'DeepRatio{word}', f(ratio[d]))
    mac(f'DeepRatio{word}CI', f(ci95(per)))
    mac(f'DeepRatio{word}Lo', f(min(per))); mac(f'DeepRatio{word}Hi', f(max(per)))
    mac(f'DeepAZX{word}', f(mean_of(rs, 'AZX'))); mac(f'DeepBFS{word}', f(mean_of(rs, 'BFS')))
clos = {ds: (rs[0][1].get('closure'), rs[0][1].get('hubs')) for ds, rs in main.items()}
mac('ShallowClosureMin', f(min(clos[d][0] for d in SHALLOW if d in clos)))
mac('ShallowClosureMax', f(max(clos[d][0] for d in SHALLOW if d in clos)))
# GrpClosure query ratio on deep graphs
gq = [mean_of(main[d], 'CLOSURE') / mean_of(main[d], 'AZX') for d in DEEP]
mac('ClosBFSRatioDeepSixteen', f(mean_of(main['deep_L16'], 'BFS') / mean_of(main['deep_L16'], 'CLOSURE')))
mac('ClosureQRatioMin', f(min(gq))); mac('ClosureQRatioMax', f(max(gq)))

# annotation width and coefficient magnitude (revision-1 binary only)
mx = [r['AZX']['x'].get('masks_per_entry_max') for rs in list(main.values()) + list(srv.values()) for r, _ in rs if 'masks_per_entry_max' in r['AZX']['x']]
mm = [r['AZX']['x'].get('masks_per_entry_mean') for rs in list(main.values()) + list(srv.values()) for r, _ in rs if 'masks_per_entry_mean' in r['AZX']['x']]
mc = [r['AZX']['x'].get('max_coef') for rs in list(main.values()) + list(srv.values()) for r, _ in rs if 'max_coef' in r['AZX']['x']]
mac('MasksMeanMin', opt(mm, f(min(mm) if mm else 0))); mac('MasksMeanMax', opt(mm, f(max(mm) if mm else 0)))
mac('MasksMeanText', opt(mm, (f(min(mm)) if f(min(mm)) == f(max(mm)) else f(min(mm)) + '--' + f(max(mm))) if mm else ''))
mac('MasksMax', opt(mx, fi(max(mx) if mx else 0)))
if mc:
    v = max(mc); e = int(math.floor(math.log10(v))) if v > 0 else 0
    pass
else:
    pass

# update cost and size (seed 1 of the main runs)
def avg_rows(rs):
    """Per-method mean over seeds of every numeric column (extras from the first seed)."""
    out = {}
    for meth in rs[0][0]:
        rows_ = [r[meth] for r, _ in rs if meth in r]
        d_ = {k: stt.mean(r_[k] for r_ in rows_) for k in rows_[0] if isinstance(rows_[0][k], float)}
        d_['x'] = rows_[0]['x']
        out[meth] = d_
    return out


upd = {ds: avg_rows(rs) for ds, rs in main.items()}
mac('UpdShallowMin', f(min(upd[d]['AZX']['u_mean_us'] for d in SHALLOW if d in upd)))
mac('UpdShallowMax', f(max(upd[d]['AZX']['u_mean_us'] for d in SHALLOW if d in upd)))
mac('UpdDeepSixteen', fi(upd['deep_L16']['AZX']['u_mean_us'])); mac('UpdDeepSixteenPnn', fi(upd['deep_L16']['AZX']['u_p99_us']))
mac('UpdDeepEight', f(upd['deep_L8']['AZX']['u_mean_us']))
cr = [upd[d]['CLOSURE']['u_mean_us'] / upd[d]['AZX']['u_mean_us'] for d in DEEP]
mac('ClosureUpdRatioMin', f(min(cr))); mac('ClosureUpdRatioMax', f(max(cr)))
szr = [upd[d]['CLOSURE']['index_MB'] / upd[d]['AZX']['index_MB'] for d in DEEP]
mac('ClosureSizeRatioMin', f(min(szr))); mac('ClosureSizeRatioMax', f(max(szr)))
mac('SizeSynOneM', f(upd['syn1m']['AZX']['index_MB'])); mac('SizeSynOneMClosure', f(upd['syn1m']['CLOSURE']['index_MB']))
mac('LabelsSynOneM', fi(upd['syn1m']['AZX']['x'].get('label_entries', 0)))
mac('SizeDeepSixteen', f(upd['deep_L16']['AZX']['index_MB'])); mac('SizeDeepSixteenClosure', f(upd['deep_L16']['CLOSURE']['index_MB']))
mac('ProbeDeepSixteenPct', f"{100 * upd['deep_L16']['AZX']['x'].get('probe_frac', 0):.0f}")

# ------------------------------------------------------------------ server-scale rows
MAINSRC = set(CUR)
section()
CUR.update(i['tag'] for v in srv.values() for _, i in v)
if srv:
    mac('ServerCount', str(len(srv)))
    for d, word in [('deep1m_L12', 'Twelve'), ('deep1m_L16', 'Sixteen'), ('friendster', 'Friendster'), ('lj_full', 'LJfull'), ('orkut', 'Orkut'), ('syn10m', 'SynTenM')]:
        if d in srv:
            r = srv[d][0][0]
            mac(f'Srv{word}Ratio', f(r['BFS']['q_mean_us'] / r['AZX']['q_mean_us']))
            mac(f'Srv{word}AZX', f(r['AZX']['q_mean_us'])); mac(f'Srv{word}BFS', f(r['BFS']['q_mean_us']))
            mac(f'Srv{word}Upd', f(r['AZX']['u_mean_us']))
            mac(f'Srv{word}Edges', f"{int(meta(d).get('m', 0)) / 1e6:.1f}" if meta(d) else TODO)
            mac(f'Srv{word}Users', f"{int(meta(d).get('users', 0)) / 1e6:.1f}" if meta(d) else TODO)
            if 'CLOSURE' in r:
                mac(f'Srv{word}ClosUpd', f(r['CLOSURE']['u_mean_us'] / r['AZX']['u_mean_us']))
        else:
            for k in ['Ratio', 'AZX', 'BFS', 'Upd', 'Edges', 'Users', 'ClosUpd']:
                mac(f'Srv{word}{k}', TODO)
    mac('SrvFA', fi(sum(int(r['AZX']['false_allow']) for v in srv.values() for r, _ in v)))
    mac('SrvQueries', fi(sum(int(r['AZX']['queries']) for v in srv.values() for r, _ in v)))
else:
    for k in ['ServerCount', 'SrvFA', 'SrvQueries']:
        mac(k, TODO)
mac('ServerCPU', 'Intel Xeon Platinum 8336C (28 hardware threads, 110\\,GB memory)')
mac('LaptopCPU', 'an Apple M-series laptop (16\\,GB memory)')

# ------------------------------------------------------------------ heavy baselines
section()
heavy = {}
for ds in DATA + SERVER:
    r, info = load('heavy', ds, 'AZX')
    if r:
        heavy[ds] = (r, info)
tcm = {}
r, info = load('heavy', 'syn1m_TC', 'TC')
if r:
    tcm['syn1m'] = r
def tc_row(d):
    if d in tcm:
        return tcm[d]
    return heavy.get(d, ({}, {}))[0]
tcu = [(d, tc_row(d)['TC']['u_mean_us'], tc_row(d)['AZX']['u_mean_us']) for d in DATA if 'TC' in tc_row(d)]
mac('TCUpdMin', f(min(x[1] for x in tcu))); mac('TCUpdMaxS', f(max(x[1] for x in tcu) / 1e6))
rr_tc = sorted(x[1] / max(1e-3, x[2]) for x in tcu)
mac('TCUpdRatioMaxName', name(max(tcu, key=lambda x: x[1] / max(1e-3, x[2]))[0]))
mac('TCUpdRatioMin', f(rr_tc[0])); mac('TCUpdRatioMed', fi(stt.median(rr_tc))); mac('TCUpdRatioMax', fi(rr_tc[-1]))
s1 = tc_row('syn1m')
nupd_tc = re.search(r'updates=(\d+)', open(f'{REV}/heavy/syn1m_TC.txt').read())[1] if os.path.exists(f'{REV}/heavy/syn1m_TC.txt') else '5'
mac('TCSynOneMUpdates', nupd_tc)
mac('TCSizeSynOneMGB', f(s1['TC']['index_MB'] / 1024) if 'TC' in s1 else TODO)
for ds in REAL + [k + '_m4' for k in REAL]:
    if ds not in heavy:
        r_, i_ = load('heavy', ds, 'AZX')
        if r_:
            heavy[ds] = (r_, i_)
lrl = sorted(((heavy[d][0]['LCR-LI']['u_mean_us'] / max(1e-3, heavy[d][0]['AZX']['u_mean_us']), d) for d in heavy if 'LCR-LI' in heavy[d][0]))
lr = [x for x, _ in lrl]
below = [(x, d) for x, d in lrl if x < 1]
mac('LCRBelowCount', str(len(below)))
mac('LCRBelowMin', f(below[0][0]) if below else TODO); mac('LCRBelowMax', f(below[-1][0]) if below else TODO)
mac('LCRBelowList', ', '.join(f'{name(d)} {f(x)}' for x, d in below) if below else TODO)
mac('LCRUpdRatioMin', f(min(lr))); mac('LCRUpdRatioMax', f(max(lr)))
lq = [heavy[d][0]['LCR-LI']['q_mean_us'] / heavy[d][0]['BFS']['q_mean_us'] for d in heavy if 'LCR-LI' in heavy[d][0] and 'BFS' in heavy[d][0]]
mac('LCRqOverBFSMin', f(min(lq))); mac('LCRqOverBFSMax', f(max(lq)))
mac('LCRUpdDeepSixteenMs', f(heavy['deep_L16'][0]['LCR-LI']['u_mean_us'] / 1000))
mac('LCRqDeepSixteen', f(heavy['deep_L16'][0]['LCR-LI']['q_mean_us']))
plfa = [(d, heavy[d][0]['PLL-noDeny']) for d in DATA if d in heavy and 'PLL-noDeny' in heavy[d][0]]
mac('PLLfaDatasets', str(sum(1 for _, x in plfa if x['false_allow'] > 0))); mac('PLLnDatasets', str(len(plfa)))
mac('PLLfaTotal', fi(sum(x['false_allow'] for _, x in plfa))); mac('PLLnq', fi(sum(x['queries'] for _, x in plfa)))
mac('PLLfaCond', fi(sum(x['x'].get('fa_cond', 0) for _, x in plfa)))
mac('PLLfaDeny', fi(sum(x['x'].get('fa_deny', 0) for _, x in plfa)))
mac('PLLfaStale', fi(sum(x['x'].get('fa_stale', 0) for _, x in plfa)))
mac('PLLbuildDeepSixteenS', f(heavy['deep_L16'][0]['PLL-noDeny']['build_ms'] / 1000))

# ------------------------------------------------------------------ SQL cross-check
section()
sq = {}
for ds in DATA:
    # the SQL cross-check is the one result taken from the pilot runs; the text says so (Sec. VI-A)
    for p in [f'{REV}/sql/{ds}.json', f'{OLD}/sql/{ds}.json']:
        if os.path.exists(p):
            sq[ds] = json.load(open(p)); CUR.add(os.path.relpath(os.path.dirname(p), ROOT) + (' (PILOT, stated in text)' if p.startswith(OLD) else '')); break
mac('SQLnq', fi(sum(j['queries'] for j in sq.values())))
mac('SQLmismatch', fi(sum(j['mismatch_vs_oracle'] for j in sq.values())))

# ------------------------------------------------------------------ depth sweep (3 seeds)
section()
dep = {}
for L in [2, 4, 8, 12, 16]:
    rs = runs('sweep_depth', f'deep_L{L}', (1, 2, 3))
    if rs:
        dep[L] = rs
def depr(L):
    per = [r['BFS']['q_mean_us'] / r['AZX']['q_mean_us'] for r, _ in dep[L]]
    return stt.mean(per), ci95(per), len(per)
for L, word in [(2, 'Two'), (4, 'Four'), (8, 'Eight'), (16, 'Sixteen')]:
    m_, c_, n_ = depr(L)
    mac(f'DepthRatio{word}', f(m_)); mac(f'DepthRatio{word}CI', f(c_))
    mac(f'DepthClosure{word}', f(dep[L][0][1]['closure']) if L != 16 else fi(dep[L][0][1]['closure']))
mac('DepthSeeds', str(min(len(v) for v in dep.values())))
mac('DepthHubsSixteen', f(dep[16][0][1]['hubs']))

# ------------------------------------------------------------------ cache window, read/write ratio
section()
sp = {}
for ds in ['ent', 'syn100k', 'amzn']:
    for W in [10, 100, 1000]:
        r, _ = load('sweep_spice', f'{ds}_W{W}', f'SPICE-ML{W}')
        if r:
            sp[(ds, W)] = r
WRATE = 20  # writes per second used to map the write-count window to time
for ds_, w_ in [('ent', 'Ent'), ('syn100k', 'Syn')]:
    if all((ds_, W) in sp for W in (10, 1000)):
        mac(f'Win{w_}FALo', fi(sp[(ds_, 10)]['SPICE-ML10']['false_allow'])); mac(f'Win{w_}FAHi', fi(sp[(ds_, 1000)]['SPICE-ML1000']['false_allow']))
    else:
        mac(f'Win{w_}FALo', TODO); mac(f'Win{w_}FAHi', TODO)
mac('WriteRate', str(WRATE))
for W, word in [(10, 'Ten'), (100, 'Hundred'), (1000, 'Thousand')]:
    mac(f'WinSec{word}', f'{W / WRATE:g}')


def revocations(ds, n):
    """Updates in the first n of the stream that can revoke: deletion of a grant or subject edge,
    or insertion of a deny edge (condition changes count through their deletion)."""
    d = f'{ROOT}/data/{ds}'
    cache = f'{REV}/meta/revocations.json'   # shipped with the artifact for readers without data/
    if not os.path.exists(f'{d}/u_op.bin'):
        c = json.load(open(cache)) if os.path.exists(cache) else {}
        return c.get(f'{ds}:{n}')
    op = np.fromfile(f'{d}/u_op.bin', dtype=np.int8)[:n]; pol = np.fromfile(f'{d}/u_pol.bin', dtype=np.int8)[:n]
    src = np.fromfile(f'{d}/u_src.bin', dtype=np.int32)[:n]
    t = np.fromfile(f'{d}/types.bin', dtype=np.int8)
    subj = t[src] != 3
    return int((((op < 0) & (pol > 0)) | ((op > 0) & (pol < 0))) [subj].sum())


rat = {}
for ds in ['ent', 'syn100k', 'deep_L16', 'amzn']:
    for q in [10, 100, 1000]:
        r, info = load('sweep_ratio', f'{ds}_q{q}', 'SPICE-ML100')
        if r:
            nu = int(re.search(r'updates=(\d+)', open(f'{REV}/sweep_ratio/{ds}_q{q}.txt').read())[1])
            rat[(ds, q)] = (r, nu, revocations(ds, nu))
if rat:
    pr = [x[0]['SPICE-ML100']['false_allow'] / max(1, x[2]) for (ds, q), x in rat.items() if q == 1000 and x[2]]
    mac('RatioFAperRevMax', f(max(pr)) if pr else TODO)
    hr = [x[0]['SPICE-ML100']['x'].get('hit_rate', 0) for (ds, q), x in rat.items() if q == 1000]
    mac('RatioHitThousand', f'{100 * stt.mean(hr):.0f}' if hr else TODO)
else:
    for k in ['RatioFAperRevMax', 'RatioHitThousand']:
        mac(k, TODO)

# ------------------------------------------------------------------ deferral
section()
df = {}
for ds in ['ent', 'syn100k', 'amzn']:
    for B in [16, 64, 256, 1024]:
        r, info = load('sweep_defer', f'{ds}_B{B}', 'AZX-DEFER-SAFE')
        if r:
            df[(ds, B)] = (r, info)


def share(x):
    e = x['x']; return e.get('deferred', 0) / max(1, e.get('deferred', 0) + e.get('sync', 0))


def ack(x, k):
    """Acknowledgment latency over ALL writes (revision 1 reports ack_mean/p50/p99);
    the pre-revision files only have sync_us_mean, which is not this quantity."""
    return x['x'].get(f'ack_{k}', float('nan'))


have_ack = any('ack_mean' in r['AZX-DEFER-SAFE']['x'] for r, _ in df.values())
mac('SafeShareEnt', f"{100 * share(df[('ent', 64)][0]['AZX-DEFER-SAFE']):.0f}")
mac('SafeShareSyn', f"{100 * share(df[('syn100k', 64)][0]['AZX-DEFER-SAFE']):.0f}")
mac('SafeShareAmzn', f"{100 * share(df[('amzn', 64)][0]['AZX-DEFER-SAFE']):.0f}")
if have_ack:
    r = df[('syn100k', 64)][0]
    mac('SafeAckMeanSyn', f(ack(r['AZX-DEFER-SAFE'], 'mean'))); mac('SafeAckPnnSyn', f(ack(r['AZX-DEFER-SAFE'], 'p99')))
    mac('SyncAckMeanSyn', f(r['AZX']['u_mean_us'])); mac('SyncAckPnnSyn', f(r['AZX']['u_p99_us']))
else:
    for k in ['SafeAckMeanSyn', 'SafeAckPnnSyn', 'SyncAckMeanSyn', 'SyncAckPnnSyn']:
        mac(k, TODO)
ap = {}
for ds in ['syn100k', 'deep_L12']:
    for c in [10, 100]:
        r, info = load('sweep_apply', f'{ds}_c{c}', 'AZX-DEFER-SAFE')
        if r:
            ap[(ds, c)] = r
def sync_p99(x):
    return x['AZX']['x'].get('ack_p99', x['AZX']['u_p99_us'])


APW = {('deep_L12', 10): 'DeepTen', ('deep_L12', 100): 'DeepHundred', ('syn100k', 10): 'SynTen', ('syn100k', 100): 'SynHundred'}
for key, w in APW.items():
    if key in ap:
        x = ap[key]; s_ = x['AZX-DEFER-SAFE']
        mac(f'Ap{w}SyncMean', f(x['AZX']['u_mean_us'])); mac(f'Ap{w}SafeMean', f(ack(s_, 'mean')))
        mac(f'Ap{w}SyncP', f(sync_p99(x))); mac(f'Ap{w}SafeP', f(ack(s_, 'p99')))
        mac(f'Ap{w}FD', fi(s_['false_deny'])); mac(f'Ap{w}FA', fi(s_['false_allow']))
    else:
        for k in ['SyncMean', 'SafeMean', 'SyncP', 'SafeP', 'FD', 'FA']:
            mac(f'Ap{w}{k}', TODO)
dk = [k for k in ap if k[0] == 'deep_L12']
if dk:
    gm = [ap[k]['AZX']['u_mean_us'] / ack(ap[k]['AZX-DEFER-SAFE'], 'mean') for k in dk]
    gp = [sync_p99(ap[k]) / ack(ap[k]['AZX-DEFER-SAFE'], 'p99') for k in dk]
    mac('ApDeepMeanGainMin', f(min(gm))); mac('ApDeepMeanGainMax', f(max(gm)))
    mac('ApDeepPGainMin', f(min(gp))); mac('ApDeepPGainMax', f(max(gp)))
    med = [ack(ap[k]['AZX-DEFER-SAFE'], 'p50') for k in ap] + [ap[k]['AZX']['u_p50_us'] for k in ap]
    mac('ApMedianMax', f(max(med)))
    mac('ApFA', fi(sum(int(x['AZX-DEFER-SAFE']['false_allow']) for x in ap.values())))
else:
    for k in ['ApDeepMeanGainMin', 'ApDeepMeanGainMax', 'ApDeepPGainMin', 'ApDeepPGainMax', 'ApMedianMax', 'ApFA']:
        mac(k, TODO)

# ------------------------------------------------------------------ who / what
CUR.add(f'{TREE}/main')
who = {d: main[d][0][1].get('who') for d in main}
wr = [who[d][0] / who[d][2] for d in main if who[d]]
mac('WhoSlowMin', f(min(wr))); mac('WhoSlowMax', f(max(wr)))
what = {d: main[d][0][1].get('what') for d in main}
whr = [what[d][2] / what[d][0] for d in DEEP if what.get(d)]
mac('WhatDeepMin', f(min(whr))); mac('WhatDeepMax', f(max(whr)))

# AMZN facts
ma = meta('amzn')
mac('AmznUpdates', fi(ma['updates'])); mac('AmznRemoves', fi(ma['effective_removes'])); mac('AmznUsers', fi(ma['users']))

# ------------------------------------------------------------------ real hierarchies
section()
CUR.update(i['tag'] for v in realm.values() for _, i in v)
plain = {k: v for k, v in realm.items() if not k.endswith('_m4') and k != 'real_enwiki'}
mat = {k: v for k, v in realm.items() if k.endswith('_m4')}
if plain:
    rr = [mean_of(v, 'BFS') / mean_of(v, 'AZX') for v in plain.values()]
    mac('RealCount', str(len(plain))); mac('RealRatioMin', f(min(rr))); mac('RealRatioMax', f(max(rr)))
    cl = [v[0][1].get('closure', 0) for v in plain.values()]
    mac('RealClosureMin', f(min(cl))); mac('RealClosureMax', f(max(cl)))
    dmx = [int(meta(k).get('depth_max', 0)) for k in plain if meta(k)]
    mac('RealDepthMin', str(min(dmx))); mac('RealDepthMax', str(max(dmx)))
    mac('RealFA', fi(sum(int(r['AZX']['false_allow']) for v in realm.values() for r, _ in v)))
    mac('RealQueries', fi(sum(int(r['AZX']['queries']) for v in realm.values() for r, _ in v)))
if mat:
    rm = [mean_of(v, 'BFS') / mean_of(v, 'AZX') for v in mat.values()]
    mac('RealMRatioMin', f(min(rm))); mac('RealMRatioMax', f(max(rm)))
    mac('RealMUpdMax', f(max(mean_of(v, 'AZX', 'u_mean_us') for v in mat.values())))
for k in ['RealCount', 'RealRatioMin', 'RealRatioMax', 'RealClosureMin', 'RealClosureMax', 'RealDepthMin', 'RealDepthMax',
          'RealFA', 'RealQueries', 'RealMRatioMin', 'RealMRatioMax', 'RealMUpdMax']:
    if k not in M:
        mac(k, TODO)

# English Wikipedia category DAG (exact 128-bit counts, 5% user sample)
ENW = 'real_enwiki'
if ENW in realm:
    rs = realm[ENW]; m = meta(ENW); info = rs[0][1]
    per = [r['BFS']['q_mean_us'] / r['AZX']['q_mean_us'] for r, _ in rs]
    mac('EnwRatio', f(stt.mean(per))); mac('EnwRatioLo', f(min(per))); mac('EnwRatioHi', f(max(per)))
    mac('EnwAZX', f(mean_of(rs, 'AZX'))); mac('EnwBFS', f(mean_of(rs, 'BFS')))
    mac('EnwSeeds', str(len(rs)))
    mac('EnwClos', f(info['closure'])); mac('EnwClosMax', fi(info['closure_max'])); mac('EnwHubs', f(info['hubs']))
    mac('EnwDepth', m.get('depth_max', TODO)); mac('EnwUsers', fi(m.get('users', 0)))
    mac('EnwGroups', f"{int(m.get('groups', 0)) / 1e6:.1f}"); mac('EnwEdges', f"{int(m.get('m', 0)) / 1e6:.1f}")
    mac('EnwIndexGB', f"{rs[0][0]['AZX']['index_MB'] / 1024:.1f}")
    mac('EnwBuildS', f(rs[0][0]['AZX']['build_ms'] / 1000))
    mac('EnwUpdMs', f(mean_of(rs, 'AZX', 'u_mean_us') / 1000))
    mac('EnwUpdMedUs', f(stt.mean(r['AZX']['u_p50_us'] for r, _ in rs)))
    lg = [r['AZX']['x'].get('max_coef_log2', 0) for r, _ in rs]
    mac('EnwCoefBits', str(int(math.ceil(max(lg)))))
    mac('EnwFA', fi(sum(int(r['AZX']['false_allow']) for r, _ in rs)))
    mac('EnwSpiceFA', fi(sum(int(r['SPICE-ML100']['false_allow']) for r, _ in rs if 'SPICE-ML100' in r)))
    rss = []
    for sd in (1, 2, 3):
        pth = f'{REV}/main/{ENW}_s{sd}.txt'
        if os.path.exists(pth):
            mm_ = re.search(r'Maximum resident set size \(kbytes\): (\d+)', open(pth).read())
            if mm_: rss.append(int(mm_[1]) / 1024 / 1024)
    mac('EnwRSS', (f'{min(rss):.1f}' if f'{min(rss):.1f}' == f'{max(rss):.1f}' else f'{min(rss):.1f}--{max(rss):.1f}') if rss else TODO)
    dr, di = parse(f'{REV}/main_defer_enwiki.txt')
    if dr:
        check_build(f'{REV}/main_defer_enwiki.txt', di)
        CUR.add(f'{TREE}/main_defer_enwiki.txt')
        mac('EnwSafeFA', fi(dr['AZX-DEFER-SAFE']['false_allow'])); mac('EnwSafeFD', fi(dr['AZX-DEFER-SAFE']['false_deny']))
        mac('EnwNaiveFA', fi(dr['AZX-DEFER-NAIVE']['false_allow']))
    else:
        for k in ['EnwSafeFA', 'EnwSafeFD', 'EnwNaiveFA']: mac(k, TODO)
    hr_, _ = load('heavy', ENW, 'AZX')
    if hr_:
        mac('EnwPLLfa', fi(hr_['PLL-noDeny']['false_allow'])); mac('EnwPLLq', f(hr_['PLL-noDeny']['q_mean_us']))
        mac('EnwLCRq', f(hr_['LCR-LI']['q_mean_us']))
    else:
        for k in ['EnwPLLfa', 'EnwPLLq', 'EnwLCRq']: mac(k, TODO)
else:
    for k in ['EnwRatio', 'EnwRatioLo', 'EnwRatioHi', 'EnwAZX', 'EnwBFS', 'EnwSeeds', 'EnwClos', 'EnwClosMax', 'EnwHubs',
              'EnwDepth', 'EnwUsers', 'EnwGroups', 'EnwEdges', 'EnwIndexGB', 'EnwBuildS', 'EnwUpdMs', 'EnwUpdMedUs',
              'EnwCoefBits', 'EnwFA', 'EnwSpiceFA', 'EnwRSS', 'EnwSafeFA', 'EnwSafeFD', 'EnwNaiveFA', 'EnwPLLfa',
              'EnwPLLq', 'EnwLCRq']:
        mac(k, TODO)

# ------------------------------------------------------------------ SV-DTC variants
section()
SVV = ['SV-DTC', 'SV-DTC-deg5', 'SV-DTC-deg64']
sv = {}
for ds in DATA + REAL:
    r, info = load('main_sv', f'{ds}_s1', 'SV-DTC')
    if r:
        sv[ds] = r
have_var = any('SV-DTC-deg64' in r for r in sv.values())
if sv:
    best = {}
    for d, r in sv.items():
        cand = [v for v in SVV if v in r]
        best[d] = min(cand, key=lambda v: r[v]['q_mean_us'])
    mac('SVCount', str(len(sv)))
    fb = [r[v]['x'].get('fallback_frac', 0) for d, r in sv.items() for v in SVV if v in r]
    mac('SVFallbackMin', pct(min(fb))); mac('SVFallbackMax', pct(max(fb)))
    qr = [r[best[d]]['q_mean_us'] / r['AZX']['q_mean_us'] for d, r in sv.items()]
    mac('SVqRatioMin', f(min(qr))); mac('SVqRatioMax', f(max(qr)))
    br = [r[best[d]]['q_mean_us'] / r['BFS']['q_mean_us'] for d, r in sv.items()]
    mac('SVSlowerThanBFS', str(sum(1 for x in br if x > 1)))
    mac('SVfa', fi(sum(int(r[best[d]]['false_allow']) for d, r in sv.items())))
    mac('SVfaCond', fi(sum(int(r[best[d]]['x'].get('fa_cond', 0)) for d, r in sv.items())))
    mac('SVfaDeny', fi(sum(int(r[best[d]]['x'].get('fa_deny', 0)) for d, r in sv.items())))
    mac('SVfaStale', fi(sum(int(r[best[d]]['x'].get('fa_stale', 0)) for d, r in sv.items())))
    mac('SVVariants', 'three' if have_var else 'one')
else:
    for k in ['SVCount', 'SVFallbackMin', 'SVFallbackMax', 'SVqRatioMin', 'SVqRatioMax', 'SVSlowerThanBFS', 'SVfa',
              'SVfaCond', 'SVfaDeny', 'SVfaStale', 'SVVariants']:
        mac(k, TODO)

# ------------------------------------------------------------------ counts over all runs
section()
CUR.add(f'{TREE}/main')
mac('NumGraphsTotal', str(len(main) + len(srv) + len([k for k in realm if not k.endswith('_m4')])))
mac('NumChecksAll', fi(nq + sum(int(r['AZX']['queries']) for v in list(srv.values()) + list(realm.values()) for r, _ in v)))
snap = [d for d in ['youtube', 'dblp', 'lj', 'lj_full', 'orkut', 'friendster'] if d in main or d in srv]
mac('NumSnap', {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six'}.get(len(snap), str(len(snap))))
mac('SnapList', ', '.join(name(d) for d in snap))

# ------------------------------------------------------------------ exact maximum coefficients (log2)
def coef_log(r):
    x = r['AZX']['x']
    if 'max_coef_log2' in x:
        return float(x['max_coef_log2'])
    if 'max_coef' in x and float(x['max_coef']) > 0:
        return math.log2(float(x['max_coef']))
    return None
def max_log(rs_list):
    v = [coef_log(r) for rs in rs_list for r, _ in rs if coef_log(r) is not None]
    return max(v) if v else None
for word, group in [('Shallow', [main[d] for d in SHALLOW if d in main] + [srv[d] for d in SERVER if d in srv and not d.startswith('deep')]),
                    ('DeepSixteen', [main['deep_L16']] if 'deep_L16' in main else []),
                    ('DeepOneM', [srv['deep1m_L16']] if 'deep1m_L16' in srv else []),
                    ('RealModerate', [v for k, v in realm.items() if k != 'real_enwiki']),
                    ('Enw', [realm['real_enwiki']] if 'real_enwiki' in realm else [])]:
    v = max_log(group)
    mac(f'CoefLog{word}', f'{v:.1f}' if v is not None else TODO)

# ------------------------------------------------------------------ end-to-end work per update batch
section()
E2E = [d for d in ['deep_L16', 'deep1m_L16', 'real_enwiki'] if d in main or d in srv or d in realm]
def e2e_src(d):
    return main.get(d) or srv.get(d) or realm.get(d)
qs = [1, 10, 100, 1000]
rows = []
qstar = {}
for d in E2E:
    rs = e2e_src(d)
    cF, cB = mean_of(rs, 'AZX'), mean_of(rs, 'BFS')
    uF, uB = mean_of(rs, 'AZX', 'u_mean_us'), mean_of(rs, 'BFS', 'u_mean_us')
    tots = [(q * cF + uF, q * cB + uB) for q in qs]
    qstar[d] = (uF - uB) / (cB - cF) if cB > cF else float('inf')
    rows.append(f"{name(d)} & {f(uF)} & {f(cF)} & {f(cB)} & " + ' & '.join(f'{f(b / a)}$\\times$' for a, b in tots) + f" & {f(qstar[d])} \\\\")
for d, w in [('deep_L16', 'DeepSixteen'), ('deep1m_L16', 'DeepOneM'), ('real_enwiki', 'Enw')]:
    if d in qstar:
        rs = e2e_src(d)
        cF, cB = mean_of(rs, 'AZX'), mean_of(rs, 'BFS'); uF, uB = mean_of(rs, 'AZX', 'u_mean_us'), mean_of(rs, 'BFS', 'u_mean_us')
        mac(f'Qstar{w}', f(qstar[d])); mac(f'Tot{w}Ten', f((10 * cB + uB) / (10 * cF + uF)))
        mac(f'Tot{w}Thousand', f((1000 * cB + uB) / (1000 * cF + uF)))
        mac(f'Tot{w}Hundred', f((100 * cB + uB) / (100 * cF + uF)))
        mac(f'UpdPnn{w}', f(stt.mean(r['AZX']['u_p99_us'] for r, _ in rs) / 1000))
    else:
        for k in ['Qstar', 'TotTen', 'TotThousand', 'UpdPnn']:
            mac(f'{k[:-3] if k.endswith("Ten") else k}{w}' if False else (f'Qstar{w}' if k == 'Qstar' else f'Tot{w}Ten' if k == 'TotTen' else f'Tot{w}Thousand' if k == 'TotThousand' else f'UpdPnn{w}'), TODO)
if not rows:
    rows = [r'\multicolumn{9}{l}{\todo{end-to-end inputs pending}} \\']
wtab('e2e', r"""\begin{table}[t]
\centering
\caption{End-to-end work per update batch of $q$ checks and one write: ratio of total BFS time to total \sys time ($q\cdot\text{check}+\text{update}$, means of the main runs). $u$: \sys mean update cost ($\mu$s); $q^*$: break-even checks per update (BFS updates cost $<$0.1\,$\mu$s).}
\label{tab:e2e}
\footnotesize
\setlength{\tabcolsep}{2.2pt}
\begin{tabular}{lrrrrrrrr}
\toprule
 & & \multicolumn{2}{c}{Check} & \multicolumn{4}{c}{BFS/\sys total, $q=$} & \\
\cmidrule(lr){3-4}\cmidrule(lr){5-8}
Data & $u$ & FLIX & BFS & 1 & 10 & 100 & 1000 & $q^*$ \\
\midrule
""" + '\n'.join(rows) + r"""
\bottomrule
\end{tabular}
\end{table}
""")

# ------------------------------------------------------------------ probe/scan ablation
section()
mode = {}
for ds in ['deep_L12', 'deep_L16', 'lj', 'real_hpo']:
    v = {}
    for md in ['scan', 'probe', 'auto']:
        r, _ = load('sweep_mode', f'{ds}_{md}', 'AZX')
        if r:
            v[md] = r['AZX']['q_mean_us']
    if len(v) == 3:
        mode[ds] = v
mac('ModeList', ', '.join(name(d) for d in mode) if mode else TODO)
_deep = [d for d in ('deep_L12', 'deep_L16') if d in mode]
if _deep:
    mac('ModeDeepAutoOverProbeMax', f(max(mode[d]['auto'] / mode[d]['probe'] for d in _deep)))
    _sa = [mode[d]['scan'] / mode[d]['auto'] for d in _deep]
    mac('ModeDeepScanOverAutoMin', f(min(_sa))); mac('ModeDeepScanOverAutoMax', f(max(_sa)))
else:
    for k in ['ModeDeepAutoOverProbeMax', 'ModeDeepScanOverAutoMin', 'ModeDeepScanOverAutoMax']: mac(k, TODO)
if 'real_hpo' in mode:
    mac('ModeHpoAutoOverScan', f(mode['real_hpo']['auto'] / mode['real_hpo']['scan']))
    mac('ModeHpoScan', f(mode['real_hpo']['scan']))
    mac('HpoBFSMain', f(mean_of(realm['real_hpo'], 'BFS')) if 'real_hpo' in realm else TODO)
    _r, _ = load('sweep_mode', 'real_hpo_auto', 'AZX')
    mac('ModeHpoProbePct', f"{100 * _r['AZX']['x'].get('probe_frac', 0):.0f}")
else:
    mac('ModeHpoAutoOverScan', TODO); mac('ModeHpoProbePct', TODO); mac('ModeHpoScan', TODO); mac('HpoBFSMain', TODO)
if mode:
    mac('ModeCount', str(len(mode)))
    mac('ModeAutoOverBestMax', f(max(v['auto'] / min(v['scan'], v['probe']) for v in mode.values())))
    mac('ModeWorstOverAutoMax', f(max(max(v['scan'], v['probe']) / v['auto'] for v in mode.values())))
    if 'deep_L16' in mode:
        v = mode['deep_L16']; mac('ModeScanOverProbeDeep', f(v['scan'] / v['probe']))
    else:
        mac('ModeScanOverProbeDeep', TODO)
else:
    for k in ['ModeCount', 'ModeAutoOverBestMax', 'ModeWorstOverAutoMax', 'ModeScanOverProbeDeep']:
        mac(k, TODO)

# ------------------------------------------------------------------ deferral: flushes and AMZN slowdown
section()
if df:
    dpf = [r['AZX-DEFER-SAFE']['x'].get('deferred', 0) / max(1, r['AZX-DEFER-SAFE']['x'].get('flushes', 1)) for r, _ in df.values()]
    mac('DeferPerFlushMax', f(max(dpf)))
    if ('amzn', 64) in df:
        r = df[('amzn', 64)][0]
        _v = ack(r['AZX-DEFER-SAFE'], 'mean') / max(1e-9, r['AZX']['u_mean_us'])
        mac('AmznAckRatio', f'{round(_v, -1):.0f}' if _v >= 10 else f(_v))
    else:
        mac('AmznAckRatio', TODO)
else:
    mac('DeferPerFlushMax', TODO); mac('AmznAckRatio', TODO)
if ap:
    dpf = [x['AZX-DEFER-SAFE']['x'].get('deferred', 0) / max(1, x['AZX-DEFER-SAFE']['x'].get('flushes', 1)) for x in ap.values()]
    mac('ApDeferPerFlush', f(max(dpf)))
else:
    mac('ApDeferPerFlush', TODO)

# ------------------------------------------------------------------ ERP case study (results_erp/)
# A production ERP permission graph (de-identified, not released). The replay driver code/erp_replay.cpp
# is generated from authz.cpp by make_erp_replay.py; every file must name the expected derivation.
section()
ERPD = f"{ROOT}/{os.environ.get('AUTHZ_ERP_RESULTS', 'results_erp')}"
def erp(nm):
    p = f'{ERPD}/{nm}.txt'
    rr, info = parse(p)
    if not rr:
        MISSING.append(f'erp/{nm}'); return {}, {}
    txt = open(p).read()
    ok = (info.get('build', '').startswith('erp-') and f'derived_from={EXPECT_BUILD}' in txt) or info.get('build') == EXPECT_BUILD
    if not ok:
        raise SystemExit(f'{p}: unexpected build {info.get("build")}')
    CUR.add(os.path.relpath(ERPD, ROOT)); return rr, info
em = {}
for line in open(f'{ERPD}/erp.meta.txt') if os.path.exists(f'{ERPD}/erp.meta.txt') else []:
    k_, _, v_ = line.strip().partition(' '); em[k_] = v_
E3 = [erp(f'main_s{i}') for i in (1, 2, 3)]
E3 = [x for x in E3 if x[0]]
if E3 and em:
    import ast
    kinds = ast.literal_eval(em['stream_kinds'])
    mac('ErpUsers', fi(em['users'])); mac('ErpDepts', fi(em['depts'])); mac('ErpTenants', fi(em['tenants']))
    mac('ErpRoles', fi(em['roles'])); mac('ErpRes', fi(em['resources'])); mac('ErpEdges', fi(em['m']))
    mac('ErpDeny', fi(em['deny_edges'])); mac('ErpCond', fi(em['cond_edges'])); mac('ErpCtrl', fi(em['controlled_resources']))
    mac('ErpUpdates', fi(em['updates'])); mac('ErpShopGrants', fi(kinds['shop_grant']))
    mac('ErpRevokes', fi(kinds['shop_revoke'] + kinds.get('pdt_revoke', 0))); mac('ErpLevel', fi(kinds['shop_level']))
    mac('ErpDeact', fi(kinds['deactivate'])); mac('ErpTraceHits', fi(em['trace_hits'])); mac('ErpTracePairs', fi(em['trace_pairs']))
    mac('ErpClos', f"{stt.mean(i_['closure'] for _, i_ in E3):.1f}"); mac('ErpClosMax', fi(max(i['closure_max'] for _, i in E3)))
    eq = lambda m_: stt.mean(r_[m_]['q_mean_us'] for r_, _ in E3)
    mac('ErpAZX', f(eq('AZX'))); mac('ErpBFS', f(eq('BFS'))); mac('ErpRatio', f(eq('BFS') / eq('AZX')))
    mac('ErpClosure', f(eq('CLOSURE')))
    mac('ErpChecks', fi(sum(r_['AZX']['queries'] for r_, _ in E3)))
    mac('ErpNaiveFAMin', fi(min(r_['AZX-DEFER-NAIVE']['false_allow'] for r_, _ in E3)))
    mac('ErpNaiveFAMax', fi(max(r_['AZX-DEFER-NAIVE']['false_allow'] for r_, _ in E3)))
    mac('ErpSpiceFA', fi(max(r_['SPICE-ML100']['false_allow'] for r_, _ in E3)))
    mac('ErpSafeFDPct', '%.1f' % (100 * stt.mean(r_['AZX-DEFER-SAFE']['false_deny'] / r_['AZX-DEFER-SAFE']['queries'] for r_, _ in E3)))
    assert all(r_[m_]['false_allow'] == 0 for r_, _ in E3 for m_ in ('AZX', 'CLOSURE', 'AZX-DEFER-SAFE'))
    mac('ErpUpdAZX', f(stt.mean(r_['AZX']['u_mean_us'] for r_, _ in E3)))
    mac('ErpIdxMB', f(stt.mean(r_['AZX']['index_MB'] for r_, _ in E3)))
    erp_tot = sum(r_['AZX']['queries'] for r_, _ in E3)
    for nm_, key_ in (('q100', 'Hundred'), ('q1000', 'Thousand')):
        r_, _ = erp(nm_)
        if r_:
            mac(f'ErpSpiceFA{key_}', fi(r_['SPICE-ML100']['false_allow'])); erp_tot += r_['AZX']['queries']
            assert r_['AZX']['false_allow'] == 0 and r_['AZX-DEFER-SAFE']['false_allow'] == 0
    mac('ErpChecksAll', fi(erp_tot))
    r_, _ = erp('sv')
    if r_:
        mac('ErpSvFA', fi(r_['SV-DTC']['false_allow']))
    r_, _ = erp('heavy')
    if r_:
        mac('ErpPLLfa', fi(r_['PLL-noDeny']['false_allow'])); mac('ErpTCupd', f(r_['TC']['u_mean_us']))
    S3 = [erp(f'synthq_s{i}') for i in (1, 2, 3)]; S3 = [x for x in S3 if x[0]]
    if S3:
        mac('ErpSynRatio', f(stt.mean(r_['BFS']['q_mean_us'] for r_, _ in S3) / stt.mean(r_['AZX']['q_mean_us'] for r_, _ in S3)))
        assert all(r_['AZX']['false_allow'] == 0 for r_, _ in S3)
else:
    for k_ in ['ErpUsers', 'ErpAZX']:
        mac(k_, TODO)

# ------------------------------------------------------------------ E1: degree-aware probe/scan test (results_e1/)
# results_e1/main: code/authz_deg.cpp, generated from authz.cpp by make_degmode.py (the probe/scan test also
# weighs the user's out-degree; one changed line). results_e1/ctrl: the unchanged binary with the same two
# methods and arguments, run after it on the same idle server. Each file must name the expected build.
section()
E1D = f"{ROOT}/{os.environ.get('AUTHZ_E1_RESULTS', 'results_e1')}"
def e1(grp, nm):
    p = f'{E1D}/{grp}/{nm}.txt'
    rr, info = parse(p)
    if not rr or 'AZX' not in rr or 'BFS' not in rr:
        return None
    txt = open(p).read(); b = info.get('build', '')
    ok = (b.startswith('deg-') and f'# deg_mode derived_from={EXPECT_BUILD}' in txt) if grp == 'main' else b in (EXPECT_BUILD, EXPECT_BUILD + '-wide')
    if not ok:
        raise SystemExit(f'{p}: unexpected build {b}')
    CUR.add(os.path.relpath(f'{E1D}/{grp}', ROOT)); return rr, info
deg, ctl = {}, {}
for ds in DATA + SERVER + REAL + [d + '_m4' for d in REAL]:
    for s_ in (1, 2, 3):
        a, c = e1('main', f'{ds}_s{s_}'), e1('ctrl', f'{ds}_s{s_}')
        if a and c:
            deg.setdefault(ds, []).append(a); ctl.setdefault(ds, []).append(c)
def spd(rs): return mean_of(rs, 'BFS') / mean_of(rs, 'AZX')
if deg:
    RP = [d for d in ('real_go', 'real_hpo', 'real_simplewiki') if d in deg]
    mac('DegRealRatioMin', f(min(spd(deg[d]) for d in RP))); mac('DegRealRatioMax', f(max(spd(deg[d]) for d in RP)))
    mac('CtlRealRatioMin', f(min(spd(ctl[d]) for d in RP))); mac('CtlRealRatioMax', f(max(spd(ctl[d]) for d in RP)))
    if 'real_hpo' in deg:
        mac('DegHpoRatio', f(spd(deg['real_hpo']))); mac('CtlHpoRatio', f(spd(ctl['real_hpo'])))
        mac('DegHpoAZX', f(mean_of(deg['real_hpo'], 'AZX'))); mac('CtlHpoAZX', f(mean_of(ctl['real_hpo'], 'AZX')))
        mac('DegHpoBFS', f(mean_of(deg['real_hpo'], 'BFS')))
        mac('DegHpoProbePct', f(100 * xmean(deg['real_hpo'], 'AZX', 'probe_frac')))
        mac('CtlHpoProbePct', f(100 * xmean(ctl['real_hpo'], 'AZX', 'probe_frac')))
    if 'real_enwiki' in deg:
        mac('DegEnwRatio', f(spd(deg['real_enwiki']))); mac('CtlEnwRatio', f(spd(ctl['real_enwiki'])))
    # check-time change of FLIX (control / degree-aware) on every graph; >1: the degree-aware test is faster
    gain = {d: mean_of(ctl[d], 'AZX') / mean_of(deg[d], 'AZX') for d in deg}
    named = {'real_hpo': 'Hpo', 'real_simplewiki': 'Wiki', 'amzn': 'Amzn', 'youtube': 'Youtube', 'real_enwiki': 'Enw'}
    for d, k in named.items():
        if d in deg:
            mac(f'Deg{k}Gain', f(gain[d])); mac(f'Deg{k}Sp', f(spd(deg[d]))); mac(f'Ctl{k}Sp', f(spd(ctl[d])))
    oth = [g for d, g in gain.items() if d not in named]
    mac('DegOtherMin', f(min(oth))); mac('DegOtherMax', f(max(oth))); mac('DegOtherCount', str(len(oth)))
    mac('DegGraphs', str(len(deg)))
    mac('DegFA', fi(sum(int(r['AZX']['false_allow']) + int(r['AZX']['false_deny']) for v in deg.values() for r, _ in v)))
    mac('DegChecks', fi(sum(int(r['AZX']['queries']) for v in deg.values() for r, _ in v)))
    open(f'{E1D}/summary.tsv', 'w').write('dataset\tseeds\tBFS/FLIX_deg\tBFS/FLIX_ctrl\tFLIX_ctrl/FLIX_deg\n' + ''.join(
        f'{d}\t{len(deg[d])}\t{spd(deg[d]):.3f}\t{spd(ctl[d]):.3f}\t{gain[d]:.3f}\n' for d in deg))

# ================================================================== numbers.tex + sources
with open(f'{PAPER}/numbers.tex', 'w') as fh:
    fh.write('% generated by code/paper_facts.py -- do not edit\n')
    fh.write('\\providecommand{\\todo}[1]{\\textcolor{red}{[#1]}}\n')
    for k in sorted(M):
        fh.write(f'\\newcommand{{\\{k}}}{{{M[k]}\\xspace}}\n')
with open(f'{PAPER}/numbers_sources.txt', 'w') as fh:
    for k in sorted(M):
        fh.write(f'{k}\t{M[k]}\t{"; ".join(SRC[k])}\n')

# ================================================================== tables
# datasets
def ds_row(d, rs_info):
    m = meta(d)
    if not m:
        return None
    info = rs_info
    if m.get('partial'):
        return (f"{name(d)} & {fi(m['n'])} & {fi(m['m'])} & {fi(m['users'])} & {fi(m['subjects']) if m.get('subjects') else '--'} & -- & -- & "
                f"{fi(m['updates'])} & {f(info.get('closure'))} & {f(info.get('hubs'))} \\\\")
    return (f"{name(d)} & {fi(m['n'])} & {fi(m['m'])} & {fi(m['users'])} & {fi(int(m['groups']) + int(m['roles']))} & "
            f"{fi(m['deny_edges'])} & {fi(m['cond_edges'])} & {fi(m['updates'])} & {f(info.get('closure'))} & {f(info.get('hubs'))} \\\\")


blocks = []
for group in [SHALLOW, DEEP, [k for k in REAL if k in realm], [k for k in SERVER if k in srv]]:
    rows = []
    for d in group:
        src = main.get(d) or realm.get(d) or srv.get(d)
        if src:
            row = ds_row(d, src[0][1])
            if row:
                rows.append(row)
    blocks.append(rows)
if not blocks[3]:
    blocks[3] = [r'\multicolumn{10}{l}{\todo{server-scale datasets pending}} \\']
wtab('datasets', r"""\begin{table*}[t]
\centering
\caption{Datasets: shallow, deep synthetic, real nesting hierarchies, and server-scale graphs. ``Subj.'' counts groups, organisational units and roles; ``Clos.'' is the mean unconditional subject closure of a user, and ``Hubs'' the mean number of frontier hubs it reaches (1,000 sampled users). --: not recorded by the generator run.}
\label{tab:datasets}
\small
\begin{tabular}{lrrrrrrrrr}
\toprule
Dataset & $|V|$ & $|E|$ & Users & Subj. & Deny & Cond. & Updates & Clos. & Hubs \\
\midrule
""" + '\n\\midrule\n'.join('\n'.join(b) for b in blocks if b) + r"""
\bottomrule
\end{tabular}
\end{table*}
""")

# latency
FAST = ['AZX', 'CLOSURE', 'BFS', 'SPICE-FC', 'SPICE-ML100']
rows = []
for group in [SHALLOW, DEEP, [d for d in SERVER if d in srv]]:
    for d in group:
        rs = main.get(d) or srv.get(d)
        if not rs:
            continue
        vals = {m: mean_of(rs, m) for m in FAST}
        exact_best = min(vals[m] for m in ['AZX', 'CLOSURE', 'BFS', 'SPICE-FC'] if not math.isnan(vals[m]))
        cells = []
        for m in FAST:
            s = f(vals[m])
            if m != 'SPICE-ML100' and vals[m] == exact_best:
                s = r'\textbf{' + s + '}'
            cells.append(s)
        rr = vals['BFS'] / vals['AZX']
        rows.append(f"{name(d)} & " + ' & '.join(cells) + f" & {f(rr)}$\\times$ \\\\")
    rows.append(r'\midrule')
rows = rows[:-1]
ci = {}
for d in DATA:
    v = [r['AZX']['q_mean_us'] for r, _ in main[d]]
    if len(v) > 1:
        ci[d] = ci95(v) / stt.mean(v)
hi = [d for d in ci if ci[d] > 0.05]
ci_txt = '95\\% $t$-based CI of the \\sys mean within {:.0f}\\% of the mean'.format(max(100 * ci[d] for d in ci if ci[d] <= 0.05))
if hi:
    ci_txt += ', up to {:.0f}\\% on {}'.format(max(100 * ci[d] for d in hi), ', '.join(name(d) for d in hi))
short_main = [name(d) for d in DATA if d in SHORT]
seed_note = ('; ' + ', '.join(f'{name(d)}: {SHORT[d]} seeds' for d in DATA if d in SHORT) + ' (a run was killed)') if short_main else ''
wtab('latency', r"""\begin{table}[t]
\centering
\caption{Mean check latency ($\mu$s; batch-timed; 3 seeds, 1 for SYN-1M, LJ and the server-scale graphs in the last block""" + seed_note + '; ' + ci_txt + r"""). Best exact method in bold. C-ML is not exact (Table~\ref{tab:fresh}).}
\label{tab:latency}
\small
\setlength{\tabcolsep}{3.2pt}
\begin{tabular}{lrrrrrr}
\toprule
Dataset & FLIX & GrpCl & BFS & C-FC & C-ML & $\frac{\text{BFS}}{\text{FLIX}}$ \\
\midrule
""" + '\n'.join(rows) + r"""
\bottomrule
\end{tabular}
\end{table}
""")

# freshness
rows = []
for d in DATA:
    rs = main[d]
    q = sum(r['AZX']['queries'] for r, _ in rs)
    cells = []
    for m in ['AZX', 'SPICE-ML100', 'AZX-DEFER-SAFE', 'AZX-DEFER-NAIVE']:
        fa = sum(int(r[m]['false_allow']) for r, _ in rs); fd = sum(int(r[m]['false_deny']) for r, _ in rs)
        cells.append(f'{fi(fa)}/{fi(fd)}')
    pl = heavy.get(d, ({}, {}))[0].get('PLL-noDeny')
    cells.append(fi(pl['false_allow']) if pl else '--')
    rows.append(f"{name(d)} & {fi(q)} & " + ' & '.join(cells) + r' \\')
wtab('fresh', r"""\begin{table}[t]
\centering
\caption{False allows / false denies against the oracle, all checks of all seeds. C-ML: quantised check cache; FLIX-D: \sys with polarity-aware deferral; Lag: deferral of every insertion; 2-hop: reachability labels without conditions or deny, false allows only, on the shorter heavy runs (\PLLnq checks in total).}
\label{tab:fresh}
\footnotesize
\setlength{\tabcolsep}{1.6pt}
\begin{tabular}{lrrrrrr}
\toprule
Dataset & Checks & FLIX & C-ML & FLIX-D & Lag & 2-hop \\
\midrule
""" + '\n'.join(rows) + r"""
\midrule
""" + f"Total & {fi(nq)} & {fi(tot['AZX'][0])}/{fi(tot['AZX'][1])} & {fi(tot['SPICE-ML100'][0])}/{fi(tot['SPICE-ML100'][1])} & {fi(tot['AZX-DEFER-SAFE'][0])}/{fi(tot['AZX-DEFER-SAFE'][1])} & {fi(tot['AZX-DEFER-NAIVE'][0])}/{fi(tot['AZX-DEFER-NAIVE'][1])} & {fi(sum(x['false_allow'] for _, x in plfa))} \\\\" + r"""
\bottomrule
\end{tabular}
\end{table}
""")

# heavy
rows = []
for group in [SHALLOW, DEEP, [d for d in SERVER if d in heavy]]:
    for d in group:
        if d not in heavy:
            continue
        h = heavy[d][0]
        tc = tc_row(d)
        def cell(src, m, k):
            return f(src[m][k]) if m in src else 'n/a'
        mrow = upd.get(d) or (srv[d][0][0] if d in srv else {})
        rows.append(f"{name(d)} & {cell(mrow,'AZX','u_mean_us')} & {cell(mrow,'CLOSURE','u_mean_us')} & "
                    f"{cell(tc,'TC','u_mean_us')} & {cell(h,'LCR-LI','u_mean_us')} & "
                    f"{cell(mrow,'AZX','index_MB')} & {cell(mrow,'CLOSURE','index_MB')} & "
                    f"{cell(tc,'TC','index_MB')} & {cell(h,'LCR-LI','index_MB')} & {cell(h,'PLL-noDeny','index_MB')} & {cell(h,'LCR-LI','q_mean_us')} \\\\")
    rows.append(r'\midrule')
rows = rows[:-1]
wtab('heavy', r"""\begin{table*}[t]
\centering
\caption{Update cost (mean $\mu$s per update) and index size (MB). \sys and GrpClosure (both columns): full streams of the main runs (Table~\ref{tab:latency}). TC, LCR and the 2-hop index (PLL) are measured on a prefix of each stream (1{,}000 updates; 100 on DEEP-12/16, SYN-1M, LJ and the server-scale graphs). TC is shown where it finished within the budget; on SYN-1M it was timed on \TCSynOneMUpdates updates. One PLL rebuild costs its build time (Sec.~\ref{sec:exp-update}). LCR check: mean $\mu$s per check on the prefix.}
\label{tab:heavy}
\small
\begin{tabular}{lrrrrrrrrrr}
\toprule
 & \multicolumn{4}{c}{Update cost ($\mu$s)} & \multicolumn{5}{c}{Index size (MB)} & LCR \\
\cmidrule(lr){2-5}\cmidrule(lr){6-10}
Dataset & FLIX & GrpCl & TC & LCR & FLIX & GrpCl & TC & LCR & 2-hop & check \\
\midrule
""" + '\n'.join(rows) + r"""
\bottomrule
\end{tabular}
\end{table*}
""")

# deferral: thresholds and expensive apply
rows = []
for ds in ['ent', 'syn100k', 'amzn']:
    for B in [16, 64, 1024]:
        if (ds, B) not in df:
            continue
        r = df[(ds, B)][0]
        s, n, a = r['AZX-DEFER-SAFE'], r.get('AZX-DEFER-NAIVE'), r['AZX']
        rows.append(f"{name(ds)} & $B{{=}}{B}$ & {pct(share(s))}\\% & {f(ack(s,'mean'))} & {f(ack(s,'p99'))} & {f(a['u_mean_us'])} & {f(a['u_p99_us'])} & "
                    f"{fi(s['false_allow'])}/{fi(s['false_deny'])} & {fi(n['false_allow']) if n else '--'} \\\\")
rows.append(r'\midrule')
if ap:
    for (ds, c), r in ap.items():
        s, a = r['AZX-DEFER-SAFE'], r['AZX']
        rows.append(f"{name(ds)} & {c}\\,$\\mu$s & {pct(share(s))}\\% & {f(ack(s,'mean'))} & {f(ack(s,'p99'))} & {f(a['u_mean_us'])} & {f(r['AZX']['x'].get('ack_p99', a['u_p99_us']))} & "
                    f"{fi(s['false_allow'])}/{fi(s['false_deny'])} & -- \\\\")
else:
    rows.append(r'\multicolumn{9}{l}{\todo{expensive-apply runs pending (results\_rev1/sweep\_apply)}} \\')
wtab('defer', r"""\begin{table}[t]
\centering
\caption{Deferred maintenance. Top: flush threshold $B$ (5{,}000 updates). Bottom: each label propagation costs an extra 10 or 100\,$\mu$s (e.g., a replica round trip); threshold flushes run in the background, while applying the buffered subject insertions before a restrictive write stays on its acknowledgment path ($B{=}64$). Ack: time until a write is acknowledged, over all writes including threshold flushes ($\mu$s). Def.: share of deferred updates; FA/FD: false allows / denies of FLIX-D; Lag: false allows.}
\label{tab:defer}
\footnotesize
\setlength{\tabcolsep}{2.0pt}
\begin{tabular}{llrrrrrrr}
\toprule
 & & \multicolumn{3}{c}{FLIX-D} & \multicolumn{2}{c}{FLIX (sync)} & & \\
\cmidrule(lr){3-5}\cmidrule(lr){6-7}
Data & Setting & Def. & Ack & p99 & Ack & p99 & FA/FD & Lag \\
\midrule
""" + '\n'.join(rows) + r"""
\bottomrule
\end{tabular}
\end{table}
""")

# read/write ratio
rows = []
for ds in ['ent', 'syn100k', 'deep_L16', 'amzn']:
    for q in [10, 100, 1000]:
        if (ds, q) not in rat:
            continue
        r, nu, rev = rat[(ds, q)]
        c = r['SPICE-ML100']
        rows.append(f"{name(ds)} & {q} & {fi(nu)} & {fi(rev) if rev is not None else '--'} & {pct(c['x'].get('hit_rate', 0))}\\% & {fi(c['false_allow'])} & "
                    f"{f(c['false_allow'] / max(1, rev)) if rev else '--'} & {fi(r['AZX-DEFER-NAIVE']['false_allow']) if 'AZX-DEFER-NAIVE' in r else '--'} \\\\")
if not rows:
    rows = [r'\multicolumn{8}{l}{\todo{read/write sweep pending (results\_rev1/sweep\_ratio)}} \\']
wtab('ratio', r"""\begin{table}[t]
\centering
\caption{Read/write ratio: checks per update $q$. Del./deny: grant and membership deletions plus deny insertions (revoking subject insertions are not counted). C-ML: Zanzibar-style cache with a window of 100 writes (5\,s at \WriteRate writes/s); FA/del.: its false allows per such update.}
\label{tab:ratio}
\footnotesize
\setlength{\tabcolsep}{2.4pt}
\begin{tabular}{lrrrrrrr}
\toprule
Data & $q$ & Upd. & Del./deny & Hit & C-ML FA & FA/del. & Lag FA \\
\midrule
""" + '\n'.join(rows) + r"""
\bottomrule
\end{tabular}
\end{table}
""")

# SV-DTC
rows = []
for d, r in sv.items():
    cells = []
    for v in (SVV[1:] if have_var else SVV[:1]):
        if v in r:
            cells.append(f"{f(r[v]['q_mean_us'])} & {pct(r[v]['x'].get('fallback_frac', 0))}\\%")
        else:
            cells.append('-- & --')
    b = min([v for v in SVV if v in r], key=lambda v: r[v]['q_mean_us'])
    rows.append(f"{name(d)} & {f(r['AZX']['q_mean_us'])} & {f(r['BFS']['q_mean_us'])} & " + ' & '.join(cells) + f" & {fi(r[b]['false_allow'])} \\\\")
if not rows:
    rows = [r'\multicolumn{8}{l}{\todo{SV-DTC results pending (results\_rev1/main\_sv)}} \\']
hdr_var = r'\multicolumn{2}{c}{SV, $K{=}5$} & \multicolumn{2}{c}{SV, $K{=}64$}' if have_var else r'\multicolumn{2}{c}{SV-DTC}'
ncol = 6 if have_var else 4
wtab('sv', r"""\begin{table}[t]
\centering
\caption{\sys against the support-vertex dynamic transitive closure (SV) of~\cite{varuni2026icact}, reimplemented with $K$ highest-degree group support vertices and a BFS fallback that is aware of resource ancestors. Check $\mu$s; FB: share of checks answered by the fallback; FA: false allows of the faster variant under Definition~\ref{def:allow}; --: not run (time and memory budget).}
\label{tab:sv}
\footnotesize
\setlength{\tabcolsep}{2.4pt}
\begin{tabular}{lrr""" + 'rr' * (2 if have_var else 1) + r"""r}
\toprule
 & & & """ + hdr_var + r""" & \\
""" + (r'\cmidrule(lr){4-5}\cmidrule(lr){6-7}' if have_var else r'\cmidrule(lr){4-5}') + r"""
Data & FLIX & BFS & """ + ('Check & FB & Check & FB' if have_var else 'Check & FB') + r""" & FA \\
\midrule
""" + '\n'.join(rows) + r"""
\bottomrule
\end{tabular}
\end{table}
""")

# real hierarchies
rows = []
for k in REAL:
    for v in (k + '_m4', k):
        if v not in realm:
            continue
        rs = realm[v]; info = rs[0][1]; m = meta(v)
        bq, aq = mean_of(rs, 'BFS'), mean_of(rs, 'AZX')
        rows.append(f"{name(v)} & {m.get('depth_max', '--')} & {f(info.get('closure'))} & {f(info.get('hubs'))} & {f(bq)} & {f(aq)} & "
                    f"{f(bq / aq)}$\\times$ & {f(mean_of(rs, 'AZX', 'u_mean_us'))} & {fi(sum(int(r['AZX']['false_allow']) for r, _ in rs))} \\\\")
    if k in deg:
        rs = deg[k]; bq, aq = mean_of(rs, 'BFS'), mean_of(rs, 'AZX')
        rows.append(f"{name(k)}$^{{d}}$ & \\multicolumn{{3}}{{r}}{{\\emph{{original test:}} {f(spd(ctl[k]))}$\\times$}} & {f(bq)} & {f(aq)} & {f(bq / aq)}$\\times$ & {f(mean_of(rs, 'AZX', 'u_mean_us'))} & "
                    f"{fi(sum(int(r['AZX']['false_allow']) for r, _ in rs))} \\\\")
if not rows:
    rows = [r'\multicolumn{9}{l}{\todo{real hierarchies pending}} \\']
wtab('real', r"""\begin{table}[t]
\centering
\caption{Real nesting hierarchies (3 seeds""" + (', ' + ', '.join(f'{name(d)}: {SHORT[d]}' for d in SHORT if d.startswith('real')) if any(d.startswith('real') for d in SHORT) else '') + r"""; mean $\mu$s). Depth: longest nesting chain; Upd.: mean $\mu$s per update; FA: false allows of \sys. $^{m}$: users with at least four memberships get a personal group, which materialises their label (same allow set). $^{d}$: probe/scan test weighted by the user's out-degree, in a run of \sys and BFS only, next to the BFS/\sys ratio of the original test in the same setting (Section~\ref{sec:exp-real}).}
\label{tab:real}
\footnotesize
\setlength{\tabcolsep}{2.6pt}
\begin{tabular}{lrrrrrrrr}
\toprule
Hierarchy & Depth & Clos. & Hubs & BFS & FLIX & $\frac{\text{BFS}}{\text{FLIX}}$ & Upd. & FA \\
\midrule
""" + '\n'.join(rows) + r"""
\bottomrule
\end{tabular}
\end{table}
""")

# ================================================================== figures
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size': 8, 'font.family': 'serif', 'axes.spines.top': False, 'axes.spines.right': False,
                     'pdf.fonttype': 42, 'axes.linewidth': 0.6})
LS = {'AZX': '-', 'BFS': '--', 'CLOSURE': ':'}
LBL = {'AZX': 'FLIX', 'BFS': 'BFS', 'CLOSURE': 'GrpClosure', 'TC': 'TC', 'LCR': 'LCR'}
C = {'AZX': '#2a6fdb', 'BFS': '#e0702b', 'CLOSURE': '#2e9e6b', 'TC': '#8a5cc2', 'LCR': '#c43d5a'}

# depth sweep with 95% CI over seeds
Ls = sorted(dep)
xs = [stt.mean(i.get('closure', 0) for _, i in dep[L]) for L in Ls]
fig, ax = plt.subplots(1, 2, figsize=(3.45, 1.7))
for meth in ['BFS', 'CLOSURE', 'AZX']:
    for j, key in enumerate(['q_mean_us', 'u_mean_us']):
        if meth == 'BFS' and j == 1:
            continue
        ys = [stt.mean(r[meth][key] for r, _ in dep[L]) for L in Ls]
        es = [ci95([r[meth][key] for r, _ in dep[L]]) for L in Ls]
        ax[j].errorbar(xs, ys, yerr=es, lw=1.2, color=C[meth], label=LBL[meth], ls=LS[meth], capsize=1.5, elinewidth=0.6)
for a, t in zip(ax, ['check latency ($\\mu$s)', 'update cost ($\\mu$s)']):
    a.set_xscale('log'); a.set_yscale('log'); a.set_xlabel('mean user closure'); a.set_ylabel(t)
    a.grid(True, which='major', lw=0.3, alpha=0.4)
h, l = ax[0].get_legend_handles_labels()
fig.tight_layout(pad=0.3, w_pad=0.8, rect=(0, 0, 1, 0.86))
fig.legend(h, l, frameon=False, loc='upper center', bbox_to_anchor=(0.5, 1.0), ncol=3, fontsize=6.5, handlelength=1.8)
fig.savefig(f'{PAPER}/figs/depth.pdf')
plt.close(fig)

# update cost per dataset (log bars)
UD = [d for d in DATA if d in heavy]
x = np.arange(len(UD)); w = 0.2
fig, ax = plt.subplots(figsize=(3.45, 1.8))
series = [('AZX', lambda d: upd[d]['AZX']['u_mean_us']),
          ('CLOSURE', lambda d: upd[d]['CLOSURE']['u_mean_us']),
          ('TC', lambda d: tc_row(d)['TC']['u_mean_us'] if 'TC' in tc_row(d) else np.nan),
          ('LCR', lambda d: heavy[d][0]['LCR-LI']['u_mean_us'] if 'LCR-LI' in heavy[d][0] else np.nan)]
for i, (m, fn) in enumerate(series):
    ax.bar(x + (i - 1.5) * w, [fn(d) for d in UD], w, color=C[m], label=LBL[m], linewidth=0)
ax.set_yscale('log'); ax.set_ylabel('$\\mu$s / update')
ax.set_xticks(x); ax.set_xticklabels([name(d) for d in UD], rotation=60, ha='right', rotation_mode='anchor', fontsize=5.8)
ax.legend(frameon=False, ncol=4, fontsize=6.5, loc='lower center', bbox_to_anchor=(0.5, 1.0))
ax.grid(True, axis='y', which='major', lw=0.3, alpha=0.4)
fig.tight_layout(pad=0.3)
fig.savefig(f'{PAPER}/figs/update.pdf')
plt.close(fig)

# cache window sweep
fig, ax = plt.subplots(figsize=(2.15, 1.6))
for ds, col in [('ent', '#2a6fdb'), ('syn100k', '#e0702b'), ('amzn', '#2e9e6b')]:
    Ws = [W for W in [10, 100, 1000] if (ds, W) in sp]
    ax.plot([W / WRATE for W in Ws], [sp[(ds, W)][f'SPICE-ML{W}']['false_allow'] for W in Ws], lw=1.2, color=col,
            label=name(ds), ls={'ent': '-', 'syn100k': '--', 'amzn': ':'}[ds])
ax.set_xscale('log'); ax.set_xlabel(f'window (s at {WRATE} writes/s)'); ax.set_ylabel('false allows')
ax.legend(frameon=False, fontsize=6, loc='lower center', bbox_to_anchor=(0.5, 1.0), ncol=3, handlelength=1.0, columnspacing=0.8)
ax.grid(True, lw=0.3, alpha=0.4)
fig.tight_layout(pad=0.3)
pass  # cache-window figure dropped (round 3); finding stated in text
# fig.savefig(f'{PAPER}/figs/cache.pdf')
plt.close(fig)

exec(open(f'{ROOT}/code/paper_fig1.py').read())

n_todo = sum(1 for v in M.values() if 'todo' in v)
srcs = {}
for k, v in SRC.items():
    for s in v:
        srcs.setdefault(s, []).append(k)
print(f'{len(M)} macros, {n_todo} placeholders: ' + ', '.join(k for k, v in sorted(M.items()) if 'todo' in v))
for s, ks in sorted(srcs.items()):
    print(f'  {s}: {len(ks)} macros')
