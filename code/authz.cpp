// AuthZ-Graph pilot: effective-permission index over a dynamic, condition-annotated
// authorization graph with deny edges, plus baselines.
//
// Semantics (deny-overrides, one frontier edge per path):
//   allow(u,r,S) = A and not D
//   A: exists path u ~> f (subject edges) , f -> a (allow edge), a ~> r (contains edges)
//   D: same with a deny edge f -> a
//   every edge on the path must have its condition mask contained in S (S = atoms true in context).
//
// Methods: AZX (frontier 2-hop labels with counting annotations; AZX-NODEG = the same labels with the
// probe/scan test that omits deg+(u), an ablation with an index of its own), BFS (online, no index),
// SPICE-ML / SPICE-FC (check cache with quantised / exact revisions), TC (closure to grant
// targets, affected-row recomputation), PLL (2-hop reachability, no deny/conditions, periodic
// rebuild), LCR (landmark label-constrained reachability index, affected-landmark recomputation).
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <memory>
#include <numeric>
#include <random>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
using namespace std;
typedef uint64_t Mask;
#ifndef AUTHZ_SRC_HASH
#define AUTHZ_SRC_HASH "unknown"
#endif
enum { USER = 0, GROUP = 1, ROLE = 2, RES = 3 };
static inline bool sat(Mask m, Mask S) { return (m & ~S) == 0; }
static double now_us() {
  return chrono::duration<double, micro>(chrono::steady_clock::now().time_since_epoch()).count();
}

// ------------------------------------------------------------------ graph
struct E { int v; Mask m; int8_t pol; };
struct Graph {
  int n = 0;
  vector<int8_t> type;
  vector<vector<E>> out, inU, inN;  // inU: sources that are users; inN: all other sources
  bool subj(int x) const { return type[x] != RES; }
  void add(int s, int d, Mask m, int8_t p) {
    out[s].push_back({d, m, p});
    (type[s] == USER ? inU : inN)[d].push_back({s, m, p});
  }
  static bool erase_from(vector<E>& v, int x) {
    for (size_t i = 0; i < v.size(); i++)
      if (v[i].v == x) { v[i] = v.back(); v.pop_back(); return true; }
    return false;
  }
  void del(int s, int d) {
    bool a = erase_from(out[s], d);
    bool b = erase_from((type[s] == USER ? inU : inN)[d], s);
    if (!a || !b) { fprintf(stderr, "del of missing edge %d->%d\n", s, d); exit(1); }
  }
  size_t m() const { size_t c = 0; for (auto& o : out) c += o.size(); return c; }
};

template <class T> static vector<T> readbin(const string& p) {
  FILE* f = fopen(p.c_str(), "rb");
  if (!f) { fprintf(stderr, "cannot open %s\n", p.c_str()); exit(1); }
  fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
  vector<T> v(sz / sizeof(T));
  if (sz && fread(v.data(), sizeof(T), v.size(), f) != v.size()) exit(1);
  fclose(f);
  return v;
}

// Resource ancestors of r reachable under S, with accumulated contains masks folded in.
// Resource side is a forest (or shallow DAG); we enumerate (ancestor, acc) pairs.
struct AncBuf { vector<pair<int, Mask>> v; };
static void res_ancestors(const Graph& G, int r, Mask S, AncBuf& B) {
  B.v.clear();
  B.v.push_back({r, 0});
  for (size_t i = 0; i < B.v.size(); i++) {
    auto [a, acc] = B.v[i];
    for (auto& e : G.inN[a])
      if (G.type[e.v] == RES && sat(acc | e.m, S)) B.v.push_back({e.v, acc | e.m});
  }
}

// Plain evaluator with switches (used to attribute false allows of condition/deny-blind indexes).
static bool eval_flags(const Graph& G, int u, int r, Mask S, bool useDeny, vector<uint32_t>& mk, uint32_t& ep) {
  ++ep; vector<int> q{u}; mk[u] = ep;
  for (size_t i = 0; i < q.size(); i++)
    for (auto& e : G.out[q[i]])
      if (G.type[e.v] != RES && sat(e.m, S) && mk[e.v] != ep) { mk[e.v] = ep; q.push_back(e.v); }
  AncBuf B; res_ancestors(G, r, S, B); bool allow = false;
  for (auto& [a, acc] : B.v)
    for (int k = 0; k < 2; k++)
      for (auto& e : (k ? G.inU : G.inN)[a]) {
        if (G.type[e.v] == RES || mk[e.v] != ep || !sat(e.m | acc, S)) continue;
        if (e.pol < 0) { if (useDeny) return false; } else allow = true;
      }
  return allow;
}

// ------------------------------------------------------------------ method interface
struct Method {
  Graph& G;
  explicit Method(Graph& g) : G(g) {}
  virtual ~Method() {}
  virtual string name() = 0;
  virtual void build() {}
  virtual void before_delete(int, int, Mask, int8_t) {}
  virtual void after_delete(int, int, Mask, int8_t) {}
  virtual void after_insert(int, int, Mask, int8_t) {}
  virtual void tick() {}  // called once per update after the graph changed (revision++)
  virtual bool check(int u, int r, Mask S, int cid) = 0;
  virtual double bytes() { return 0; }
};

// ------------------------------------------------------------------ BFS (online, no index)
struct BFS : Method {
  vector<uint32_t> mark; uint32_t ep = 0; vector<int> q; AncBuf anc;
  explicit BFS(Graph& g) : Method(g) { mark.assign(g.n, 0); }
  string name() override { return "BFS"; }
  bool check(int u, int r, Mask S, int) override {
    ++ep; q.clear(); q.push_back(u); mark[u] = ep;
    size_t frontier_out = 0;
    for (size_t i = 0; i < q.size(); i++) {
      int x = q[i];
      for (auto& e : G.out[x]) {
        if (!sat(e.m, S)) continue;
        if (G.type[e.v] == RES) { frontier_out++; continue; }
        if (mark[e.v] != ep) { mark[e.v] = ep; q.push_back(e.v); }
      }
    }
    res_ancestors(G, r, S, anc);
    // choose the cheaper side: scan in-frontier edges of r's ancestors, or out-frontier of closure
    size_t in_cost = 0;
    for (auto& [a, acc] : anc.v) in_cost += G.inN[a].size() + G.inU[a].size();
    bool allow = false;
    if (in_cost <= frontier_out) {
      for (auto& [a, acc] : anc.v)
        for (int k = 0; k < 2; k++)
          for (auto& e : (k ? G.inU : G.inN)[a]) {
            if (G.type[e.v] == RES || mark[e.v] != ep || !sat(e.m | acc, S)) continue;
            if (e.pol < 0) return false;
            allow = true;
          }
    } else {
      // small closure: probe ancestors via a hash of (ancestor -> acc)
      for (int x : q)
        for (auto& e : G.out[x]) {
          if (G.type[e.v] != RES || !sat(e.m, S)) continue;
          for (auto& [a, acc] : anc.v)
            if (a == e.v && sat(e.m | acc, S)) { if (e.pol < 0) return false; allow = true; }
        }
    }
    return allow;
  }
  // who-can-access: number of users allowed on r under S
  long who(int r, Mask S) {
    res_ancestors(G, r, S, anc);
    vector<int> allowSrc, denySrc;
    for (auto& [a, acc] : anc.v)
      for (int k = 0; k < 2; k++)
        for (auto& e : (k ? G.inU : G.inN)[a])
          if (G.type[e.v] != RES && sat(e.m | acc, S)) (e.pol < 0 ? denySrc : allowSrc).push_back(e.v);
    auto users_reaching = [&](vector<int>& src, uint32_t tag) {
      ++ep; q.clear();
      for (int s : src) if (mark[s] != ep) { mark[s] = ep; q.push_back(s); }
      vector<int> us;
      for (size_t i = 0; i < q.size(); i++) {
        int x = q[i];
        if (G.type[x] == USER) us.push_back(x);
        for (int k = 0; k < 2; k++)
          for (auto& e : (k ? G.inU : G.inN)[x])
            if (G.type[e.v] != RES && sat(e.m, S) && mark[e.v] != ep) { mark[e.v] = ep; q.push_back(e.v); }
      }
      (void)tag;
      return us;
    };
    vector<int> den = users_reaching(denySrc, 0);
    unordered_set<int> dset(den.begin(), den.end());
    vector<int> al = users_reaching(allowSrc, 1);
    long c = 0;
    for (int x : al) if (!dset.count(x)) c++;
    return c;
  }
};

