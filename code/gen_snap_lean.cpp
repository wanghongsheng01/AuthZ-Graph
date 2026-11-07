// Lean C++ port of `gen.py snap` for very large SNAP community files (Orkut / Friendster /
// full LiveJournal), where the Python generator does not fit the memory budget.
// Same distributions, id layout and binary output as gen.py (types.bin, e_*.bin, u_*.bin,
// meta.txt); the random number stream differs (std::mt19937_64 instead of Python's random),
// so instances are statistically equivalent, not bit-identical.
//
// usage: gen_snap_lean <out_dir> <cmty.txt.gz> <seed> <max_groups (0 = all)> [n_updates=10000]
#include <zlib.h>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <numeric>
#include <random>
#include <string>
#include <sys/stat.h>
#include <unordered_map>
#include <vector>
using namespace std;
typedef uint64_t Mask;
enum { USER = 0, GROUP = 1, ROLE = 2, RES = 3 };

static mt19937_64 R;
static double U() { return uniform_real_distribution<double>(0, 1)(R); }
static long RI(long a, long b) { return uniform_int_distribution<long>(a, b)(R); }  // inclusive

static const int DOM[5] = {6, 3, 3, 3, 5};
static int OFF[5];
static Mask atom(int a, int v) { return 1ULL << (OFF[a] + v); }
static Mask rand_cond(double p_cond, double p_two = 0.2) {
  if (U() >= p_cond) return 0;
  int k = (U() < p_two) ? 2 : 1;
  int a1 = RI(0, 4), a2 = a1;
  while (k == 2 && a2 == a1) a2 = RI(0, 4);
  Mask m = atom(a1, RI(0, DOM[a1] - 1));
  if (k == 2) m |= atom(a2, RI(0, DOM[a2] - 1));
  return m;
}

static vector<int8_t> types;
struct EV { Mask m; int8_t p; };
static unordered_map<uint64_t, EV> edges;
static inline uint64_t key(int s, int d) { return ((uint64_t)(uint32_t)s << 32) | (uint32_t)d; }
static bool add_edge(int s, int d, Mask m = 0, int8_t p = 1) {
  if (s == d) return false;
  if (types[s] != RES && types[d] != RES && !(s > d)) { fprintf(stderr, "DAG violation %d->%d\n", s, d); exit(1); }
  return edges.emplace(key(s, d), EV{m, p}).second;
}
static int add_nodes(int t, long k) { int s = (int)types.size(); types.insert(types.end(), k, (int8_t)t); return s; }

static vector<int> cats, tabs, cols, rows;
static int rand_resource(double w0, double w1, double w2) {
  double x = U();
  if (x < w0) return cats[RI(0, cats.size() - 1)];
  if (x < w0 + w1) return tabs[RI(0, tabs.size() - 1)];
  if (x < w0 + w1 + w2) return cols[RI(0, cols.size() - 1)];
  return rows[RI(0, rows.size() - 1)];
}

template <class T> static void wr(const string& p, const vector<T>& v) {
  FILE* f = fopen(p.c_str(), "wb"); fwrite(v.data(), sizeof(T), v.size(), f); fclose(f);
}

