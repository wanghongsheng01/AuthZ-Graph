#!/usr/bin/env python3
"""Authz datasets whose nested-group layer is a REAL hierarchy (output of parse_real.py).

Groups = categories / ontology terms; nesting = subcategory / is_a / part_of (child -> parent);
users = member items (genes, articles) with their real direct memberships. Cycles are removed by
condensing strongly connected components into one group. Roles, resources, grants, conditions,
deny edges and the update stream come from gen.py's Builder, with the same conventions:
ids roles < groups < users < resources, every subject->subject edge from a higher to a lower id.

usage: gen_real.py <parsed_dir> <out> <label> [seed] [p_role] [max_users|0] [tau]
"""
import collections, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen import Builder, GROUP, USER, snapshot  # noqa: E402


def scc_condense(n, edges):
    """Tarjan (iterative). Returns comp[] and the number of components."""
    adj = [[] for _ in range(n)]
    for c, p in edges:
        adj[c].append(p)
    index, low, comp = [-1] * n, [0] * n, [-1] * n
    onst, st, idx, nc = [False] * n, [], 0, 0
    for s in range(n):
        if index[s] >= 0:
            continue
        work = [(s, 0)]
        index[s] = low[s] = idx; idx += 1; st.append(s); onst[s] = True
        while work:
            v, i = work[-1]
            if i < len(adj[v]):
                work[-1] = (v, i + 1)
                w = adj[v][i]
                if index[w] < 0:
                    index[w] = low[w] = idx; idx += 1; st.append(w); onst[w] = True
                    work.append((w, 0))
                elif onst[w]:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    while True:
                        w = st.pop(); onst[w] = False; comp[w] = nc
                        if w == v:
                            break
                    nc += 1
    return comp, nc


def materialize(types, init, ops, tau):
    """Give every user with >= tau group memberships a personal group g_u: u -> g_u -> (its groups).
    Semantics-preserving (same allow set); emulates materialising the labels of high-degree users.
    Ids: personal groups are inserted just before the users, so all subject edges still go
    from a higher to a lower id. Membership updates of such users are rewritten to g_u -> g."""
    first_user = types.index(USER)
    deg = collections.Counter(s for s, d, m, p in init if types[s] == USER and types[d] == GROUP)
    mat = sorted(u for u, c in deg.items() if c >= tau)
    K = len(mat)
    shift = lambda x: x if x < first_user else x + K
    pg = {u: first_user + i for i, u in enumerate(mat)}
    def edge(s, d):
        if types[s] == USER and types[d] == GROUP and s in pg:
            return pg[s], shift(d)
        return shift(s), shift(d)
    new_types = types[:first_user] + [GROUP] * K + types[first_user:]
    new_init = [edge(s, d) + (m, p) for s, d, m, p in init]
    new_init += [(shift(u), pg[u], 0, 1) for u in mat]
    new_ops = [(o,) + edge(s, d) + (m, p) for o, s, d, m, p in ops]
    return new_types, new_init, new_ops, K


def main(pdir, out, label, seed=1, p_role=0.05, max_users=None, n_upd=10000, tau=None):
    names = {}
    for line in open(f'{pdir}/groups.tsv', encoding='utf-8'):
        g, _, nm = line.rstrip('\n').partition('\t')
        names[g] = nm
    gidx = {g: i for i, g in enumerate(names)}
    raw_nest = []
    for line in open(f'{pdir}/nest.tsv', encoding='utf-8'):
        c, p = line.rstrip('\n').split('\t')
        if c in gidx and p in gidx and c != p:
            raw_nest.append((gidx[c], gidx[p]))
    n0 = len(gidx)
    comp, nc = scc_condense(n0, raw_nest)
    cnest = sorted({(comp[c], comp[p]) for c, p in raw_nest if comp[c] != comp[p]})
    merged = n0 - nc
    # topological order with parents first (Kahn on parent -> child)
    children = collections.defaultdict(list); indeg = [0] * nc
    for c, p in cnest:
        children[p].append(c); indeg[c] += 1
    order = [v for v in range(nc) if indeg[v] == 0]
    for v in order:
        for c in children[v]:
            indeg[c] -= 1
            if indeg[c] == 0:
                order.append(c)
    assert len(order) == nc
    depth = [0] * nc
    parents = collections.defaultdict(list)
    for c, p in cnest:
        parents[c].append(p)
    for v in order:
        if parents[v]:
            depth[v] = 1 + max(depth[p] for p in parents[v])
    members = collections.defaultdict(set)
    frac = float(os.environ.get('AUTHZ_USER_FRAC', '1'))     # deterministic hash sampling of users
    import zlib
    for line in open(f'{pdir}/members.tsv', encoding='utf-8'):
        u, g = line.rstrip('\n').split('\t')
        if frac < 1 and zlib.crc32(u.encode()) % 100000 >= frac * 100000:
            continue
        if g in gidx:
            members[u].add(comp[gidx[g]])
    unames = sorted(members)
    b = Builder(seed)
    rng = b.rng
    if max_users and len(unames) > max_users:
        unames = sorted(rng.sample(unames, max_users))
    b.roles(int(os.environ.get('AUTHZ_N_ROLES', max(200, nc // 20))))   # override for very large hierarchies
    gnode = b.add_nodes(GROUP, nc, 'group')          # gnode[k] for the k-th group in topo order
    pos = {v: k for k, v in enumerate(order)}
    node_of = {v: gnode[pos[v]] for v in range(nc)}
    for c, p in cnest:
        b.add_edge(node_of[c], node_of[p])
    users = b.add_nodes(USER, len(unames), 'user')
    for u, nm in zip(users, unames):
        for g in members[nm]:
            b.add_edge(u, node_of[g])
        if rng.random() < 0.01:
            b.add_edge(u, rng.choice(b.role_ids), b.ctx.rand_cond(rng, 0.2))
    groups = list(gnode)
    for g in groups:
        if rng.random() < p_role:
            for _ in range(rng.randint(1, 2)):
                b.add_edge(g, rng.choice(b.role_ids), b.ctx.rand_cond(rng, 0.2))
    b.resources(20, 40, 15, 4)
    b.grants(per_role=(5, 20), p_cond=0.3, deny_frac=0.08,
             deny_subjects=groups + b.role_ids + users[: max(1, len(users) // 100)])
    init = snapshot(b)
    ops = b.updates(n_upd, groups, users)
    dd = [depth[v] for v in range(nc)]
    K = 0
    if tau:
        b.types, init, ops, K = materialize(b.types, init, ops, tau)
    b.write(out, init, ops, {'source': f'REAL {label} (groups={n0}, scc_merged={merged}) p_role={p_role}'
                             + (f' materialised users deg>={tau}: {K}' if tau else ''),
                             'nest_edges': len(cnest), 'raw_nest_edges': len(raw_nest),
                             'scc_merged_groups': merged, 'depth_max': max(dd),
                             'depth_mean': f'{sum(dd) / len(dd):.2f}',
                             'member_edges': sum(len(members[u]) for u in unames), 'n_roles': len(b.role_ids)})


if __name__ == '__main__':
    a = sys.argv
    main(a[1], a[2], a[3], int(a[4]) if len(a) > 4 else 1, float(a[5]) if len(a) > 5 else 0.05,
         int(a[6]) if len(a) > 6 and int(a[6]) > 0 else None, tau=int(a[7]) if len(a) > 7 else None)