// expansion of resource subtrees (shared by what-can-access implementations)
static long expand_what(const Graph& G, vector<pair<int, Mask>>& allowRoots, vector<pair<int, Mask>>& denyRoots,
                        Mask S, vector<uint32_t>& mk, uint32_t& ep) {
  // deny subtree marks first, then allowed subtree count excluding denied nodes
  ++ep; uint32_t den = ep; vector<int> st;
  for (auto& [a, m] : denyRoots) if (mk[a] != den) { mk[a] = den; st.push_back(a); }
  while (!st.empty()) {
    int x = st.back(); st.pop_back();
    for (auto& e : G.out[x]) if (sat(e.m, S) && mk[e.v] != den) { mk[e.v] = den; st.push_back(e.v); }
  }
  ++ep; uint32_t al = ep; long c = 0;
  for (auto& [a, m] : allowRoots) if (mk[a] != den && mk[a] != al) { mk[a] = al; st.push_back(a); c++; }
  while (!st.empty()) {
    int x = st.back(); st.pop_back();
    for (auto& e : G.out[x])
      if (sat(e.m, S) && mk[e.v] != den && mk[e.v] != al) { mk[e.v] = al; st.push_back(e.v); c++; }
  }
  return c;
}

// ------------------------------------------------------------------ AZX: frontier 2-hop labels
// Lout(x) for every non-user subject node x: hub f (subject node with >=1 frontier edge) ->
// multiset of path masks with path counts (counting semiring over monomials). Users and
// resources keep no labels; they are resolved with one hop at query time.
// Path-count type. 64-bit by default; -DAUTHZ_WIDE switches to exact 128-bit counts for
// hierarchies whose path counts exceed 2^63 (overflow is detected and aborts in both cases).
#ifdef AUTHZ_WIDE
typedef __int128 Cnt;
#else
typedef int64_t Cnt;
#endif
static inline Cnt cabs(Cnt x) { return x < 0 ? -x : x; }
struct Trip { int f; Mask m; Cnt c; };
struct LEnt { int hub; vector<pair<Mask, Cnt>> ms; };
static void normalize(vector<Trip>& d) {
  sort(d.begin(), d.end(), [](const Trip& a, const Trip& b) { return a.f != b.f ? a.f < b.f : a.m < b.m; });
  size_t w = 0;
  for (size_t i = 0; i < d.size();) {
    Trip t = d[i]; size_t j = i + 1;
    while (j < d.size() && d[j].f == t.f && d[j].m == t.m) {
      if (__builtin_add_overflow(t.c, d[j].c, &t.c)) { fprintf(stderr, "AZX: path-count overflow in delta merge\n"); exit(3); }
      j++;
    }
    if (t.c != 0) d[w++] = t;
    i = j;
  }
  d.resize(w);
}

struct AZX : Method {
  vector<vector<LEnt>> L;
  vector<int> fdeg, ddeg;            // frontier out-degree / deny out-degree per subject node
  vector<vector<int>> rev;           // hub -> nodes whose label contains it (lazy, may be stale)
  vector<uint32_t> mark; uint32_t ep = 0; vector<int> hubs; AncBuf anc;
  vector<uint32_t> amark; vector<int> apos;  // scratch for propagation
  long prop_nodes = 0, prop_trips = 0; bool allHubs = false, degtest = true; Cnt maxcoef = 0;
  double apply_cost_us = getenv("AUTHZ_APPLY_COST_US") ? atof(getenv("AUTHZ_APPLY_COST_US")) : 0;
  explicit AZX(Graph& g, bool all = false, bool deg = true) : Method(g), allHubs(all), degtest(deg) {}
  string name() override { return allHubs ? "CLOSURE" : degtest ? "AZX" : "AZX-NODEG"; }
  bool hub(int x) { return allHubs ? G.type[x] != USER : fdeg[x] > 0; }

  bool is_frontier_edge(int s, int d) { return G.subj(s) && G.type[d] == RES; }

