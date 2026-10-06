# FrontierStore artifact

This artifact supports the negative result reported in **Auditable Source–Fact Storage: Transactional Equivalence, Closure Counterexamples, and Representation Costs**. It contains:

- a finite source/fact transition model;
- the FrontierStore typed immutable-segment implementation;
- a standalone segment parser;
- normalized SQLite and whole-image controls;
- a root-selected SQLite image plus canonical audit-export control;
- current tests, bounded histories, and a joint reader/compaction case;
- retained historical rows and current fixed-order measurements;
- deterministic reconstruction scripts, formal arguments, and evidence ledgers.

The theorem-level result is that the stated logical contract is transactionally expressible and therefore does not require FrontierStore or another specialized engine. In the retained fixed-order runs, the generic rooted database/export composition has lower pooled and per-repetition persistence-plus-full-audit medians, while FrontierStore emits and retains fewer bytes. No counterbalanced observation was admitted, so the timing ordering is descriptive rather than an order-controlled performance conclusion. The artifact preserves the logical result, the cost observations, and the failed order-control evidence.

## Scientific boundary

A logical state contains one epoch, one schema, source records with generations, fact records with nonempty generation-stamped dependencies, and an exact inverse relation. Payloads are uninterpreted. The producer must supply complete dependencies; the store cannot infer omitted semantic edges.

The observation contract distinguishes:

- **complete endpoint identity**: an observation equals one committed source/fact/inverse state;
- **dependency closure**: every declared dependency matches a live source generation and the inverse is exact;
- **current-acquisition freshness**: an acquisition begun after successful update return selects that update or a later endpoint;
- **historical stability**: an already acquired value remains equal to its captured endpoint.

Closure alone is insufficient. `proofs/representation.md` gives a closed-but-nonendpoint relational schedule, and the current suite executes the corresponding regression. The normalized SQLite reader therefore materializes all relations inside one explicit read transaction.

The publication argument assumes one live store instance per directory, one writer, in-process snapshot pins, complete positive byte output, the stated file and directory synchronization effects, atomic same-filesystem root replacement, and no concurrent publisher or collector during standalone checking. Process termination is not a raw-device power cut. A caught FrontierStore or SQLite-R publication exception quarantines the cached instance; current-state operations require a quiescent reopen. SQLite-R also rejects a stale cached writer if another instance has advanced the selector and reclaims only unselected same-epoch orphans before a safe retry.

All persistent create paths capture and validate the supplied bootstrap before replacing an existing target. Every update adapter captures the proposed endpoint and verifies that the supplied delta folds exactly from the private old endpoint before durable mutation. Regression cases require invalid bootstrap or delta input to leave the prior durable state unchanged. This is a preflight-rejection guarantee, not a transactional whole-directory replacement guarantee after an I/O failure.

## Current evidence

### Executed correctness evidence

- The retained 114-method run passes; its transcript and count record are under `results/current/code-audit-tests/`. The current source contains the existing 132 methods plus eight new finite-run admission/coverage methods (140 total). The additional 26 methods and subsequent edits are not retroactively validated by the retained transcript.
- 775 bounded histories cover 2,925 intermediate logical states; this is the complete Cartesian product for the declared five-operation alphabet at lengths two, three, and four. The former 769-history prefix result is retained under `results/retired/` and is not current evidence.
- 7,750 abstract publication observations materialized through a complete-object map and selector have zero closure, endpoint-membership, or cut-correspondence violations; closed-mix and early-selector negative controls fail as expected.
- The joint case completes 20 updates, one reader, and five compactions over 24 sources and 144 facts.
- Every fresh post-return acquisition equals the complete expected endpoint.
- Every held acquisition remains equal to its captured endpoint.
- Pinned selected files remain present, and released obsolete files are collected.
- All 264 retained current comparison endpoints equal the shared logical oracle.

The historical `results/raw/` files are preserved. In particular, earlier rows contain 80 process-exit cases and seven targeted mutations, but they predate current parser/publication repairs and are not presented as current-source correctness evidence.

### Retained three-scale comparison

A shared untimed transition first constructs the exact next state and delta. Persistence return is then timed and followed immediately by a timed full audit; transition construction, dependency discovery, and invalidation-set derivation are excluded. The aligned combined median is computed from each persistence and its following audit. All scale cases run engines in the fixed order FrontierStore, SQLite normalized, then SQLite rooted audit.

