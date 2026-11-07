#!/usr/bin/env python3
"""Workload generator for the AuthZ-Graph pilot.

Produces an authorization graph (users, groups/org units, roles, resources),
conditional edges (conjunctions of context atoms, encoded as 64-bit masks),
deny edges, and an update stream, in a binary layout read by authz.cpp.

Node-id convention (keeps the subject side a DAG under updates):
  roles [0, nR)  <  groups/org units  <  users  <  resources.
Every subject->subject edge goes from a higher id to a lower id.
Senior roles get higher ids than the junior roles they inherit.

Usage:
  gen.py syn   <out> <n_users> [seed]
  gen.py snap  <out> <cmty.txt.gz> [seed] [max_groups]
  gen.py ent   <out> [seed]
  gen.py amzn  <out> <amzn-anon-access-samples-2.0.csv> [seed]
"""
import gzip, os, sys, random, struct
import numpy as np

USER, GROUP, ROLE, RES = 0, 1, 2, 3

# ---------------------------------------------------------------- context
class Ctx:
    """Context schema: attributes with finite domains; atom = (attr, value)."""
    def __init__(self, domains):
        self.domains = domains
        self.off = np.cumsum([0] + domains[:-1]).tolist()
        assert sum(domains) <= 64
    def atom(self, a, v):
        return 1 << (self.off[a] + v)
    def rand_cond(self, rng, p_cond, p_two=0.2, attrs=None):
        """Random conjunction of 1-2 atoms on distinct attributes (or 0 = true)."""
        if rng.random() >= p_cond:
            return 0
        pool = attrs if attrs is not None else list(range(len(self.domains)))
        k = 2 if (rng.random() < p_two and len(pool) > 1) else 1
        m = 0
        for a in rng.sample(pool, k):
            m |= self.atom(a, rng.randrange(self.domains[a]))
        return m

DEFAULT_CTX = [6, 3, 3, 3, 5]   # purpose, network, time window, device, region