  void build() override {
    int n = G.n;
    L.assign(n, {}); fdeg.assign(n, 0); ddeg.assign(n, 0); rev.assign(n, {});
    mark.assign(n, 0); amark.assign(n, 0); apos.assign(n, -1);
    for (int x = 0; x < n; x++)
      for (auto& e : G.out[x])
        if (is_frontier_edge(x, e.v)) { fdeg[x]++; if (e.pol < 0) ddeg[x]++; }
    // subject->subject edges go from higher to lower id: ascending id is a topological order
    vector<Trip> acc;
    for (int x = 0; x < n; x++) {
      if (G.type[x] == USER || G.type[x] == RES) continue;
      acc.clear();
      if (hub(x)) acc.push_back({x, 0, 1});
      for (auto& e : G.out[x]) {
        if (!G.subj(e.v)) continue;
        for (auto& le : L[e.v]) for (auto& [m, c] : le.ms) acc.push_back({le.hub, m | e.m, c});
      }
      apply(x, acc, true);
    }
  }
  // merge a normalized delta into L[x]
  void apply(int x, vector<Trip>& d, bool need_norm) {
    if (need_norm) normalize(d);
    auto& Lx = L[x];
    for (auto& t : d) {
      auto it = lower_bound(Lx.begin(), Lx.end(), t.f, [](const LEnt& a, int f) { return a.hub < f; });
      if (it == Lx.end() || it->hub != t.f) {
        if (t.c <= 0) { fprintf(stderr, "AZX: negative count on missing hub\n"); exit(1); }
        it = Lx.insert(it, LEnt{t.f, {}});
        rev[t.f].push_back(x);
      }
      auto& ms = it->ms;
      auto jt = lower_bound(ms.begin(), ms.end(), t.m, [](const pair<Mask, Cnt>& a, Mask m) { return a.first < m; });
      if (jt != ms.end() && jt->first == t.m) {
        if (__builtin_add_overflow(jt->second, t.c, &jt->second)) { fprintf(stderr, "AZX: 64-bit path-count overflow\n"); exit(3); }
        if (cabs(jt->second) > maxcoef) maxcoef = cabs(jt->second);
        if (jt->second == 0) ms.erase(jt);
      } else {
        if (t.c <= 0) { fprintf(stderr, "AZX: negative count on missing mask\n"); exit(1); }
        ms.insert(jt, {t.m, t.c});
        if (cabs(t.c) > maxcoef) maxcoef = cabs(t.c);
      }
      if (ms.empty()) Lx.erase(it);
    }
  }
  // propagate delta d at x to all non-user ancestors, ascending id = topological order
  void propagate(int x, vector<Trip> d) {
    if (apply_cost_us > 0) { double t0 = now_us(); while (now_us() - t0 < apply_cost_us) {} }
    if (G.type[x] == USER) return;
    normalize(d);
    if (d.empty()) return;
    ++ep;
    vector<int> A{x}; amark[x] = ep;
    for (size_t i = 0; i < A.size(); i++)
      for (auto& e : G.inN[A[i]])
        if (G.type[e.v] != RES && amark[e.v] != ep) { amark[e.v] = ep; A.push_back(e.v); }
    sort(A.begin(), A.end());
    vector<vector<Trip>> D(A.size());
    for (size_t i = 0; i < A.size(); i++) apos[A[i]] = (int)i;
    D[apos[x]] = std::move(d);
    for (size_t i = 0; i < A.size(); i++) {
      int b = A[i];
      auto& Db = D[i];
      if (i) normalize(Db);
      if (Db.empty()) continue;
      prop_nodes++; prop_trips += Db.size();
      apply(b, Db, false);
      for (auto& e : G.inN[b]) {
        if (G.type[e.v] == RES) continue;
        auto& Da = D[apos[e.v]];
        for (auto& t : Db) Da.push_back({t.f, t.m | e.m, t.c});
      }
      vector<Trip>().swap(Db);
    }
  }
  vector<Trip> shifted_label(int y, Mask phi, Cnt sign) {
    vector<Trip> d;
    for (auto& le : L[y]) for (auto& [m, c] : le.ms) d.push_back({le.hub, m | phi, sign * c});
    return d;
  }
  void after_insert(int s, int d, Mask m, int8_t p) override {
    if (G.type[s] == RES) return;                       // contains edge: no label work
    if (G.type[d] == RES) {                             // frontier edge
      if (p < 0) ddeg[s]++;
      if (fdeg[s]++ == 0 && G.type[s] != USER && !allHubs) propagate(s, {{s, 0, 1}});
      return;
    }
    if (G.type[s] == USER) return;                      // user labels are deferred
    propagate(s, shifted_label(d, m, +1));
  }
  void before_delete(int s, int d, Mask m, int8_t p) override {
    if (G.type[s] == RES) return;
    if (G.type[d] == RES) {
      if (p < 0) ddeg[s]--;
      if (--fdeg[s] == 0 && G.type[s] != USER && !allHubs) propagate(s, {{s, 0, -1}});
      return;
    }
    if (G.type[s] == USER) return;
    propagate(s, shifted_label(d, m, -1));
  }
  // collect hubs reachable from u under S into `hubs` (marked with ep)
  void user_hubs(int u, Mask S) {
    ++ep; hubs.clear();
    for (auto& e : G.out[u]) {
      if (!sat(e.m, S)) continue;
      if (G.type[e.v] == RES) { if (mark[u] != ep) { mark[u] = ep; hubs.push_back(u); } continue; }
      for (auto& le : L[e.v]) {
        if (mark[le.hub] == ep) continue;
        for (auto& [w, c] : le.ms)
          if (sat(w | e.m, S)) { mark[le.hub] = ep; hubs.push_back(le.hub); break; }
      }
    }
  }
  // does u reach hub f under S (via one user edge and a label entry)?
  bool reaches(int u, int f, Mask S) {
    for (auto& e : G.out[u]) {
      if (G.type[e.v] == RES || !sat(e.m, S)) continue;
      auto& Lg = L[e.v];
      auto it = lower_bound(Lg.begin(), Lg.end(), f, [](const LEnt& a, int x) { return a.hub < x; });
      if (it != Lg.end() && it->hub == f)
        for (auto& [w, c] : it->ms) if (sat(w | e.m, S)) return true;
    }
    return false;
  }
  long probes = 0, scans = 0;
  int force_mode = getenv("AUTHZ_FORCE_MODE") ? (string(getenv("AUTHZ_FORCE_MODE")) == "probe" ? 2 : string(getenv("AUTHZ_FORCE_MODE")) == "scan" ? 1 : 0) : 0;
  bool check(int u, int r, Mask S, int) override {
    res_ancestors(G, r, S, anc);
    size_t cin = 0, clab = 0;
    for (auto& [a, acc] : anc.v) cin += G.inN[a].size() + G.inU[a].size();
    for (auto& e : G.out[u]) clab += G.type[e.v] == RES ? 1 : L[e.v].size();
    // probing costs about c_in * deg+(u) label lookups (the probe bound), scanning c_lab;
    // degtest = false is the ablation that omits deg+(u)
    size_t cprobe = degtest ? cin * G.out[u].size() : cin;
    if (force_mode == 2 || (force_mode == 0 && cprobe * 4 < clab)) {   // probe mode: few grant sources on r, large user label
      probes++;
      bool allow = false;
      for (auto& [a, acc] : anc.v)
        for (int k = 0; k < 2; k++)
          for (auto& e : (k ? G.inU : G.inN)[a]) {
            if (G.type[e.v] == RES || !sat(e.m | acc, S)) continue;
            if (e.v == u || reaches(u, e.v, S)) { if (e.pol < 0) return false; allow = true; }
          }
      return allow;
    }
    scans++;
    user_hubs(u, S);
    if (hubs.empty()) return false;
    bool allow = false;
    for (auto& [a, acc] : anc.v) {
      size_t ind = G.inN[a].size() + G.inU[a].size();
      if (ind <= 4 * hubs.size()) {
        for (int k = 0; k < 2; k++)
          for (auto& e : (k ? G.inU : G.inN)[a]) {
            if (G.type[e.v] == RES || mark[e.v] != ep || !sat(e.m | acc, S)) continue;
            if (e.pol < 0) return false;
            allow = true;
          }
      } else {
        for (int f : hubs)
          for (auto& e : G.out[f])
            if (e.v == a && sat(e.m | acc, S)) { if (e.pol < 0) return false; allow = true; }
      }
    }
    return allow;
  }
  long who(int r, Mask S) {
    res_ancestors(G, r, S, anc);
    vector<int> fa, fd;
    for (auto& [a, acc] : anc.v)
      for (int k = 0; k < 2; k++)
        for (auto& e : (k ? G.inU : G.inN)[a])
          if (G.type[e.v] != RES && sat(e.m | acc, S)) (e.pol < 0 ? fd : fa).push_back(e.v);
    auto collect = [&](vector<int>& F) {
      // nodes g with a satisfied path to some f in F; users via one hop
      vector<int> users;
      ++ep; uint32_t seen = ep;
      vector<int> cores;
      for (int f : F) {
        if (G.type[f] == USER) { users.push_back(f); continue; }
        auto& R = rev[f]; size_t k = 0; ++ep; uint32_t dup = ep;
        for (size_t i = 0; i < R.size(); i++) {
          int g = R[i];
          if (mark[g] == dup) continue;
          auto it = lower_bound(L[g].begin(), L[g].end(), f, [](const LEnt& a, int x) { return a.hub < x; });
          if (it == L[g].end() || it->hub != f) continue;        // stale: drop
          mark[g] = dup; R[k++] = g;
          if (amark[g] == seen) continue;
          for (auto& [w, c] : it->ms) if (sat(w, S)) { amark[g] = seen; cores.push_back(g); break; }
        }
        R.resize(k);
      }
      for (int g : cores) for (auto& e : G.inU[g]) if (sat(e.m, S)) users.push_back(e.v);
      sort(users.begin(), users.end());
      users.erase(unique(users.begin(), users.end()), users.end());
      return users;
    };
    vector<int> den = collect(fd), al = collect(fa);
    long c = 0;
    for (int x : al) if (!binary_search(den.begin(), den.end(), x)) c++;
    return c;
  }
  long what(int u, Mask S, vector<uint32_t>& mk, uint32_t& mep) {
    user_hubs(u, S);
    vector<pair<int, Mask>> ar, dr;
    for (int f : hubs)
      for (auto& e : G.out[f]) if (G.type[e.v] == RES && sat(e.m, S)) (e.pol < 0 ? dr : ar).push_back({e.v, 0});
    return expand_what(G, ar, dr, S, mk, mep);
  }
  double bytes() override {
    double b = 0;
    for (auto& l : L) { b += sizeof(vector<LEnt>) + l.capacity() * sizeof(LEnt); for (auto& e : l) b += e.ms.capacity() * sizeof(pair<Mask, Cnt>); }
    for (auto& r : rev) b += sizeof(vector<int>) + r.capacity() * 4;
    b += 8.0 * G.n;
    return b;
  }
  size_t entries() { size_t c = 0; for (auto& l : L) c += l.size(); return c; }
};

// ------------------------------------------------------------------ SpiceDB-like check cache
struct Spice : Method {
  BFS inner; bool exact; long W; long revn = 0; unordered_map<uint64_t, pair<uint8_t, long>> cache;
  long hits = 0, misses = 0;
  Spice(Graph& g, bool exact_, long W_) : Method(g), inner(g), exact(exact_), W(W_) {}
  string name() override { return exact ? "SPICE-FC" : "SPICE-ML" + to_string(W); }
  void tick() override { revn++; }
  bool check(int u, int r, Mask S, int cid) override {
    long q = exact ? revn : revn / W;
    uint64_t key = ((uint64_t)u << 36) | ((uint64_t)r << 10) | (uint64_t)cid;
    auto it = cache.find(key);
    if (it != cache.end() && it->second.second == q) { hits++; return it->second.first; }
    misses++;
    bool a = inner.check(u, r, S, cid);
    if (cache.size() > 2000000) cache.clear();
    cache[key] = {(uint8_t)a, q};
    return a;
  }
  double bytes() override { return cache.size() * 48.0; }
};

