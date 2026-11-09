# Artifact: No Stale Allows — Maintaining Effective Permissions over Dynamic Authorization Graphs with Conditions and Deny

This repository contains the prototype (FLIX), all baselines, the workload generators, and the raw outputs from which every number in the paper is generated.

## Contents

| Path | What it is |
|---|---|
| `code/authz.cpp` | Single-file C++17 implementation of FLIX (frontier 2-hop labels with counting deltas), FLIX-D (polarity-aware deferral), Lag (naive deferral), GrpClosure, BFS (online evaluation), Cache-ML / Cache-FC, TC, 2-hop (PLL, deny-blind), LCR landmark index, and SV-DTC (support-vertex dynamic transitive closure), BiBFS (`BIBFS`, online direction-optimizing bidirectional search), plus the experiment driver. FLIX chooses probe mode when `4*c_in*deg+(u) < c_lab`, the probe bound of Section IV-B; `AZX-NODEG` is the ablation that omits `deg+(u)` (an index of its own; listed right after `AZX`, the two swap timing positions on odd updates); `AZX-K<c>` uses the constant `c` instead of 4 (an index of its own). This file (build `9cc732b8a369`) produced `results/rev4`; for every method of `results/rev3` it makes the same decisions as `code/authz_4128c8762e6c.cpp`, the exact source of `results/rev3` (`paper_facts.py` checks that the FLIX counters of every `results/rev4/bibfs` run equal those of the `results/rev3/main` run with the same graph and seed). |
| `code/gen.py` | Generators for the synthetic Zanzibar-style graphs (`syn`), the deep-nesting graphs (`deep`), SNAP community graphs (`snap`), the enterprise-shaped graph (`ent`, no real rows), and the UCI Amazon Access Samples graph (`amzn`). |
| `code/gen_snap_lean.cpp` | C++ port of `gen.py snap` for the largest SNAP graphs (LJ-full, Orkut, Friendster). It writes the same binary layout but uses a different RNG. |
| `code/parse_real.py`, `code/gen_real.py`, `code/real_pathcount.py` | Parsers and generators for the real hierarchies: Gene Ontology, HPO, Simple English Wikipedia categories and English Wikipedia categories. |
| `code/sql_cte.py` | Recursive SQL formulation of the decision semantics (SQLite), used as an independent cross-check. |
| `code/run_rev3.py`, `scripts/server/rev3_*.sh` | The complete experiment matrix of the paper (`results/rev3`): the matrix of `run_rev1.py` plus group `abl` (FLIX, FLIX without `deg+(u)`, BFS in one process), with a memory watchdog and the English Wikipedia phase. |
| `code/run_rev4.py`, `scripts/server/rev4_*.sh` | The additions of the last revision (`results/rev4`): group `bibfs` (FLIX, BFS, BiBFS) on every graph and seed of the main runs, group `kconst` (FLIX with the constants 4, 1, 2, 8, 16 and BFS in one process; English Wikipedia: two processes), and the depth sweep with BiBFS. All three set `AUTHZ_SHUFFLE=1`. |
| `code/run_rev1.py` | The previous matrix (`results/rev2`, build `cdb396bf191d`, test `4*c_in < c_lab`). |
| `code/paper_facts.py`, `code/paper_fig1.py` | Turn raw outputs into the paper's numbers, tables and figures. They refuse outputs whose build hash differs. |
| `scripts/parse_amzn.py` | Streams the UCI Amazon archive without extracting it (the wide CSV is 4.8 GB). |
| `results/rev3/` | Raw outputs behind every table in the paper. Each file starts with `# build=4128c8762e6c` (`-wide` for English Wikipedia). `results/rev3/abl/` holds the ablation of the mode test and `results/rev3/summary/ablation_nodeg.tsv` its per-graph summary; `results/rev3/oom_attempts/` holds the GrpClosure run on English Wikipedia that exceeded the 80 GB memory guard. |
| `results/rev4/` | Raw outputs of `run_rev4.py` (`# build=9cc732b8a369`, `-wide` for English Wikipedia), with per-graph summaries in `results/rev4/summary/bibfs.tsv` and `kconst.tsv`. `results/erp4/` holds the same additions for the ERP case study (generated replay driver, same build). |
| `results/rev2/` | Outputs of the previous build `cdb396bf191d` (test without `deg+(u)`), kept for reference. `results/rev2/meta/` (also copied to `results/rev3/meta/`) holds the dataset sizes and the revocation counts of the update streams, so that the numbers can be regenerated without the datasets. |
| `results/erp3/`, `code/make_erp_replay.py` | Raw outputs of the production ERP case study (Section VI-H), run on the laptop below because the data stays on premises (`results/erp`: previous build). The ERP export is de-identified enterprise data and is not released, nor is the script that builds the graph from it. The replay driver `code/erp_replay.cpp` is generated from the unchanged `code/authz.cpp` by `make_erp_replay.py`; its only change is that checks other than new-enemy probes are sampled from a page-access trace (`qtrace.bin`). |
| `results/e1/`, `code/make_degmode.py`, `scripts/server/e1_*.sh` | Earlier experiment on the previous build (commit `e255b93`), superseded by `results/rev3/abl`: `make_degmode.py` generated `code/authz_deg.cpp` from the `cdb396bf191d` version of `code/authz.cpp`; its only change is the mode test `4*c_in*deg+(u) < c_lab`. `results/e1/main` runs it with FLIX and BFS on every graph of the main runs; `results/e1/ctrl` runs the unchanged binary with the same two methods and arguments, so that both tests are compared under the same conditions. `results/e1/summary.tsv` lists both ratios per graph. |
| `code/check_topo.py` | Checks that every graph and update stream keeps subject identifiers in topological order (Section IV-D); all 27 graphs pass. |
| `results/sql/`, `results/server/meta/`, `results/real/` | The SQLite cross-check of the pilot runs (stated as such in the paper) and the size metadata of the server-scale and English Wikipedia graphs. |