class Builder:
    def __init__(self, seed, ctx_domains=DEFAULT_CTX):
        self.rng = random.Random(seed)
        self.ctx = Ctx(ctx_domains)
        self.types = []
        self.kind = []          # fine-grained label for stats only
        self.edges = {}         # (src,dst) -> (mask, pol)

    def add_nodes(self, t, k, label):
        s = len(self.types)
        self.types += [t] * k
        self.kind += [label] * k
        return list(range(s, s + k))

    def add_edge(self, s, d, mask=0, pol=1):
        if (s, d) in self.edges or s == d:
            return False
        ts, td = self.types[s], self.types[d]
        if ts != RES and td != RES:
            assert s > d, (s, d)
        self.edges[(s, d)] = (mask, pol)
        return True

    # ------------------------------------------------------------ resources
    def resources(self, n_cat, n_tab, n_col, n_rowpart):
        """catalog -> table -> {columns, row partitions}; contains edges parent->child."""
        self.cats, self.tabs, self.cols, self.rows = [], [], [], []
        for _ in range(n_cat):
            c = self.add_nodes(RES, 1, 'catalog')[0]
            self.cats.append(c)
            for _ in range(n_tab):
                t = self.add_nodes(RES, 1, 'table')[0]
                self.tabs.append(t)
                self.add_edge(c, t)
                for col in self.add_nodes(RES, n_col, 'column'):
                    self.cols.append(col)
                    self.add_edge(t, col)
                for rp in self.add_nodes(RES, n_rowpart, 'rowpart'):
                    self.rows.append(rp)
                    self.add_edge(t, rp)

    def rand_resource(self, w=(0.05, 0.55, 0.2, 0.2)):
        x = self.rng.random()
        if x < w[0]:
            return self.rng.choice(self.cats)
        if x < w[0] + w[1]:
            return self.rng.choice(self.tabs)
        if x < w[0] + w[1] + w[2]:
            return self.rng.choice(self.cols)
        return self.rng.choice(self.rows)

    # ------------------------------------------------------------ roles
    def roles(self, n_roles, p_inherit=0.4):
        self.role_ids = self.add_nodes(ROLE, n_roles, 'role')
        for r in self.role_ids:
            if r > 0 and self.rng.random() < p_inherit:
                for _ in range(self.rng.randint(1, 2)):
                    self.add_edge(r, self.rng.randrange(0, r))

    def grants(self, per_role=(3, 10), p_cond=0.25, deny_frac=0.03, deny_subjects=None):
        n_grants = 0
        for r in self.role_ids:
            for _ in range(self.rng.randint(*per_role)):
                if self.add_edge(r, self.rand_resource(), self.ctx.rand_cond(self.rng, p_cond), 1):
                    n_grants += 1
        subj = deny_subjects
        for _ in range(int(deny_frac * n_grants)):
            s = self.rng.choice(subj)
            self.add_edge(s, self.rand_resource((0.0, 0.6, 0.3, 0.1)),
                          self.ctx.rand_cond(self.rng, 0.15), -1)

    # ------------------------------------------------------------ update stream
    def updates(self, n_upd, groups, users, mix=None):
        """Valid stream of edge insertions/deletions (half each), DAG-preserving."""
        rng = self.rng
        mix = mix or {'member': 0.40, 'nest': 0.05, 'assign': 0.20, 'grant': 0.20,
                      'deny': 0.10, 'cond': 0.05}
        kinds = list(mix)
        probs = np.array([mix[k] for k in kinds]); probs /= probs.sum()
        by_kind = {k: [] for k in kinds}
        roles = self.role_ids
        for (s, d), (m, p) in self.edges.items():
            ts, td = self.types[s], self.types[d]
            if p < 0:
                by_kind['deny'].append((s, d))
            elif td == RES and ts != RES:
                by_kind['grant'].append((s, d))
            elif ts == USER and td == GROUP:
                by_kind['member'].append((s, d))
            elif ts == GROUP and td == GROUP:
                by_kind['nest'].append((s, d))
            elif td == ROLE:
                by_kind['assign'].append((s, d))
        live = {k: list(v) for k, v in by_kind.items()}
        ops = []
        gmin = min(groups) if groups else 0

        def pop_random(lst):
            while lst:
                i = rng.randrange(len(lst))
                lst[i], lst[-1] = lst[-1], lst[i]
                e = lst.pop()
                if e in self.edges:
                    return e
            return None

        def new_edge(k):
            for _ in range(50):
                if k == 'member':
                    s, d = rng.choice(users), rng.choice(groups)
                    m, p = 0, 1
                elif k == 'nest':
                    s = rng.choice(groups)
                    if s == gmin:
                        continue
                    d = rng.randrange(gmin, s)
                    m, p = 0, 1
                elif k == 'assign':
                    s = rng.choice(groups) if rng.random() < 0.85 else rng.choice(users)
                    d = rng.choice(roles)
                    m, p = self.ctx.rand_cond(rng, 0.2), 1
                elif k == 'grant':
                    s, d = rng.choice(roles), self.rand_resource()
                    m, p = self.ctx.rand_cond(rng, 0.25), 1
                else:  # deny
                    x = rng.random()
                    s = rng.choice(groups) if x < 0.6 else (rng.choice(roles) if x < 0.8 else rng.choice(users))
                    d = self.rand_resource((0.0, 0.6, 0.3, 0.1))
                    m, p = self.ctx.rand_cond(rng, 0.15), -1
                if (s, d) not in self.edges and s != d:
                    return s, d, m, p
            return None

        while len(ops) < n_upd:
            k = kinds[np.searchsorted(np.cumsum(probs), rng.random())]
            if k == 'cond':
                pool = live['grant'] + live['assign']
                e = pop_random(pool) if pool else None
                if e is None:
                    continue
                m, p = self.edges.pop(e)
                ops.append((-1, e[0], e[1], m, p))
                nm = self.ctx.rand_cond(rng, 1.0)
                self.edges[e] = (nm, p)
                ops.append((1, e[0], e[1], nm, p))
                continue
            if rng.random() < 0.5 and live[k]:
                e = pop_random(live[k])
                if e is None:
                    continue
                m, p = self.edges.pop(e)
                ops.append((-1, e[0], e[1], m, p))
            else:
                ne = new_edge(k)
                if ne is None:
                    continue
                s, d, m, p = ne
                self.edges[(s, d)] = (m, p)
                live[k].append((s, d))
                ops.append((1, s, d, m, p))
        return ops[:n_upd]

    # ------------------------------------------------------------ output
    def write(self, out, initial_edges, ops, extra=None):
        os.makedirs(out, exist_ok=True)
        np.array(self.types, dtype=np.int8).tofile(f'{out}/types.bin')
        E = initial_edges
        np.array([e[0] for e in E], dtype=np.int32).tofile(f'{out}/e_src.bin')
        np.array([e[1] for e in E], dtype=np.int32).tofile(f'{out}/e_dst.bin')
        np.array([e[2] for e in E], dtype=np.uint64).tofile(f'{out}/e_mask.bin')
        np.array([e[3] for e in E], dtype=np.int8).tofile(f'{out}/e_pol.bin')
        np.array([o[0] for o in ops], dtype=np.int8).tofile(f'{out}/u_op.bin')
        np.array([o[1] for o in ops], dtype=np.int32).tofile(f'{out}/u_src.bin')
        np.array([o[2] for o in ops], dtype=np.int32).tofile(f'{out}/u_dst.bin')
        np.array([o[3] for o in ops], dtype=np.uint64).tofile(f'{out}/u_mask.bin')
        np.array([o[4] for o in ops], dtype=np.int8).tofile(f'{out}/u_pol.bin')
        t = np.array(self.types)
        with open(f'{out}/meta.txt', 'w') as f:
            f.write(f'n {len(self.types)}\nm {len(E)}\nupdates {len(ops)}\n')
            f.write('ctx ' + ' '.join(map(str, self.ctx.domains)) + '\n')
            for name, tt in (('users', USER), ('groups', GROUP), ('roles', ROLE), ('resources', RES)):
                f.write(f'{name} {int((t == tt).sum())}\n')
            npos = sum(1 for e in E if e[3] > 0)
            f.write(f'deny_edges {len(E) - npos}\n')
            f.write(f'cond_edges {sum(1 for e in E if e[2])}\n')
            for k, v in (extra or {}).items():
                f.write(f'{k} {v}\n')