// ------------------------------------------------------------------ TC: closure to grant targets
// row(u): sorted (target resource, polarity) -> antichain of minimal masks. Affected rows
// (users that reach the tail of an updated subject/frontier edge) are recomputed after the update.
struct TEnt { int a; int8_t pol; vector<Mask> anti; };
static bool anti_insert(vector<Mask>& A, Mask w) {
  for (Mask m : A) if ((m & ~w) == 0) return false;   // dominated
  size_t k = 0;
  for (size_t i = 0; i < A.size(); i++) if (!((w & ~A[i]) == 0)) A[k++] = A[i];
  A.resize(k); A.push_back(w);
  return true;
}
struct TC : Method {
  vector<vector<TEnt>> row; vector<int> users; AncBuf anc;
  unordered_map<int, vector<Mask>> lab; vector<pair<int, Mask>> wl;
  vector<uint32_t> mark; uint32_t ep = 0; long recomputed = 0;
  explicit TC(Graph& g) : Method(g) {}
  string name() override { return "TC"; }
  void compute(int u) {
    lab.clear(); wl.clear();
    lab[u].push_back(0); wl.push_back({u, 0});
    map<pair<int, int8_t>, vector<Mask>> acc;
    for (size_t i = 0; i < wl.size(); i++) {
      auto [x, w] = wl[i];
      for (auto& e : G.out[x]) {
        Mask w2 = w | e.m;
        if (G.type[e.v] == RES) { anti_insert(acc[{e.v, e.pol}], w2); continue; }
        if (anti_insert(lab[e.v], w2)) wl.push_back({e.v, w2});
      }
    }
    auto& R = row[u]; R.clear();
    for (auto& [k, v] : acc) R.push_back({k.first, k.second, v});
  }
  void build() override {
    row.assign(G.n, {}); mark.assign(G.n, 0);
    for (int x = 0; x < G.n; x++) if (G.type[x] == USER) compute(x);
  }
  void affected(int s) {
    if (G.type[s] == RES) return;
    if (G.type[s] == USER) { compute(s); recomputed++; return; }
    ++ep; vector<int> q{s}; mark[s] = ep;
    for (size_t i = 0; i < q.size(); i++) {
      int x = q[i];
      for (int k = 0; k < 2; k++)
        for (auto& e : (k ? G.inU : G.inN)[x])
          if (G.type[e.v] != RES && mark[e.v] != ep) { mark[e.v] = ep; q.push_back(e.v); }
    }
    for (int x : q) if (G.type[x] == USER) { compute(x); recomputed++; }
  }
  void after_insert(int s, int, Mask, int8_t) override { affected(s); }
  void after_delete(int s, int, Mask, int8_t) override { affected(s); }
  bool check(int u, int r, Mask S, int) override {
    res_ancestors(G, r, S, anc);
    auto& R = row[u]; bool allow = false;
    for (auto& [a, acc] : anc.v) {
      auto it = lower_bound(R.begin(), R.end(), a, [](const TEnt& t, int x) { return t.a < x; });
      for (; it != R.end() && it->a == a; ++it)
        for (Mask w : it->anti) if (sat(w | acc, S)) { if (it->pol < 0) return false; allow = true; break; }
    }
    return allow;
  }
  double bytes() override {
    double b = 0;
    for (auto& R : row) { b += sizeof(R) + R.capacity() * sizeof(TEnt); for (auto& t : R) b += t.anti.capacity() * 8; }
    return b;
  }
};

// ------------------------------------------------------------------ PLL: 2-hop reachability, no deny
// Pruned landmark labeling for plain reachability over allow edges (conditions and deny ignored),
// rebuilt from scratch every `B` updates (static index).
struct PLL : Method {
  vector<vector<int>> Lin, Lout; vector<int> rank_of; long B; long since = 0; double build_ms = 0;
  vector<uint32_t> mark; uint32_t ep = 0; long rebuilds = 0;
  PLL(Graph& g, long B_) : Method(g), B(B_) {}
  string name() override { return "PLL-noDeny"; }
  static bool inter(const vector<int>& a, const vector<int>& b) {
    size_t i = 0, j = 0;
    while (i < a.size() && j < b.size()) { if (a[i] == b[j]) return true; a[i] < b[j] ? i++ : j++; }
    return false;
  }
  void build() override {
    double t0 = now_us();
    int n = G.n;
    vector<int> ord(n); iota(ord.begin(), ord.end(), 0);
    vector<size_t> deg(n);
    for (int x = 0; x < n; x++) deg[x] = G.out[x].size() + G.inN[x].size() + G.inU[x].size();
    sort(ord.begin(), ord.end(), [&](int a, int b) { return deg[a] > deg[b]; });
    Lin.assign(n, {}); Lout.assign(n, {}); mark.assign(n, 0);
    vector<int> q;
    for (int k = 0; k < n; k++) {
      int v = ord[k];
      // forward
      ++ep; q.clear(); q.push_back(v); mark[v] = ep;
      for (size_t i = 0; i < q.size(); i++) {
        int w = q[i];
        if (w != v && inter(Lout[v], Lin[w])) continue;
        Lin[w].push_back(k);
        for (auto& e : G.out[w]) if (e.pol > 0 && mark[e.v] != ep) { mark[e.v] = ep; q.push_back(e.v); }
      }
      ++ep; q.clear(); q.push_back(v); mark[v] = ep;
      for (size_t i = 0; i < q.size(); i++) {
        int w = q[i];
        if (w != v && inter(Lout[w], Lin[v])) continue;
        if (w != v) Lout[w].push_back(k); else Lout[w].push_back(k);
        for (int s = 0; s < 2; s++)
          for (auto& e : (s ? G.inU : G.inN)[w]) if (e.pol > 0 && mark[e.v] != ep) { mark[e.v] = ep; q.push_back(e.v); }
      }
    }
    build_ms = (now_us() - t0) / 1000.0;
    rebuilds++;
  }
  void tick() override { if (++since >= B) { since = 0; build(); } }
  bool check(int u, int r, Mask, int) override { return inter(Lout[u], Lin[r]); }
  double bytes() override {
    double b = 0;
    for (int x = 0; x < G.n; x++) b += 48 + 4.0 * (Lin[x].capacity() + Lout[x].capacity());
    return b;
  }
};

// ------------------------------------------------------------------ LCR: landmark index
// Landmark label-constrained reachability (after Valstar, Fletcher & Yoshida, SIGMOD'17;
// re-implemented): for each landmark l, the antichain of minimal label sets (masks) to every
// reachable resource state (resource, polarity). A query runs a constrained BFS from u that stops
// at landmarks and consults their index. Updates recompute the landmarks that reach the tail.
struct LCR : Method {
  int K; vector<int> lms; vector<int> lm_id;
  vector<unordered_map<int64_t, vector<Mask>>> idx;
  vector<uint32_t> mark; uint32_t ep = 0; vector<int> q; AncBuf anc; long relabels = 0;
  LCR(Graph& g, int K_) : Method(g), K(K_) {}
  string name() override { return "LCR-LI"; }
  static int64_t st(int a, int8_t p) { return (int64_t)a * 2 + (p < 0); }
  void index_landmark(int li) {
    int l = lms[li]; auto& M = idx[li]; M.clear();
    unordered_map<int, vector<Mask>> lab; vector<pair<int, Mask>> wl{{l, 0}}; lab[l].push_back(0);
    vector<pair<int64_t, Mask>> rq;
    for (size_t i = 0; i < wl.size(); i++) {
      auto [x, w] = wl[i];
      for (auto& e : G.out[x]) {
        Mask w2 = w | e.m;
        if (G.type[e.v] == RES) { if (anti_insert(M[st(e.v, e.pol)], w2)) rq.push_back({st(e.v, e.pol), w2}); continue; }
        if (anti_insert(lab[e.v], w2)) wl.push_back({e.v, w2});
      }
    }
    for (size_t i = 0; i < rq.size(); i++) {      // propagate down the resource hierarchy
      auto [s, w] = rq[i]; int a = (int)(s / 2); int8_t p = (s & 1) ? -1 : 1;
      for (auto& e : G.out[a])
        if (G.type[e.v] == RES && anti_insert(M[st(e.v, p)], w | e.m)) rq.push_back({st(e.v, p), w | e.m});
    }
  }
  void build() override {
    int n = G.n; vector<int> cand;
    for (int x = 0; x < n; x++) if (G.type[x] == GROUP || G.type[x] == ROLE) cand.push_back(x);
    sort(cand.begin(), cand.end(), [&](int a, int b) {
      return G.out[a].size() + G.inN[a].size() + G.inU[a].size() > G.out[b].size() + G.inN[b].size() + G.inU[b].size(); });
    if ((int)cand.size() > K) cand.resize(K);
    lms = cand; lm_id.assign(n, -1); idx.assign(lms.size(), {}); mark.assign(n, 0);
    for (size_t i = 0; i < lms.size(); i++) lm_id[lms[i]] = (int)i;
    for (size_t i = 0; i < lms.size(); i++) index_landmark((int)i);
  }
  void refresh(int s, int d) {
    if (G.type[s] == RES) {               // contains edge: landmarks whose index holds s
      for (size_t i = 0; i < lms.size(); i++)
        if (idx[i].count(st(s, 1)) || idx[i].count(st(s, -1)) || idx[i].count(st(d, 1)) || idx[i].count(st(d, -1))) {
          index_landmark((int)i); relabels++;
        }
      return;
    }
    ++ep; q.clear(); q.push_back(s); mark[s] = ep;
    for (size_t i = 0; i < q.size(); i++)
      for (auto& e : G.inN[q[i]]) if (G.type[e.v] != RES && mark[e.v] != ep) { mark[e.v] = ep; q.push_back(e.v); }
    for (int x : q) if (lm_id[x] >= 0) { index_landmark(lm_id[x]); relabels++; }
  }
  void after_insert(int s, int d, Mask, int8_t) override { refresh(s, d); }
  void after_delete(int s, int d, Mask, int8_t) override { refresh(s, d); }
  bool check(int u, int r, Mask S, int) override {
    res_ancestors(G, r, S, anc);
    bool allow = false;
    ++ep; q.clear(); q.push_back(u); mark[u] = ep;
    for (size_t i = 0; i < q.size(); i++) {
      int x = q[i];
      int li = lm_id[x];
      if (li >= 0) {
        for (int8_t p : {(int8_t)-1, (int8_t)1}) {
          auto it = idx[li].find(st(r, p));
          if (it == idx[li].end()) continue;
          for (Mask w : it->second) if (sat(w, S)) { if (p < 0) return false; allow = true; break; }
        }
        continue;                          // landmark index covers everything below l
      }
      for (auto& e : G.out[x]) {
        if (!sat(e.m, S)) continue;
        if (G.type[e.v] == RES) {
          for (auto& [a, acc] : anc.v) if (a == e.v && sat(e.m | acc, S)) { if (e.pol < 0) return false; allow = true; }
          continue;
        }
        if (mark[e.v] != ep) { mark[e.v] = ep; q.push_back(e.v); }
      }
    }
    return allow;
  }
  double bytes() override {
    double b = 0;
    for (auto& M : idx) { b += M.size() * 64.0; for (auto& [k, v] : M) b += v.capacity() * 8; }
    return b;
  }
};