| Sources / facts | Engine | Persistence median | Audit median | Combined median | Process writes | Final stored |
|---:|---|---:|---:|---:|---:|---:|
| 500 / 3,000 | FrontierStore | 19.35 ms | 195.39 ms | 219.77 ms | 48 KiB | 1.23 MiB |
| 500 / 3,000 | SQLite normalized | 5.23 ms | 22.65 ms | 28.22 ms | 328 KiB | 5.02 MiB |
| 500 / 3,000 | SQLite rooted audit | 25.66 ms | 77.07 ms | 128.01 ms | 1,332 KiB | 22.01 MiB |
| 2,000 / 12,000 | FrontierStore | 110.13 ms | 869.26 ms | 979.06 ms | 48 KiB | 2.93 MiB |
| 2,000 / 12,000 | SQLite normalized | 20.96 ms | 172.36 ms | 198.11 ms | 388 KiB | 6.93 MiB |
| 2,000 / 12,000 | SQLite rooted audit | 165.09 ms | 566.46 ms | 722.78 ms | 5,182 KiB | 65.64 MiB |
| 6,000 / 36,000 | FrontierStore | 409.71 ms | 2,428.41 ms | 2,884.69 ms | 48 KiB | 7.68 MiB |
| 6,000 / 36,000 | SQLite normalized | 79.31 ms | 625.34 ms | 737.82 ms | 382 KiB | 11.32 MiB |
| 6,000 / 36,000 | SQLite rooted audit | 612.92 ms | 1,973.89 ms | 2,548.22 ms | 15,580 KiB | 136.84 MiB |

Process-accounted writes are operating-system block-write counters around persistence, not device traffic or flash write amplification. Final stored bytes are short-run directory sizes, not a long-running reclamation model. Ordinary page cache is retained.

The retained pooled medians favor the rooted database/export control at all three scales, and every per-repetition median has the same ordering. SQLite rooted audit is lower on 44/48, 20/24, and 11/16 aligned transition identities from small to large. SQLite rooted audit writes 27.75, 107.96, and 324.58 times as many process-accounted bytes as FrontierStore and retains 17.91, 22.40, and 17.81 times as many bytes. Normalized SQLite has the smallest pooled median when a separate export is unnecessary. Because all runs use one fixed order and two bounded counterbalance launches produced no accepted observation, none of these timing statements is an order-independent or causal speed claim.

## Representation choices

### FrontierStore

A visible `ROOT` selects one base segment and an ordered sequence of immutable deltas. Each segment contains a header, typed records in a fixed phase order, and a footer. The root and parser bind epoch, schema, parent chain, live counts, filenames, and selected paths. An update writes only touched source, fact, and reverse records plus the new root, although the current Python transition/check paths still copy or scan complete live maps.

### SQLite normalized

Metadata, sources, facts, and dependency edges are stored in normalized tables. The measured wrapper receives the shared precomputed post-state and delta, applies that delta in one write transaction, and materializes each observation in one explicit read transaction. It therefore executes the atomic-write and one-snapshot-read premises, but does not independently derive the invalidation set by SQL join or time that derivation. The relational existence result is proved in `proofs/representation.md`; SQLite's own interpreter is the audit surface.

### SQLite rooted audit

Each update writes a complete immutable SQLite image and a complete canonical export, synchronizes both, and atomically replaces one selector naming the pair. The export parser uses only the Python standard library and checks strict structure, source-generation closure, schema equality, ordering, and exact reverse membership. The audit requires equal database and export decodes. SQLite internals are not modified, and SQLite physical pages are not independently parsed. The frozen timing harness calls the complete selected-pair reader twice per audit sample--once through `durable_state()` and once through `audit_report()`--then compares the first decoded state with the logical oracle. Each retained rooted-audit sample therefore includes two full database decodes, two export parses, and two pair comparisons; the published numbers are not single-pass timings.

## Reconstructing retained and current summaries

Run these commands from the artifact root. Use a fresh destination for `summarize_results.py`; the scripts do not execute the storage engines.

```sh
mkdir -p ../summary-reconstruction ../current-summary-reconstruction ../paper-input-reconstruction
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_checked_results.py --results results
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_artifact.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_publication_references.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_reviewer_repairs.py --output ../reviewer-repairs.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/summarize_results.py --results results --output ../summary-reconstruction
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/summarize_current.py --results results/current --output ../current-summary-reconstruction --paper-dir ../paper-input-reconstruction
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/generate_paper_values.py --output ../paper-input-reconstruction/paper-values.tex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/journal_robustness.py --results results/current --output ../current-summary-reconstruction/journal-robustness.json
```