def snapshot(b):
    return [(s, d, m, p) for (s, d), (m, p) in b.edges.items()]


# ================================================================= datasets
def gen_syn(out, n_users, seed=1, n_upd=10000):
    """Zanzibar-style synthetic: org tree + nested project groups + role DAG."""
    b = Builder(seed)
    rng = b.rng
    n_roles = max(100, n_users // 100)
    n_depts = max(20, n_users // 50)
    n_groups = max(10, n_users // 100)
    b.roles(n_roles)
    depts = b.add_nodes(GROUP, n_depts, 'dept')
    for d in depts[1:]:                       # tree: parent has lower id
        lo = max(depts[0], d - 1 - rng.randint(0, 8))
        b.add_edge(d, rng.randint(depts[0], d - 1) if rng.random() < 0.3 else lo)
    pgroups = b.add_nodes(GROUP, n_groups, 'group')
    for g in pgroups:
        if g > pgroups[0] and rng.random() < 0.3:
            b.add_edge(g, rng.randint(pgroups[0], g - 1))
    users = b.add_nodes(USER, n_users, 'user')
    zipf = np.random.default_rng(seed).zipf(1.6, size=4 * n_users)
    zi = 0
    for u in users:
        b.add_edge(u, rng.choice(depts[len(depts) // 4:]))     # users sit in lower depts
        for _ in range(np.random.default_rng(seed + u).poisson(1.0)):
            g = pgroups[(int(zipf[zi % len(zipf)]) - 1) % len(pgroups)]; zi += 1
            b.add_edge(u, g)
        if rng.random() < 0.05:
            b.add_edge(u, rng.choice(b.role_ids), b.ctx.rand_cond(rng, 0.2))
    for d in depts:
        if rng.random() < 0.3:
            for _ in range(rng.randint(1, 2)):
                b.add_edge(d, rng.choice(b.role_ids), b.ctx.rand_cond(rng, 0.2))
    for g in pgroups:
        for _ in range(rng.randint(1, 3)):
            b.add_edge(g, rng.choice(b.role_ids), b.ctx.rand_cond(rng, 0.2))
    nc = 10 + n_users // 10000
    b.resources(nc, 30, 15, 4)
    b.grants(per_role=(5, 20), p_cond=0.3, deny_frac=0.08,
             deny_subjects=depts + pgroups + b.role_ids + users[: max(1, n_users // 100)])
    init = snapshot(b)
    ops = b.updates(n_upd, depts + pgroups, users)
    b.write(out, init, ops, {'source': f'synthetic-zanzibar n_users={n_users} seed={seed}'})


def gen_snap(out, cmty_path, seed=1, max_groups=None, n_upd=10000):
    """SNAP ground-truth communities as the user->group layer (Yang & Leskovec 2012)."""
    rng = random.Random(seed)
    comms = []
    with gzip.open(cmty_path, 'rt') as f:
        for line in f:
            xs = line.split()
            if len(xs) >= 3:
                comms.append(xs)
    comms.sort(key=len, reverse=True)          # larger communities get lower ids
    if max_groups:
        comms = comms[:max_groups]
    b = Builder(seed)
    n_groups = len(comms)
    n_roles = max(500, n_groups // 10)
    b.roles(n_roles)
    gids = b.add_nodes(GROUP, n_groups, 'community')
    member_of = {}
    for gi, c in enumerate(comms):
        for x in c:
            member_of.setdefault(x, []).append(gi)
    # nest each community under the larger community it overlaps most (>= 50%)
    n_nest = 0
    for gi in range(1, n_groups):
        cnt = {}
        for x in comms[gi]:
            for gj in member_of[x]:
                if gj < gi:
                    cnt[gj] = cnt.get(gj, 0) + 1
        if cnt:
            gj, c = max(cnt.items(), key=lambda kv: kv[1])
            if c >= 0.5 * len(comms[gi]):
                b.add_edge(gids[gi], gids[gj]); n_nest += 1
    names = sorted(member_of)
    users = b.add_nodes(USER, len(names), 'user')
    for u, x in zip(users, names):
        for gi in member_of[x]:
            b.add_edge(u, gids[gi])
        if rng.random() < 0.01:
            b.add_edge(u, rng.choice(b.role_ids), b.ctx.rand_cond(rng, 0.2))
    for g in gids:
        if rng.random() < 0.5:
            for _ in range(rng.randint(1, 2)):
                b.add_edge(g, rng.choice(b.role_ids), b.ctx.rand_cond(rng, 0.2))
    b.resources(20, 40, 15, 4)
    b.grants(per_role=(5, 20), p_cond=0.3, deny_frac=0.08,
             deny_subjects=gids + b.role_ids + users[: max(1, len(users) // 100)])
    init = snapshot(b)
    ops = b.updates(n_upd, gids, users)
    b.write(out, init, ops, {'source': f'SNAP {os.path.basename(cmty_path)}',
                             'nest_edges': n_nest})


def gen_ent(out, seed=7, n_upd=10000):
    """Shape-faithful reconstruction of a production data-governance permission model (no real rows).

    Published shape statistics only: ~813 departments + 27 orphan departments attached
    to the root, ~4.2k organisation members, one department group per department
    (grants to a department cover its subtree), single-parent user-group tree,
    application-scoped roles whose members are users or groups, row grants as
    field=value conditions (row partitions), column grants, deny rules.
    """
    ctx = [6, 2, 3, 4]          # application, network, purpose, device
    b = Builder(seed, ctx)
    rng = b.rng
    n_apps, roles_per_app = 6, 8
    b.roles(n_apps * roles_per_app, p_inherit=0.25)
    app_of_role = {r: r // roles_per_app for r in b.role_ids}
    depts = b.add_nodes(GROUP, 840, 'dept')
    for i, d in enumerate(depts[1:], 1):
        if i <= 27:
            b.add_edge(d, depts[0])               # orphan departments hung on the root
        else:
            lo = max(1, i - 40)
            b.add_edge(d, depts[rng.randint(lo, i - 1)] if rng.random() < 0.9 else depts[0])
    ugroups = b.add_nodes(GROUP, 60, 'usergroup')
    for g in ugroups[1:]:
        if rng.random() < 0.5:
            b.add_edge(g, rng.randint(ugroups[0], g - 1))
    users = b.add_nodes(USER, 4157, 'member')
    leafish = depts[200:]
    for u in users:
        b.add_edge(u, rng.choice(leafish))
        if rng.random() < 0.15:
            b.add_edge(u, rng.choice(ugroups))
    # resources: services (catalog) -> ADS tools/APIs (table) -> columns, row partitions
    b.resources(3, 40, 20, 12)
    for r in b.role_ids:
        a = app_of_role[r]
        app = b.ctx.atom(0, a)
        for _ in range(rng.randint(2, 6)):        # role members: users or groups
            s = rng.choice(users) if rng.random() < 0.4 else (
                rng.choice(depts) if rng.random() < 0.7 else rng.choice(ugroups))
            b.add_edge(s, r)
        for _ in range(rng.randint(4, 12)):       # row grants (application-scoped)
            t = rng.choice(b.tabs)
            tgt = t if rng.random() < 0.3 else t + 1 + 20 + rng.randrange(12)
            b.add_edge(r, tgt, app | b.ctx.rand_cond(rng, 0.1, attrs=[1, 2, 3]), 1)
        for _ in range(rng.randint(0, 6)):        # column grants
            t = rng.choice(b.tabs)
            b.add_edge(r, t + 1 + rng.randrange(20), app, 1)
    for _ in range(40):                           # deny rules (user_group / user)
        s = rng.choice(ugroups + depts) if rng.random() < 0.7 else rng.choice(users)
        b.add_edge(s, b.rand_resource((0.0, 0.7, 0.3, 0.0)), 0, -1)
    init = snapshot(b)
    mix = {'member': 0.50, 'nest': 0.05, 'assign': 0.20, 'grant': 0.17, 'deny': 0.05, 'cond': 0.03}
    ops = b.updates(n_upd, depts + ugroups, users, mix)
    b.write(out, init, ops, {'source': 'enterprise-shaped reconstruction (no real rows)'})


def gen_amzn(out, pkl_path, seed=3, n_upd=10000, theta=0.9, min_group=5):
    """UCI Amazon Access Samples (real, anonymised): persons, org attributes, PERMGROUP
    memberships, HOST / SYSTEM_SUPPORT grants, and the 2005-2010 add/remove history.

    Org tree: person -> manager team (PERSON_MGR_ID) -> DEPTNAME -> ROLLUP_2 -> ROLLUP_1
    (each parent by majority vote of members). Group-level grants are mined: a resource held
    by >= theta of a group's members (>= min_group members) becomes a group grant, and those
    members' direct grants are dropped. Remaining direct grants stay as user grants.
    Updates: the most recent history events, applied validly to the snapshot (add = insert if
    absent, remove = delete if present). No conditions; deny edges are synthetic (1%).
    """
    import pickle, collections
    rng = random.Random(seed)
    D = pickle.load(open(pkl_path, 'rb'))
    P = [p for p in D['persons'] if p[0]['PERSON_ID'] != '0']
    b = Builder(seed)
    def maj(items):
        return collections.Counter(items).most_common(1)[0][0]
    r1 = sorted(set(a['PERSON_ROLLUP_1'] for a, _ in P))
    r2 = sorted(set(a['PERSON_ROLLUP_2'] for a, _ in P))
    dp = sorted(set(a['PERSON_DEPTNAME'] for a, _ in P))
    mg = sorted(set(a['PERSON_MGR_ID'] for a, _ in P))
    pg = sorted(set(x for _, acc in P for x in acc if x.startswith('PERMGROUP_')))
    b.role_ids = []
    n1 = dict(zip(r1, b.add_nodes(GROUP, len(r1), 'rollup1')))
    n2 = dict(zip(r2, b.add_nodes(GROUP, len(r2), 'rollup2')))
    nd = dict(zip(dp, b.add_nodes(GROUP, len(dp), 'dept')))
    nm = dict(zip(mg, b.add_nodes(GROUP, len(mg), 'team')))
    npg = dict(zip(pg, b.add_nodes(GROUP, len(pg), 'permgroup')))
    r2_r1, d_r2, m_d = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
    for a, _ in P:
        r2_r1[a['PERSON_ROLLUP_2']].append(a['PERSON_ROLLUP_1'])
        d_r2[a['PERSON_DEPTNAME']].append(a['PERSON_ROLLUP_2'])
        m_d[a['PERSON_MGR_ID']].append(a['PERSON_DEPTNAME'])
    for k, v in r2_r1.items(): b.add_edge(n2[k], n1[maj(v)])
    for k, v in d_r2.items(): b.add_edge(nd[k], n2[maj(v)])
    for k, v in m_d.items(): b.add_edge(nm[k], nd[maj(v)])
    pids = [a['PERSON_ID'] for a, _ in P]
    nu = dict(zip(pids, b.add_nodes(USER, len(P), 'person')))
    resnames = sorted(set(x for _, acc in P for x in acc if not x.startswith('PERMGROUP_')))
    root = b.add_nodes(RES, 1, 'catalog')[0]
    host_cat = b.add_nodes(RES, 1, 'catalog')[0]; sys_cat = b.add_nodes(RES, 1, 'catalog')[0]
    b.add_edge(root, host_cat); b.add_edge(root, sys_cat)
    nr = {}
    for x in resnames:
        nr[x] = b.add_nodes(RES, 1, 'resource')[0]
        b.add_edge(host_cat if x.startswith('HOST_') else sys_cat, nr[x])
    members = collections.defaultdict(set)     # group node -> user nodes
    holds = collections.defaultdict(set)       # resource name -> user nodes
    for a, acc in P:
        u = nu[a['PERSON_ID']]
        b.add_edge(u, nm[a['PERSON_MGR_ID']]); members[nm[a['PERSON_MGR_ID']]].add(u)
        for x in acc:
            if x.startswith('PERMGROUP_'):
                b.add_edge(u, npg[x]); members[npg[x]].add(u)
            else:
                holds[x].add(u)
    covered = set(); n_gg = 0
    # group candidates per resource via user->groups index
    ugroups = collections.defaultdict(list)
    for g, us in members.items():
        for u in us: ugroups[u].append(g)
    for x, us in holds.items():
        gc = collections.Counter(g for u in us for g in ugroups[u])
        for g, c in gc.items():
            sz = len(members[g])
            if sz >= min_group and c >= theta * sz:
                b.add_edge(g, nr[x]); n_gg += 1
                for u in members[g] & us: covered.add((u, x))
    n_direct = 0
    for x, us in holds.items():
        for u in us:
            if (u, x) not in covered:
                b.add_edge(u, nr[x]); n_direct += 1
    groups = list(n1.values()) + list(n2.values()) + list(nd.values()) + list(nm.values()) + list(npg.values())
    users = list(nu.values())
    allres = list(nr.values())
    for _ in range(max(1, (n_gg + n_direct) // 100)):        # synthetic deny rules (1%)
        s = rng.choice(groups) if rng.random() < 0.7 else rng.choice(users)
        b.add_edge(s, rng.choice(allres), 0, -1)
    # real update stream: the most recent history events. The snapshot is the end state, so we
    # rewind it by undoing the window (in reverse time order), then replay the window forward.
    H = sorted(D['hist'], key=lambda h: h[3])
    pgid = {x.split('_', 1)[1]: x for x in pg}
    hostid = {x.split('_', 1)[1]: x for x in resnames if x.startswith('HOST_')}
    ops = []; skipped = 0
    for act, tgt, login, _, _ in reversed(H):
        if len(ops) >= n_upd: break
        if login not in nu: skipped += 1; continue
        u = nu[login]
        if tgt in pgid: d = npg[pgid[tgt]]
        elif tgt in hostid: d = nr[hostid[tgt]]
        else: skipped += 1; continue
        ops.append((act, u, d))
    for act, u, d in ops:                      # ops is in reverse time order here
        if act == 'add_access': b.edges.pop((u, d), None)
        else: b.edges.setdefault((u, d), (0, 1))
    ops.reverse()
    init = snapshot(b)
    out_ops = []
    for act, u, d in ops:
        if act == 'add_access':
            if (u, d) in b.edges: continue
            b.edges[(u, d)] = (0, 1); out_ops.append((1, u, d, 0, 1))
        else:
            if (u, d) not in b.edges: continue
            m, p = b.edges.pop((u, d)); out_ops.append((-1, u, d, m, p))
    n_rm = sum(1 for o in out_ops if o[0] < 0)
    b.write(out, init, out_ops, {'source': 'UCI Amazon Access Samples (real) + 1% synthetic deny',
                                 'group_grants_mined': n_gg, 'direct_grants': n_direct,
                                 'history_events_used': len(ops), 'effective_updates': len(out_ops),
                                 'effective_removes': n_rm, 'skipped_events': skipped})

def gen_deep(out, n_users, levels, width, parents, seed=5, n_upd=10000):
    """Deep nested groups (Zanzibar 'nested group' pattern): `levels` layers of `width`
    groups, each group a member of `parents` groups one layer up; roles hang on ~5% of groups."""
    b = Builder(seed)
    rng = b.rng
    b.roles(max(100, n_users // 100))
    layers = [b.add_nodes(GROUP, width, f'L{i}') for i in range(levels)]
    for i in range(1, levels):
        for g in layers[i]:
            for _ in range(parents):
                b.add_edge(g, rng.choice(layers[i - 1]))
    groups = [g for l in layers for g in l]
    users = b.add_nodes(USER, n_users, 'user')
    for u in users:
        for _ in range(rng.randint(1, 2)):
            b.add_edge(u, rng.choice(layers[-1]))
    for g in groups:
        if rng.random() < 0.05:
            b.add_edge(g, rng.choice(b.role_ids), b.ctx.rand_cond(rng, 0.2))
    b.resources(10 + n_users // 10000, 30, 15, 4)
    b.grants(per_role=(5, 20), p_cond=0.3, deny_frac=0.08,
             deny_subjects=groups + b.role_ids + users[: max(1, n_users // 100)])
    init = snapshot(b)
    ops = b.updates(n_upd, groups, users)
    b.write(out, init, ops, {'source': f'deep-nesting levels={levels} width={width} parents={parents}'})


if __name__ == '__main__':
    mode, out = sys.argv[1], sys.argv[2]
    if mode == 'syn':
        gen_syn(out, int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 1)
    elif mode == 'snap':
        gen_snap(out, sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 1,
                 int(sys.argv[5]) if len(sys.argv) > 5 else None)
    elif mode == 'deep':
        gen_deep(out, int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]))
    elif mode == 'ent':
        gen_ent(out)
    elif mode == 'amzn':
        gen_amzn(out, sys.argv[3], n_upd=int(sys.argv[4]) if len(sys.argv) > 4 else 10000)