## Build

```bash
H=$(md5sum code/authz.cpp | cut -c1-12)
g++ -O2 -std=c++17 -DAUTHZ_SRC_HASH="\"$H\"" -o code/authz code/authz.cpp
g++ -O2 -std=c++17 -DAUTHZ_WIDE -DAUTHZ_SRC_HASH="\"$H-wide\"" -o code/authz_wide code/authz.cpp
```

The `_wide` build uses exact 128-bit path counts. It is needed for the English Wikipedia category hierarchy, whose path counts exceed 2^63. Both builds abort on overflow.

## Data

All datasets except the ERP case study are public or synthetic.

- **SNAP** ground-truth communities from https://snap.stanford.edu/data/: `com-dblp`, `com-youtube`, `com-lj`, `com-orkut` and `com-friendster` (`*.all.cmty.txt.gz`).
- **UCI Amazon Access Samples**, https://archive.ics.uci.edu/dataset/216 (DOI 10.24432/C5JW2K, CC BY 4.0).
- **Gene Ontology** (`go-basic.obo`) and the **Human Phenotype Ontology** with their public annotation files.
- **Wikimedia dumps** of `categorylinks` and `page` for `simplewiki` and `enwiki`.
- **Synthetic graphs**, regenerated deterministically with `code/gen.py` and fixed seeds. For example:

```bash
python3 code/gen.py syn data/syn100k 100000 1
python3 code/gen.py deep data/deep_L16 100000 16 2000 2
python3 code/gen.py snap data/dblp com-dblp.all.cmty.txt.gz 1
```

## Run

```bash
./code/authz data/syn100k AZX,BFS,SPICE-ML,SPICE-FC,CLOSURE,DEFER-SAFE,DEFER-NAIVE 10 -1 1
./code/authz data/syn100k AZX,AZX-NODEG,BFS 10 -1 1      # ablation of the mode test
AUTHZ_SHUFFLE=1 ./code/authz data/syn100k AZX,BFS,BIBFS 10 -1 1                              # BiBFS
AUTHZ_SHUFFLE=1 ./code/authz data/syn100k AZX,AZX-K1,AZX-K2,AZX-K8,AZX-K16,BFS 10 -1 1       # constant sweep
python3 code/run_rev3.py . 8     # with code/authz built from code/authz_4128c8762e6c.cpp
python3 code/run_rev4.py . 8
```

The driver arguments are, in order: `<data dir> <methods> <checks per update> <updates (-1 = all)> <seed> <W> <B> <K>`.

Method names map to the paper as follows: AZX = FLIX; AZX-NODEG = FLIX without `deg+(u)` in the mode test (column -deg of Table IX); AZX-K<c> = FLIX with the constant c in the mode test; BIBFS = BiBFS; DEFER-SAFE = FLIX-D; DEFER-NAIVE = Lag; CLOSURE = GrpClosure; SPICE-ML / SPICE-FC = Cache-ML / Cache-FC; PLL = 2-hop (no deny).

Environment variables:

| Variable | Effect |
|---|---|
| `AUTHZ_DEFER_B` | Deferral threshold |
| `AUTHZ_APPLY_COST_US` | Simulated replica apply latency |
| `AUTHZ_BG_FLUSH` | Treat threshold flushes as background work |
| `AUTHZ_FORCE_MODE=scan\|probe` | Ablation of the check algorithm's mode selection |
| `AUTHZ_DUMP=<file>` | Dump the update/query stream for `sql_cte.py` |
| `AUTHZ_SHUFFLE=1` | Time the methods in a fresh random order for every update (an RNG of its own: the updates and checks do not change) |

Every check is compared against the online oracle. The `false_allow` and `false_deny` columns count disagreements.

## Regenerate the paper's numbers

```bash
pip install -r requirements.txt
AUTHZ_RESULTS=results/rev3 AUTHZ_ERP_RESULTS=results/erp3 AUTHZ_RESULTS4=results/rev4 AUTHZ_ERP_RESULTS4=results/erp4 python3 code/paper_facts.py
```

This writes `paper/numbers.tex` (all macros, including those of the ERP case study and of the ablation), `paper/tables/*.tex` and `paper/figs/*.pdf` from `results/rev3/`, `results/rev4/`, `results/erp3/` and `results/erp4/` alone; no dataset is needed. `paper/numbers_sources.txt` lists the raw file behind each macro.

## Hardware used

- An Intel Xeon Platinum 8336C server (28 hardware threads, 110 GB RAM, Ubuntu 22.04, g++ 11.4) for all `results/rev4`, `results/rev3`, `results/rev2` and `results/e1` runs (`results/rev3` and `results/rev4` with up to eight concurrent jobs at nice 10 on the otherwise idle server; `results/e1` one job at a time).
- Python 3.9 with the packages in `requirements.txt` to regenerate numbers, tables and figures.
- An Apple M-series laptop (16 GB) for the pilot runs and the ERP case study.

All runs are single-threaded; several runs were executed concurrently on the server.

## License

Code under the MIT License. The derived datasets follow the licenses of their sources.