// ------------------------------------------------------------------ deferred maintenance
// AZX on a private graph view G_I. "Deferrable" updates are buffered and applied in batches;
// all others are applied before the write is acknowledged.
//   NAIVE: defer every insertion except deny insertions, and every deny deletion.
//   SAFE : defer only allow-edge insertions, deny-edge deletions, and subject-edge insertions
//          whose target cannot reach any deny edge; flush the buffer before any synchronous
//          update that can create new deny reachability (deny insertion, subject insertion).
struct Deferred : Method {
  Graph GI; AZX inner; bool safe; long B;
  struct Op { int8_t op; int s, d; Mask m; int8_t p; };
  unordered_map<uint64_t, Op> pend;
  long ndef = 0, nsync = 0, nflush = 0, ncancel = 0; double sync_us = 0, flush_us = 0;
  Deferred(Graph& g, bool safe_, long B_) : Method(g), GI(g), inner(GI), safe(safe_), B(getenv("AUTHZ_DEFER_B") ? atol(getenv("AUTHZ_DEFER_B")) : B_) {}
  string name() override { return safe ? "AZX-DEFER-SAFE" : "AZX-DEFER-NAIVE"; }
  void build() override { GI = G; inner.build(); }
  void applyI(const Op& o) {
    if (o.op > 0) { GI.add(o.s, o.d, o.m, o.p); inner.after_insert(o.s, o.d, o.m, o.p); }
    else { inner.before_delete(o.s, o.d, o.m, o.p); GI.del(o.s, o.d); }
  }
  // Threshold flush (buffer full): may run in the background when AUTHZ_BG_FLUSH=1, so its time is
  // excluded from the acknowledgment latency of the triggering write.
  void flush() { double t0 = now_us(); for (auto& [k, o] : pend) applyI(o); pend.clear(); nflush++; double dt = now_us() - t0; flush_us += dt; last_flush_us += dt; }
  // Pre-restrictive flush required by Theorem 3 before acknowledging a write that may create deny
  // reachability: only buffered subject-edge insertions need to be applied (targeted flush); it is
  // always on the acknowledgment path and is never counted as background work.
  long ntargeted = 0, ntargeted_ops = 0;
  void flush_subject_inserts() {
    double t0 = now_us(); long k = 0;
    for (auto it = pend.begin(); it != pend.end();) {
      const Op& o = it->second;
      if (o.op > 0 && G.type[o.s] != RES && G.type[o.d] != RES) { applyI(o); it = pend.erase(it); k++; } else ++it;
    }
    if (k) { ntargeted++; ntargeted_ops += k; }
    flush_us += now_us() - t0;
  }
  double last_flush_us = 0; bool bg = getenv("AUTHZ_BG_FLUSH") && atoi(getenv("AUTHZ_BG_FLUSH"));
  bool deny_reach(int y) {
    if (inner.ddeg[y] > 0) return true;
    for (auto& le : inner.L[y]) if (inner.ddeg[le.hub] > 0) return true;
    return false;
  }
  void on(Op o) {
    uint64_t k = ((uint64_t)o.s << 32) | (uint32_t)o.d;
    auto it = pend.find(k);
    if (it != pend.end()) {
      Op q = it->second; pend.erase(it); ncancel++;
      if (q.op > 0 && o.op < 0) return;                     // pending insert cancelled by delete
      if (q.op < 0 && o.op > 0) {                           // pending delete, edge still in G_I
        if (q.m == o.m && q.p == o.p) return;
        Op del{-1, q.s, q.d, q.m, q.p}; applyI(del);
      }
    }
    bool frontier = G.type[o.s] != RES && G.type[o.d] == RES;
    bool subject = G.type[o.s] != RES && G.type[o.d] != RES;
    bool defer;
    if (!safe) defer = (o.op > 0 && !(frontier && o.p < 0) && G.type[o.s] != RES) || (o.op < 0 && frontier && o.p < 0);
    else defer = (o.op > 0 && frontier && o.p > 0) || (o.op < 0 && frontier && o.p < 0) ||
                 (o.op > 0 && subject && !deny_reach(o.d));
    if (defer) { pend[k] = o; ndef++; if ((long)pend.size() >= B) flush(); return; }
    double t0 = now_us();
    if (safe && o.op > 0 && (subject || (frontier && o.p < 0)) && !pend.empty()) flush_subject_inserts();
    applyI(o); nsync++; sync_us += now_us() - t0;
  }
  void after_insert(int s, int d, Mask m, int8_t p) override { on({1, s, d, m, p}); }
  void before_delete(int s, int d, Mask m, int8_t p) override { on({-1, s, d, m, p}); }
  bool check(int u, int r, Mask S, int c) override { return inner.check(u, r, S, c); }
  double bytes() override { return inner.bytes(); }
};