`verify_publication_references.py` fails closed unless the audit and publication manifest contain the same duplicate-free inventory of at least 55 scholarly references, every audit row has closed metadata and passage-fit status with a valid date, and titles and stable locators agree. When the paper sources are available, run this extended check from the project root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=artifact \
python artifact/experiments/verify_publication_references.py \
  --audit artifact/reference_audit.csv \
  --manifest artifact/publication_reference_manifest.csv \
  --tex paper/main.tex \
  --bib paper/references.bib
```

Extended mode also requires exact equality among manuscript citation keys, BibTeX entries, the passage audit, and the publication manifest; checks audited author order, year, venue, title, publication kind, and DOI/arXiv locator agreement where encoded; and rejects multi-key citation commands. The delivered paper contains 62 cited entries, all 62 audit rows are closed, and the largest citation command contains one key.

`journal_robustness.py` deterministically reconstructs distribution summaries, paired differences, and TeX macros from the 264 already accepted rows. It executes no storage engine, uses a fixed bootstrap seed, and states a fixed-order conditional inference boundary. The package verifier byte-compares all four retained journal-robustness outputs.

The historical aggregator writes `historical-values.tex`; that file preserves the old historical macro surface but is not an input to the current manuscript. `generate_paper_values.py` independently derives the manuscript's bounded-history, joint-history, retained-row, and campaign-bound macros from retained evidence. Its output must match `results/current/paper-inputs/paper-values.tex` and the paper-side `paper-values.tex` byte for byte. The other four paper inputs are likewise retained under `results/current/paper-inputs/` and regenerated from the accepted observations plus the complete passing test transcript. The package verifier fails closed on a mismatch.

The current aggregator admits only:

- `results/current/current-small/`;
- `results/current/current-medium-retry/`;
- `results/current/current-large/`.

It records 264 retained observation rows, regenerates the paper CSVs and macros, and emits scale-level and per-repetition order diagnostics. The pilot, two monitor failures, first uncontrolled medium attempt, interface-error order launch, and no-observation counterbalance launch remain retained but excluded. No excluded output is silently substituted.


## Platform and historical environment boundary

`PLATFORM.md` separates platform-neutral data-only reconstruction from engine
execution.  The accepted timing records confirm only a Linux-compatible POSIX
surface with `fcntl`, procfs (`/proc/self/io`), cgroup-v2 controls, schedulable
CPU affinity, and zero configured swap at admission.  They did not retain the
Python/SQLite version, kernel/distribution, filesystem and mount options, CPU,
storage medium, virtualization provider, or immutable timed-source identifier.
Those fields are explicitly unknown in `results/current/accepted-environment.json`
and are not backfilled from a later review machine.

The package verifier parses every delivered `.py` source with `ast.parse`
without importing it, excluding only root-level Git checkout metadata. The
retained repair record's 34-source inventory is checked against its own count
and preserved as historical coverage; additions are parsed in the current run,
not retroactively inserted into that record. `verify_reviewer_repairs.py` additionally creates an
unimported bad Python file in a temporary directory and requires that same
parser surface to reject it with a path-specific witness.

Current summaries, paired diagnostics, and robustness reconstruction admit rows
only after checking completed controller accounting, matching CSV/JSON values,
finite nonnegative measurements, and the full declared observation grid. Missing
or duplicate identities are rejected before aggregation. Canonical robustness
JSON and TeX outputs use LF newlines on every platform, preserving the retained
byte surface. The data-only regression subset is runnable with:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m unittest tests.test_evidence_admission tests.test_journal_evidence -v
```

Logical-model and standalone-parser imports do not eagerly import the POSIX
store. Requesting `FrontierStore` or `Snapshot` still loads the Linux-specific
engine. The finite-model and SQLite preflight tests can therefore run without
emulating Linux storage behavior. SQLite preflight rejects non-UTF-8 payloads
(such as lone Python surrogates) before create replacement or update mutation.