int main(int argc, char** argv) {
  if (argc < 5) { fprintf(stderr, "usage: gen_snap_lean out cmty.gz seed max_groups [n_upd]\n"); return 1; }
  string out = argv[1], path = argv[2];
  R.seed(atol(argv[3])); long maxg = atol(argv[4]); long n_upd = argc > 5 ? atol(argv[5]) : 10000;
  for (int i = 0, o = 0; i < 5; i++) { OFF[i] = o; o += DOM[i]; }
  // ---- read communities
  gzFile gz = gzopen(path.c_str(), "rb");
  if (!gz) { fprintf(stderr, "cannot open %s\n", path.c_str()); return 1; }
  vector<vector<int64_t>> comms; static char buf[1 << 27]; string line;
  while (gzgets(gz, buf, sizeof buf)) {
    vector<int64_t> c; char* p = buf;
    while (*p) { while (*p == ' ' || *p == '\t') p++; if (*p == '\n' || !*p) break; char* e; long long v = strtoll(p, &e, 10); if (e == p) break; c.push_back(v); p = e; }
    if (c.size() >= 3) comms.push_back(std::move(c));
  }
  gzclose(gz);
  stable_sort(comms.begin(), comms.end(), [](const vector<int64_t>& a, const vector<int64_t>& b) { return a.size() > b.size(); });
  if (maxg > 0 && (long)comms.size() > maxg) comms.resize(maxg);
  long G = comms.size();
  fprintf(stderr, "communities kept %ld\n", G);
  // ---- node id compaction and membership CSR (node -> communities)
  vector<int64_t> names; size_t memb = 0;
  for (auto& c : comms) { memb += c.size(); names.insert(names.end(), c.begin(), c.end()); }
  sort(names.begin(), names.end()); names.erase(unique(names.begin(), names.end()), names.end());
  long NU = names.size();
  auto uid = [&](int64_t x) { return (long)(lower_bound(names.begin(), names.end(), x) - names.begin()); };
  vector<vector<int>> cm(G);  // community -> compact member ids
  vector<long> deg(NU + 1, 0);
  for (long g = 0; g < G; g++) { cm[g].reserve(comms[g].size()); for (auto x : comms[g]) { long u = uid(x); cm[g].push_back((int)u); deg[u + 1]++; } vector<int64_t>().swap(comms[g]); }
  for (long i = 0; i < NU; i++) deg[i + 1] += deg[i];
  vector<int> csr(memb); { vector<long> pos(deg.begin(), deg.end() - 1); for (long g = 0; g < G; g++) for (int u : cm[g]) csr[pos[u]++] = (int)g; }
  fprintf(stderr, "users %ld memberships %zu\n", NU, memb);
  // ---- ids: roles, groups, users, resources
  long nR = max(500L, G / 10);
  edges.reserve(memb + 4 * G + 20 * nR + 1000000);
  int r0 = add_nodes(ROLE, nR);
  for (long r = 1; r < nR; r++) if (U() < 0.4) { int k = RI(1, 2); for (int j = 0; j < k; j++) add_edge(r0 + r, r0 + RI(0, r - 1)); }
  int g0 = add_nodes(GROUP, G);
  // nest each community under the larger community it overlaps most (>= 50%)
  long n_nest = 0; vector<int> cnt(G, 0), touched;
  for (long gi = 1; gi < G; gi++) {
    touched.clear();
    for (int u : cm[gi]) for (long k = deg[u]; k < deg[u + 1]; k++) { int gj = csr[k]; if (gj < gi) { if (!cnt[gj]) touched.push_back(gj); cnt[gj]++; } }
    int best = -1, bc = 0;
    for (int gj : touched) { if (cnt[gj] > bc) { bc = cnt[gj]; best = gj; } cnt[gj] = 0; }
    if (best >= 0 && bc >= 0.5 * cm[gi].size()) { add_edge(g0 + gi, g0 + best); n_nest++; }
  }
  int u0 = add_nodes(USER, NU);
  for (long u = 0; u < NU; u++) {
    for (long k = deg[u]; k < deg[u + 1]; k++) add_edge(u0 + u, g0 + csr[k]);
    if (U() < 0.01) add_edge(u0 + u, r0 + RI(0, nR - 1), rand_cond(0.2));
  }
  vector<int>().swap(csr); vector<vector<int>>().swap(cm);
  for (long g = 0; g < G; g++) if (U() < 0.5) { int k = RI(1, 2); for (int j = 0; j < k; j++) add_edge(g0 + g, r0 + RI(0, nR - 1), rand_cond(0.2)); }
  // resources(20, 40, 15, 4)
  for (int c = 0; c < 20; c++) {
    int cat = add_nodes(RES, 1); cats.push_back(cat);
    for (int t = 0; t < 40; t++) {
      int tb = add_nodes(RES, 1); tabs.push_back(tb); add_edge(cat, tb);
      int cs = add_nodes(RES, 15); for (int i = 0; i < 15; i++) { cols.push_back(cs + i); add_edge(tb, cs + i); }
      int rs = add_nodes(RES, 4); for (int i = 0; i < 4; i++) { rows.push_back(rs + i); add_edge(tb, rs + i); }
    }
  }
  // grants per role 5-20, p_cond 0.3; deny 8% of grants
  long n_grants = 0;
  for (long r = 0; r < nR; r++) { int k = RI(5, 20); for (int j = 0; j < k; j++) if (add_edge(r0 + r, rand_resource(0.05, 0.55, 0.2), rand_cond(0.3), 1)) n_grants++; }
  long nUd = max(1L, NU / 100);
  for (long i = 0; i < (long)(0.08 * n_grants); i++) {
    long pick = RI(0, G + nR + nUd - 1);
    int s = pick < G ? g0 + pick : (pick < G + nR ? r0 + (pick - G) : u0 + (pick - G - nR));
    add_edge(s, rand_resource(0.0, 0.6, 0.3), rand_cond(0.15), -1);
  }
  // ---- initial snapshot
  vector<int32_t> es, ed; vector<Mask> em; vector<int8_t> ep;
  es.reserve(edges.size()); ed.reserve(edges.size()); em.reserve(edges.size()); ep.reserve(edges.size());
  // live lists per kind for the update stream
  enum { MEMBER, NEST, ASSIGN, GRANT, DENY, NK };
  vector<vector<uint64_t>> live(NK);
  long ndeny = 0, ncond = 0;
  for (auto& [k, v] : edges) {
    int s = (int)(k >> 32), d = (int)(k & 0xffffffffu);
    es.push_back(s); ed.push_back(d); em.push_back(v.m); ep.push_back(v.p);
    if (v.p < 0) ndeny++;
    if (v.m) ncond++;
    int ts = types[s], td = types[d];
    if (v.p < 0) live[DENY].push_back(k);
    else if (td == RES && ts != RES) live[GRANT].push_back(k);
    else if (ts == USER && td == GROUP) live[MEMBER].push_back(k);
    else if (ts == GROUP && td == GROUP) live[NEST].push_back(k);
    else if (td == ROLE) live[ASSIGN].push_back(k);
  }
  // ---- update stream (mix as gen.py)
  const double mix[6] = {0.40, 0.05, 0.20, 0.20, 0.10, 0.05};  // member nest assign grant deny cond
  struct Op { int8_t op; int s, d; Mask m; int8_t p; };
  vector<Op> ops;
  auto pop_random = [&](vector<uint64_t>& L, uint64_t& out) {
    while (!L.empty()) { size_t i = RI(0, L.size() - 1); swap(L[i], L.back()); uint64_t k = L.back(); L.pop_back(); if (edges.count(k)) { out = k; return true; } }
    return false;
  };
  auto new_edge = [&](int k, int& s, int& d, Mask& m, int8_t& p) {
    for (int t = 0; t < 50; t++) {
      if (k == MEMBER) { s = u0 + RI(0, NU - 1); d = g0 + RI(0, G - 1); m = 0; p = 1; }
      else if (k == NEST) { s = g0 + RI(0, G - 1); if (s == g0) continue; d = RI(g0, s - 1); m = 0; p = 1; }
      else if (k == ASSIGN) { s = (U() < 0.85) ? g0 + RI(0, G - 1) : u0 + RI(0, NU - 1); d = r0 + RI(0, nR - 1); m = rand_cond(0.2); p = 1; }
      else if (k == GRANT) { s = r0 + RI(0, nR - 1); d = rand_resource(0.05, 0.55, 0.2); m = rand_cond(0.25); p = 1; }
      else { double x = U(); s = x < 0.6 ? g0 + RI(0, G - 1) : (x < 0.8 ? r0 + RI(0, nR - 1) : u0 + RI(0, NU - 1)); d = rand_resource(0.0, 0.6, 0.3); m = rand_cond(0.15); p = -1; }
      if (s != d && !edges.count(key(s, d))) return true;
    }
    return false;
  };
  while ((long)ops.size() < n_upd) {
    double x = U(), c = 0; int k = 0;
    for (; k < 6; k++) { c += mix[k]; if (x < c) break; }
    if (k >= 6) k = 5;
    if (k == 5) {  // condition change on a grant or assignment edge
      vector<uint64_t>& L = (U() < 0.5 && !live[GRANT].empty()) ? live[GRANT] : live[ASSIGN];
      if (L.empty()) continue;
      size_t i = RI(0, L.size() - 1); uint64_t kk = L[i];
      auto it = edges.find(kk); if (it == edges.end()) continue;
      int s = (int)(kk >> 32), d = (int)(kk & 0xffffffffu);
      ops.push_back({-1, s, d, it->second.m, it->second.p});
      Mask nm = rand_cond(1.0); it->second.m = nm;
      ops.push_back({1, s, d, nm, it->second.p});
      continue;
    }
    if (U() < 0.5 && !live[k].empty()) {
      uint64_t kk; if (!pop_random(live[k], kk)) continue;
      auto it = edges.find(kk); int s = (int)(kk >> 32), d = (int)(kk & 0xffffffffu);
      ops.push_back({-1, s, d, it->second.m, it->second.p}); edges.erase(it);
    } else {
      int s, d; Mask m; int8_t p; if (!new_edge(k, s, d, m, p)) continue;
      edges.emplace(key(s, d), EV{m, p}); live[k].push_back(key(s, d)); ops.push_back({1, s, d, m, p});
    }
  }
  ops.resize(n_upd);
  // ---- write
  mkdir(out.c_str(), 0755);
  wr(out + "/types.bin", types); wr(out + "/e_src.bin", es); wr(out + "/e_dst.bin", ed); wr(out + "/e_mask.bin", em); wr(out + "/e_pol.bin", ep);
  vector<int8_t> uo; vector<int32_t> us, ud; vector<Mask> um; vector<int8_t> up;
  for (auto& o : ops) { uo.push_back(o.op); us.push_back(o.s); ud.push_back(o.d); um.push_back(o.m); up.push_back(o.p); }
  wr(out + "/u_op.bin", uo); wr(out + "/u_src.bin", us); wr(out + "/u_dst.bin", ud); wr(out + "/u_mask.bin", um); wr(out + "/u_pol.bin", up);
  long cntT[4] = {0, 0, 0, 0}; for (auto t : types) cntT[t]++;
  FILE* f = fopen((out + "/meta.txt").c_str(), "w");
  fprintf(f, "n %zu\nm %zu\nupdates %zu\nctx 6 3 3 3 5\nusers %ld\ngroups %ld\nroles %ld\nresources %ld\ndeny_edges %ld\ncond_edges %ld\n",
          types.size(), es.size(), ops.size(), cntT[USER], cntT[GROUP], cntT[ROLE], cntT[RES], ndeny, ncond);
  fprintf(f, "source SNAP %s (gen_snap_lean, max_groups=%ld)\nnest_edges %ld\n", path.substr(path.rfind('/') + 1).c_str(), maxg, n_nest);
  fclose(f);
  fprintf(stderr, "wrote %s n=%zu m=%zu updates=%zu\n", out.c_str(), types.size(), es.size(), ops.size());
  return 0;
}