// ------------------------------------------------------------------ SV-DTC (support-vertex DTC)
// Re-implementation following the published description of a support-vertex dynamic
// transitive-closure index for Zanzibar-style graphs (ICACT 2026), after Hanauer, Henzinger &
// Schulz (SEA 2020): plain reachability over allow edges (conditions and deny are not modelled),
// k random support vertices with forward/backward reachability sets, observations O1-O3, BFS
// fallback, and a write queue applied every A writes (index recomputed at each application).
struct SVDTC : Method {
  Graph GI; int K; long A; vector<int> sv; vector<vector<uint8_t>> Rp, Rm;
  struct Op { int8_t op; int s, d; Mask m; int8_t p; };
  vector<Op> queue; mt19937_64 rng{7}; vector<uint32_t> mark; uint32_t ep = 0; vector<int> q;
  long fallbacks = 0, decided = 0, recomputes = 0; bool sel_degree; AncBuf anc;
  SVDTC(Graph& g, int K_, long A_, bool deg) : Method(g), GI(g), K(K_), A(A_), sel_degree(deg) {}
  string name() override { return sel_degree ? "SV-DTC-deg" + to_string(K) : "SV-DTC"; }
  void bfs(int src, bool fwd, vector<uint8_t>& R) {
    R.assign(GI.n, 0); q.clear(); q.push_back(src); R[src] = 1;
    for (size_t i = 0; i < q.size(); i++) {
      int x = q[i];
      if (fwd) { for (auto& e : GI.out[x]) if (e.pol > 0 && !R[e.v]) { R[e.v] = 1; q.push_back(e.v); } }
      else for (int k = 0; k < 2; k++) for (auto& e : (k ? GI.inU : GI.inN)[x]) if (e.pol > 0 && !R[e.v]) { R[e.v] = 1; q.push_back(e.v); }
    }
  }
  void recompute() {
    sv.clear(); Rp.assign(K, {}); Rm.assign(K, {});
    if (sel_degree) {          // variant: the K highest-degree groups/roles as support vertices
      vector<int> cand;
      for (int x = 0; x < GI.n; x++) if (GI.type[x] == GROUP || GI.type[x] == ROLE) cand.push_back(x);
      auto deg = [&](int x) { return GI.out[x].size() + GI.inN[x].size() + GI.inU[x].size(); };
      size_t k = min((size_t)K, cand.size());
      partial_sort(cand.begin(), cand.begin() + k, cand.end(), [&](int a, int b) { return deg(a) > deg(b); });
      sv.assign(cand.begin(), cand.begin() + k); K = (int)k;
      Rp.assign(K, {}); Rm.assign(K, {});
    } else for (int i = 0; i < K; i++) sv.push_back((int)(rng() % GI.n));
    for (int i = 0; i < K; i++) { bfs(sv[i], true, Rp[i]); bfs(sv[i], false, Rm[i]); }
    recomputes++;
  }
  void build() override { GI = G; mark.assign(G.n, 0); recompute(); }
  void flush() {
    for (auto& o : queue) { if (o.op > 0) GI.add(o.s, o.d, o.m, o.p); else GI.del(o.s, o.d); }
    queue.clear(); recompute();
  }
  void push(Op o) {
    for (size_t i = 0; i < queue.size(); i++)          // insert-then-delete of the same edge cancels
      if (queue[i].s == o.s && queue[i].d == o.d && queue[i].op != o.op) { queue.erase(queue.begin() + i); return; }
    queue.push_back(o); if ((long)queue.size() >= A) flush();
  }
  void after_insert(int s, int d, Mask m, int8_t p) override { push({1, s, d, m, p}); }
  void before_delete(int s, int d, Mask m, int8_t p) override { push({-1, s, d, m, p}); }
  bool check(int u, int r, Mask, int) override {
    for (int i = 0; i < K; i++) {
      if (sv[i] == u) { decided++; return Rp[i][r]; }
      if (sv[i] == r) { decided++; return Rm[i][u]; }
    }
    for (int i = 0; i < K; i++) {
      if (Rm[i][u] && Rp[i][r]) { decided++; return true; }      // O1
      if (Rp[i][u] && !Rp[i][r]) { decided++; return false; }    // O2
      if (Rm[i][r] && !Rm[i][u]) { decided++; return false; }    // O3
    }
    fallbacks++;
    // fallback BFS over subject vertices; a frontier edge into any containment ancestor of r
    // (conditions ignored, as in the original) witnesses reachability
    res_ancestors(GI, r, ~0ULL, anc);
    ++ep; for (auto& [a, acc] : anc.v) mark[a] = ep;
    uint32_t tgt = ep;
    ++ep; q.clear(); q.push_back(u); mark[u] = ep;
    for (size_t i = 0; i < q.size(); i++)
      for (auto& e : GI.out[q[i]]) {
        if (e.pol < 0) continue;
        if (GI.type[e.v] == RES) { if (mark[e.v] == tgt) return true; continue; }
        if (mark[e.v] == ep || mark[e.v] == tgt) continue;
        mark[e.v] = ep; q.push_back(e.v);
      }
    return false;
  }
  double bytes() override { return 2.0 * K * G.n; }
};

// ------------------------------------------------------------------ driver
struct Stat {
  vector<double> q, u, ack; long fa = 0, fd = 0, nq = 0, fa_t = 0, fa_stale = 0, fa_deny = 0, fa_cond = 0; double build_ms = 0;
};
static double pct(vector<double> v, double p) {
  if (v.empty()) return 0; size_t k = min(v.size() - 1, (size_t)(p * (v.size() - 1)));
  nth_element(v.begin(), v.begin() + k, v.end()); return v[k];
}
static double mean(const vector<double>& v) { double s = 0; for (double x : v) s += x; return v.empty() ? 0 : s / v.size(); }