Repository CI uses a 180-second whole-check timeout and always attempts to upload
raw logs. It runs data reconstruction, the data-only subset, finite enumeration,
and in-memory/preflight SQLite tests, not storage-engine benchmarks or the
complete Linux regression suite. An unrun workflow is not
executed correctness evidence.

## Current executable cases

The historical guarded runner defines named cases for tests, histories, scale comparison, and attempted order control. Its lifetime allowance is exhausted: `run_bounded.py` must not admit another run or reset `resource-accounting.json`. Data-only reconstruction remains usable.

For a separately authorized **new finite correctness run**, the flat artifact repository has one fixed entry point:

```sh
PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 python -B -m experiments.fresh_run \
  --authorize-new-run --out /tmp/p021-owned-checks
```

The destination must be absent and outside the deliverable. This does not reuse the old allowance. It reserves the entire new run before launching, retains failed reservations without refund, checks the old ledger and source bytes afterward, and leaves all raw output in that destination. Six sequential stages cover the complete test inventory, finite histories, owned SQLite and strict-export cases, owned process exits, the joint history, and retained-data reconstruction. There is no scale/throughput matrix, external source-program execution, or downloaded experimental input. See `PLATFORM.md` for limits and accounting scope.

The new Windows diagnostic exercises all test discovery, but is not a passing full-suite result: 24 of the existing 132 methods and all eight new methods pass; ten existing rooted-image methods error on file synchronization and 98 existing methods cannot be imported because their modules require `fcntl`. There are no skipped methods or assertion failures. The separate finite stage checks 775 histories, 2,925 states, and 7,750 abstract cuts. The owned SQLite stage matches eight persisted endpoints and an actual reader spanning another connection's commit, rejects six malformed databases and nine malformed exports, and accepts its valid export control. Six owned SQLite process exits cover WAL and DELETE at before-commit, after-commit, and after-close. These are process-exit and SQLite-API results with the operating system alive; the owned SQL bootstrap does not test the adapters' directory synchronization. Segment publication/recovery and the joint history remain unexecuted on this host. No synchronization or locking mechanism was replaced to obtain these results.

`.github/workflows/scientific-checks.yml` prepares that same finite entry point on Ubuntu 24.04 from the **flat artifact root**, on main pushes or explicit dispatch. It retains a failing job exit and uses an always-run raw-output upload. The workflow has not been executed as part of this local handoff; preparing it is not cloud validation. The older data-only integrity workflow remains separate.

The experimental launcher uses one child, bounded address space, a wall watchdog, concurrent-tree monitoring, and a parent-death signal on supported systems. Monitoring is a bounded operational guard, not a proof of continuous peak memory or progress through uninterruptible kernel I/O.

## Resource accounting

Unknown inherited scientific use is conservatively charged to the full non-reserved allocation rather than treated as zero. Failed and interrupted attempts consume their complete reservations. The historical campaign CPU upper bound reaches 28,800 seconds, with no remaining allowance. The two order-control attempts are retained and excluded from timing inference. See the unchanged `resource-accounting.json` and historical per-case accounting files. New finite-run reservations and observed use are recorded only in each external destination's `run.json` and per-stage accounting; they do not alter historical measurements or replenish that lifetime allowance.

## Contents

- `frontierstore/`: transition model, segmented store, standalone checker, audit-export parser, baselines, workload generation, and lexical input adapter.
- `tests/`: current behavioral, boundary, representation, read, write, export, and process-exit cases.
- `experiments/`: bounded execution, deterministic workloads, retained-result aggregation, order diagnostics, journal robustness reconstruction, and fail-closed verification.
- `proofs/`: logical, publication, representation, and cost-bound arguments.
- `results/raw/`: unchanged historical observations.
- `results/current/`: retained current, pilot, failed/excluded, order-control, and summary evidence.
- `claim_evidence_ledger.csv`: manuscript claims mapped to arguments, code, tests, results, maturity, and limitations.
- `calibration_matrix.csv`: complete-paper venue and related-work calibration.
- `reference_audit.csv` and `publication_reference_manifest.csv`: the 62-entry passage audit and exact publication inventory.
- `external_resources.csv` and `external_inputs/`: provenance, licenses, and lawful text inputs.
- `FORMAT.md`: exact durable byte-format and publication contract.

The ten bundled Solidity excerpts are consumed only as irregular licensed text. No contract is executed, and lexical facts are not vulnerability labels or semantic audit ground truth. No private data, accelerator, production service, or new human evidence is required.