int main(int argc, char** argv) {
  if (argc < 3) { fprintf(stderr, "usage: authz <datadir> <methods,comma> [qpu=10] [nupd=all] [seed=1] [W=100] [B=1000] [K=64]\n"); return 1; }
  string dir = argv[1], ms = argv[2];
  int qpu = argc > 3 ? atoi(argv[3]) : 10;
  long nupd_lim = argc > 4 ? atol(argv[4]) : -1;
  int seed = argc > 5 ? atoi(argv[5]) : 1;
  long W = argc > 6 ? atol(argv[6]) : 100;
  long Bp = argc > 7 ? atol(argv[7]) : 1000;
  int K = argc > 8 ? atoi(argv[8]) : 64;
  Graph G;
  G.type = readbin<int8_t>(dir + "/types.bin"); G.n = (int)G.type.size();
  G.out.assign(G.n, {}); G.inU.assign(G.n, {}); G.inN.assign(G.n, {});
  auto es = readbin<int32_t>(dir + "/e_src.bin"), ed = readbin<int32_t>(dir + "/e_dst.bin");
  auto em = readbin<uint64_t>(dir + "/e_mask.bin"); auto ep = readbin<int8_t>(dir + "/e_pol.bin");
  for (size_t i = 0; i < es.size(); i++) G.add(es[i], ed[i], em[i], ep[i]);
  auto uo = readbin<int8_t>(dir + "/u_op.bin"); auto us = readbin<int32_t>(dir + "/u_src.bin");
  auto ud = readbin<int32_t>(dir + "/u_dst.bin"); auto um = readbin<uint64_t>(dir + "/u_mask.bin");
  auto up = readbin<int8_t>(dir + "/u_pol.bin");
  long NU = (long)uo.size(); if (nupd_lim >= 0 && nupd_lim < NU) NU = nupd_lim;
  // context schema
  vector<int> dom;
  { FILE* f = fopen((dir + "/meta.txt").c_str(), "r"); char line[512];
    while (fgets(line, sizeof line, f)) if (!strncmp(line, "ctx ", 4)) { char* p = line + 4; int v; int off;
        while (sscanf(p, "%d%n", &v, &off) == 1) { dom.push_back(v); p += off; } }
    fclose(f); }
  mt19937_64 rng(seed);
  const int NC = 512; vector<Mask> ctx(NC);
  for (int c = 0; c < NC; c++) { Mask S = 0; int off = 0; for (int d : dom) { S |= 1ULL << (off + rng() % d); off += d; } ctx[c] = S; }
  vector<int> usersV, resV;
  for (int x = 0; x < G.n; x++) { if (G.type[x] == USER) usersV.push_back(x); else if (G.type[x] == RES) resV.push_back(x); }
  // query pool: half "related" (random walk u -> frontier -> descend), half uniform
  auto rand_triple = [&](int& u, int& r, int& c) {
    u = usersV[rng() % usersV.size()]; c = rng() % NC; r = resV[rng() % resV.size()];
    if (rng() % 2) {
      int x = u;
      for (int step = 0; step < 12; step++) {
        auto& o = G.out[x]; if (o.empty()) break;
        auto& e = o[rng() % o.size()];
        if (G.type[e.v] == RES) {
          int a = e.v;
          for (int k = rng() % 3; k > 0; k--) { auto& oo = G.out[a]; if (oo.empty()) break; a = oo[rng() % oo.size()].v; }
          r = a; break;
        }
        x = e.v;
      }
    }
  };
  const int P = 5000; vector<array<int, 3>> pool(P);
  for (auto& t : pool) rand_triple(t[0], t[1], t[2]);
  vector<double> zc(P); { double s = 0; for (int i = 0; i < P; i++) { s += 1.0 / (i + 1); zc[i] = s; } for (auto& z : zc) z /= s; }
  // methods
  vector<unique_ptr<Method>> M; BFS* bfs = nullptr; AZX* azx = nullptr;
  {
    size_t p = 0; string s = ms + ",";
    while ((p = s.find(',')) != string::npos) {
      string t = s.substr(0, p); s.erase(0, p + 1);
      if (t == "AZX") { M.emplace_back(new AZX(G)); azx = (AZX*)M.back().get(); }
      else if (t == "AZX-NODEG") M.emplace_back(new AZX(G, false, false));
      else if (t == "CLOSURE") M.emplace_back(new AZX(G, true));
      else if (t == "BFS") { M.emplace_back(new BFS(G)); bfs = (BFS*)M.back().get(); }
      else if (t == "SPICE-ML") M.emplace_back(new Spice(G, false, W));
      else if (t == "SPICE-FC") M.emplace_back(new Spice(G, true, W));
      else if (t == "TC") M.emplace_back(new TC(G));
      else if (t == "PLL") M.emplace_back(new PLL(G, Bp));
      else if (t == "LCR") M.emplace_back(new LCR(G, K));
      else if (t == "DEFER-NAIVE") M.emplace_back(new Deferred(G, false, Bp));
      else if (t == "DEFER-SAFE") M.emplace_back(new Deferred(G, true, Bp));
      else if (t == "SV-DTC") M.emplace_back(new SVDTC(G, 5, getenv("AUTHZ_SV_A") ? atol(getenv("AUTHZ_SV_A")) : 64, false));
      else if (t == "SV-DTC-deg5") M.emplace_back(new SVDTC(G, 5, getenv("AUTHZ_SV_A") ? atol(getenv("AUTHZ_SV_A")) : 64, true));
      else if (t == "SV-DTC-deg64") M.emplace_back(new SVDTC(G, 64, getenv("AUTHZ_SV_A") ? atol(getenv("AUTHZ_SV_A")) : 64, true));
      else if (!t.empty()) { fprintf(stderr, "unknown method %s\n", t.c_str()); return 1; }
    }
  }
  BFS oracle(G);
  { // structural statistics: unconditional subject closure of users, and frontier hubs reached
    long sc = 0, sh = 0, mx = 0; int ns = 1000; vector<uint32_t> mk(G.n, 0); uint32_t e2 = 0;
    for (int k = 0; k < ns; k++) {
      int u = usersV[rng() % usersV.size()]; ++e2; vector<int> q{u}; mk[u] = e2; long h = 0;
      for (size_t i = 0; i < q.size(); i++) { bool fr = false;
        for (auto& e : G.out[q[i]]) { if (G.type[e.v] == RES) { fr = true; continue; } if (mk[e.v] != e2) { mk[e.v] = e2; q.push_back(e.v); } }
        h += fr; }
      sc += q.size(); sh += h; mx = max(mx, (long)q.size());
    }
    printf("# closure_mean=%.1f closure_max=%ld hubs_mean=%.1f (sample %d users)\n", (double)sc / ns, mx, (double)sh / ns, ns);
  }
  vector<Stat> st(M.size());
  for (size_t i = 0; i < M.size(); i++) { double t0 = now_us(); M[i]->build(); st[i].build_ms = (now_us() - t0) / 1000; }
  printf("# build=%s defer_B=%s sv_A=%s apply_cost_us=%s bg_flush=%s timing=batch probe_test=deg\n", AUTHZ_SRC_HASH,
         getenv("AUTHZ_DEFER_B") ? getenv("AUTHZ_DEFER_B") : "default(B)", getenv("AUTHZ_SV_A") ? getenv("AUTHZ_SV_A") : "64",
         getenv("AUTHZ_APPLY_COST_US") ? getenv("AUTHZ_APPLY_COST_US") : "0", getenv("AUTHZ_BG_FLUSH") ? getenv("AUTHZ_BG_FLUSH") : "0");
  printf("# data=%s n=%d m=%zu users=%zu res=%zu updates=%ld qpu=%d W=%ld B=%ld K=%d\n", dir.c_str(), G.n, G.m(),
         usersV.size(), resV.size(), NU, qpu, W, Bp, K);
  long truth_allow = 0, nq = 0;
  int ntarget = getenv("AUTHZ_TARGET") ? atoi(getenv("AUTHZ_TARGET")) : 2;
  unordered_map<int, pair<int, int>> lastq;   // user -> last checked (resource, context)
  FILE* dumpf = getenv("AUTHZ_DUMP") ? fopen(getenv("AUTHZ_DUMP"), "w") : nullptr;
  vector<uint32_t> emk(G.n, 0); uint32_t eep = 0;
  uniform_real_distribution<double> U01(0, 1);
  for (long t = 0; t < NU; t++) {
    int s = us[t], d = ud[t]; Mask m = um[t]; int8_t p = up[t];
    if (uo[t] > 0) {
      G.add(s, d, m, p);
      for (size_t i = 0; i < M.size(); i++) {
        auto* dz = dynamic_cast<Deferred*>(M[i].get()); if (dz) dz->last_flush_us = 0;
        double t0 = now_us(); M[i]->after_insert(s, d, m, p); M[i]->tick(); double dt = now_us() - t0;
        st[i].u.push_back(dt); st[i].ack.push_back(dz && dz->bg ? dt - dz->last_flush_us : dt);
      }
    } else {
      vector<double> tt(M.size());
      for (size_t i = 0; i < M.size(); i++) {
        auto* dz = dynamic_cast<Deferred*>(M[i].get()); if (dz) dz->last_flush_us = 0;
        double t0 = now_us(); M[i]->before_delete(s, d, m, p); tt[i] = now_us() - t0;
      }
      G.del(s, d);
      for (size_t i = 0; i < M.size(); i++) {
        auto* dz = dynamic_cast<Deferred*>(M[i].get());
        double t0 = now_us(); M[i]->after_delete(s, d, m, p); M[i]->tick(); double dt = tt[i] + now_us() - t0;
        st[i].u.push_back(dt); st[i].ack.push_back(dz && dz->bg ? dt - dz->last_flush_us : dt);
      }
    }
    if (dumpf) fprintf(dumpf, "U %d %d %d %llu %d\n", (int)uo[t], s, d, (unsigned long long)m, (int)p);
    struct QQ { int u, r, c; bool targeted, truth; };
    vector<QQ> batch;
    for (int k = 0; k < qpu; k++) {
      int u, r, c; bool targeted = k < ntarget;
      if (targeted) {
        // new-enemy probe: a user below the updated edge, a resource below its head
        int x = s; u = -1;
        for (int step = 0; step < 8 && u < 0; step++) {
          if (G.type[x] == USER) { u = x; break; }
          if (!G.inU[x].empty() && (rng() % 3)) { u = G.inU[x][rng() % G.inU[x].size()].v; break; }
          vector<int> cs; for (auto& e : G.inN[x]) if (G.type[e.v] != RES) cs.push_back(e.v);
          if (cs.empty()) { if (!G.inU[x].empty()) u = G.inU[x][rng() % G.inU[x].size()].v; break; }
          x = cs[rng() % cs.size()];
        }
        if (u < 0) u = usersV[rng() % usersV.size()];
        auto lq = lastq.find(u);
        bool replay = (k == 1) && lq != lastq.end();   // 2nd probe re-issues a previously checked triple of u
        int y = d; r = -1;
        for (int step = 0; step < 12 && r < 0; step++) {
          if (G.type[y] == RES) { r = y; break; }
          auto& o = G.out[y]; if (o.empty()) break;
          y = o[rng() % o.size()].v;
        }
        if (r < 0) r = resV[rng() % resV.size()];
        for (int k2 = rng() % 3; k2 > 0; k2--) { auto& oo = G.out[r]; if (oo.empty()) break; r = oo[rng() % oo.size()].v; }
        c = rng() % NC;
        for (int tries = 0; tries < 64 && !sat(m, ctx[c]); tries++) c = rng() % NC;
        if (replay) { r = lq->second.first; c = lq->second.second; }
      } else if (U01(rng) < 0.7) { double z = U01(rng); int i = (int)(lower_bound(zc.begin(), zc.end(), z) - zc.begin()); i = min(i, P - 1); u = pool[i][0]; r = pool[i][1]; c = pool[i][2]; }
      else rand_triple(u, r, c);
      Mask S = ctx[c];
      bool truth = oracle.check(u, r, S, c);
      truth_allow += truth; nq++;
      if (dumpf) fprintf(dumpf, "Q %d %d %llu %d\n", u, r, (unsigned long long)S, (int)truth);
      batch.push_back({u, r, c, targeted, truth});
      lastq[u] = {r, c};
    }
    // time each method over the whole batch (one timer pair per batch: avoids the ~42 ns timer quantum)
    vector<char> ans(batch.size());
    auto nodeg = [&](size_t j) { auto* a = dynamic_cast<AZX*>(M[j].get()); return a && !a->degtest; };
    for (size_t i0 = 0; i0 < M.size(); i0++) {
      // an AZX-NODEG listed right after AZX swaps timing positions with it on odd updates
      size_t i = i0;
      if (t % 2 && i0 + 1 < M.size() && nodeg(i0 + 1) && M[i0]->name() == "AZX") i = i0 + 1;
      else if (t % 2 && i0 > 0 && nodeg(i0) && M[i0 - 1]->name() == "AZX") i = i0 - 1;
      double t0 = now_us();
      for (size_t k = 0; k < batch.size(); k++) ans[k] = M[i]->check(batch[k].u, batch[k].r, ctx[batch[k].c], batch[k].c);
      double per = (now_us() - t0) / max<size_t>(1, batch.size());
      for (size_t k = 0; k < batch.size(); k++) st[i].q.push_back(per);
      for (size_t k = 0; k < batch.size(); k++) {
        int u = batch[k].u, r = batch[k].r; Mask S = ctx[batch[k].c]; bool truth = batch[k].truth, targeted = batch[k].targeted;
        bool a = ans[k];
        if (a && !truth) {
          st[i].fa++;
          if (targeted) st[i].fa_t++;
          if (dynamic_cast<PLL*>(M[i].get()) || dynamic_cast<SVDTC*>(M[i].get())) {
            bool nd = eval_flags(G, u, r, S, false, emk, eep), nc = eval_flags(G, u, r, ~0ULL, false, emk, eep);
            if (!nc) st[i].fa_stale++; else if (nd) st[i].fa_deny++; else st[i].fa_cond++;
          }
        }
        if (!a && truth) st[i].fd++;
        st[i].nq++;
      }
    }
  }
  printf("# queries=%ld truth_allow_rate=%.4f\n", nq, (double)truth_allow / max(1L, nq));
  printf("method\tbuild_ms\tq_mean_us\tq_p50_us\tq_p99_us\tu_mean_us\tu_p50_us\tu_p99_us\tu_max_us\tindex_MB\tfalse_allow\tfalse_deny\tqueries\textra\n");
  for (size_t i = 0; i < M.size(); i++) {
    string extra;
    if (auto* sp = dynamic_cast<Spice*>(M[i].get())) extra = "hit_rate=" + to_string((double)sp->hits / max(1L, sp->hits + sp->misses));
    if (auto* pl = dynamic_cast<PLL*>(M[i].get())) extra = "rebuilds=" + to_string(pl->rebuilds) + ";last_build_ms=" + to_string(pl->build_ms) + ";fa_cond=" + to_string(st[i].fa_cond) + ";fa_deny=" + to_string(st[i].fa_deny) + ";fa_stale=" + to_string(st[i].fa_stale);
    if (auto* tc = dynamic_cast<TC*>(M[i].get())) extra = "rows_recomputed=" + to_string(tc->recomputed);
    if (auto* lc = dynamic_cast<LCR*>(M[i].get())) extra = "landmarks=" + to_string(lc->lms.size()) + ";relabels=" + to_string(lc->relabels);
    if (auto* sv = dynamic_cast<SVDTC*>(M[i].get())) extra = "fallback_frac=" + to_string((double)sv->fallbacks / max(1L, sv->fallbacks + sv->decided)) + ";recomputes=" + to_string(sv->recomputes) + ";fa_cond=" + to_string(st[i].fa_cond) + ";fa_deny=" + to_string(st[i].fa_deny) + ";fa_stale=" + to_string(st[i].fa_stale);
    if (auto* dz = dynamic_cast<Deferred*>(M[i].get())) extra = "ack_mean=" + to_string(mean(st[i].ack)) + ";ack_p50=" + to_string(pct(st[i].ack, 0.5)) + ";ack_p99=" + to_string(pct(st[i].ack, 0.99)) + ";deferred=" + to_string(dz->ndef) + ";sync=" + to_string(dz->nsync) + ";flushes=" + to_string(dz->nflush) + ";targeted_flushes=" + to_string(dz->ntargeted) + ";targeted_ops=" + to_string(dz->ntargeted_ops) + ";cancel=" + to_string(dz->ncancel) + ";sync_us_mean=" + to_string(dz->sync_us / max(1L, dz->nsync)) + ";flush_ms_total=" + to_string(dz->flush_us / 1000);
    if (auto* az = dynamic_cast<AZX*>(M[i].get())) { size_t ne = 0, nm = 0, mx = 0; for (auto& l : az->L) for (auto& e : l) { ne++; nm += e.ms.size(); mx = max(mx, e.ms.size()); }
      extra = "masks_per_entry_mean=" + to_string(ne ? (double)nm / ne : 0) + ";masks_per_entry_max=" + to_string(mx) + ";max_coef_log2=" + to_string(az->maxcoef > 0 ? log2((double)az->maxcoef) : 0.0) + ";ack_p99=" + to_string(pct(st[i].ack, 0.99)) + ";"; }
    if (auto* az = dynamic_cast<AZX*>(M[i].get())) extra += "probe_frac=" + to_string((double)az->probes / max(1L, az->probes + az->scans)) + ";" + "label_entries=" + to_string(az->entries()) + ";prop_nodes=" + to_string(az->prop_nodes) + ";prop_trips=" + to_string(az->prop_trips);
    // PLL builds after B updates are part of update cost: tick() is timed inside u
    extra += ";fa_targeted=" + to_string(st[i].fa_t);
    printf("%s\t%.1f\t%.3f\t%.3f\t%.3f\t%.2f\t%.2f\t%.2f\t%.0f\t%.2f\t%ld\t%ld\t%ld\t%s\n", M[i]->name().c_str(), st[i].build_ms,
           mean(st[i].q), pct(st[i].q, 0.5), pct(st[i].q, 0.99), mean(st[i].u), pct(st[i].u, 0.5), pct(st[i].u, 0.99),
           st[i].u.empty() ? 0 : *max_element(st[i].u.begin(), st[i].u.end()), M[i]->bytes() / 1048576.0, st[i].fa, st[i].fd, st[i].nq, extra.c_str());
  }
  if (dumpf) fclose(dumpf);
  // who / what on the final graph: AZX vs BFS
  if (azx && bfs) {
    vector<uint32_t> mk(G.n, 0); uint32_t mep = 0; vector<double> tw1, tw2, tq1, tq2; long mism = 0;
    for (int k = 0; k < 300; k++) {
      int r = resV[rng() % resV.size()]; Mask S = ctx[rng() % NC];
      double t0 = now_us(); long a = azx->who(r, S); tw1.push_back(now_us() - t0);
      t0 = now_us(); long b = bfs->who(r, S); tw2.push_back(now_us() - t0);
      if (a != b) mism++;
    }
    for (int k = 0; k < 300; k++) {
      int u = usersV[rng() % usersV.size()]; Mask S = ctx[rng() % NC];
      double t0 = now_us(); long a = azx->what(u, S, mk, mep); tq1.push_back(now_us() - t0);
      // BFS what: closure then same expansion
      t0 = now_us();
      vector<int> qq{u}; vector<char> seen(0); unordered_set<int> vis{u};
      vector<pair<int, Mask>> ar, dr;
      for (size_t i = 0; i < qq.size(); i++)
        for (auto& e : G.out[qq[i]]) {
          if (!sat(e.m, S)) continue;
          if (G.type[e.v] == RES) { (e.pol < 0 ? dr : ar).push_back({e.v, 0}); continue; }
          if (vis.insert(e.v).second) qq.push_back(e.v);
        }
      long b = expand_what(G, ar, dr, S, mk, mep); tq2.push_back(now_us() - t0);
      if (a != b) mism++;
    }
    printf("# who: AZX mean %.1f us p99 %.1f | BFS mean %.1f us p99 %.1f\n", mean(tw1), pct(tw1, .99), mean(tw2), pct(tw2, .99));
    printf("# what: AZX mean %.1f us p99 %.1f | BFS mean %.1f us p99 %.1f | who/what mismatches %ld\n", mean(tq1), pct(tq1, .99), mean(tq2), pct(tq2, .99), mism);
  }
  return 0;
}
