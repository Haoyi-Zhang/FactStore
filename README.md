# FrontierStore artifact

## Current-source fanout/cadence evidence

`experiments/fanout_cadence.py` extends the owned SQLite-API lifecycle without
changing the native engines' persistence contract. It uses dependency widths
2/8, separate archive-audit intervals 1/4, 64 sources/384 facts, eight updates
and three shuffled-order repetitions per configuration. Workload/affected-set
generation and state conversion are included in the timed lifecycle and
transition phase. Every commit still has a current endpoint read; audit cadence
controls only the additional database/export comparison. Leases, actual archive
deletion and checkpoint policy are common. Source-before/after bindings,
environment/SQLite compile options, raw states and phase samples are retained in
`results/sqlite-fanout/`. Unavailable native fcntl support is
recorded without substituting lock or directory-fsync calls.

```sh
python -B -m unittest experiments.fanout_cadence_tests -v
python -B -m unittest experiments.history_lifecycle_tests -v
python -B -m experiments.fanout_cadence --check results/sqlite-fanout --tex ../paper/fanout-cadence.tex
```

For a new measurement, replace `--check` with `--out` and choose a fresh owned
directory. Replaying/checking retained data is not a new timing run. This is not
the requested native FrontierStore/SQLite-N/SQLite-R matched lifecycle result;
the separately admitted Linux experiment below supplies that scoped comparison.
No whole-engine ordering or device-write conclusion follows from this API
sensitivity alone.

The newly measured Windows cohort has the semantic dataset name `sqlite-fanout`.
Only its new directory name changed; raw samples, observed footprints, negative
results and the consumed source-before/after bytes are unchanged. This dataset
is not the native Linux comparison below.

### Reviewed supported-Linux comparison

`experiments/native_fanout_cadence.py` adds actual engine selection for the
unchanged FrontierStore, SQLite-Normalized and SQLite-Rooted-Audit writers.
The `native-fanout` job in `.github/workflows/scientific-checks.yml` runs it on
the existing Ubuntu 24.04/Python 3.12 CI, preserving every current check:

```sh
python -B -m unittest experiments.native_fanout_cadence_tests -v
python -B -m experiments.native_fanout_cadence --authorize-new-study --engines frontier sqlite_n sqlite_r --out "$NATIVE_FANOUT_OUT" --json-out "$NATIVE_FANOUT_OUT/verified-summary.json"
python -B -m experiments.native_fanout_cadence --check "$NATIVE_FANOUT_OUT"
```

The output is a fresh `native-fanout` directory under `$RUNNER_TEMP`, outside
the checkout. No retained result or exhausted historical ledger is rewritten.
There are 36 lifecycles: widths 2/8, audit intervals 1/4, three repetitions and
all three native engines. The six engine permutations occur twice across the
12 paired blocks; within each width/cadence, each engine occupies every slot
once. Each lifecycle includes workload/affected-set generation, eight shared
updates, a bootstrap lease through update four, matched maintenance epochs at
updates four/eight, physical historical reads after collection, release and
actual reclamation, and a checked orderly reopen. The common empty maintenance
transition advances all engines to the same epoch: FrontierStore publishes a
base, SQLite-N commits the empty delta and checkpoints, and SQLite-R publishes
its native complete pair. SQLite-N uses native FULL/WAL and never deletes live
WAL/SHM files. SQLite-R's minimal harness collector protects the captured pair
until lease release and fsyncs directories after confined obsolete-file unlink.

The common comparison contract is quiescent, post-return durable current and
leased endpoint equality. Native writers retain their actual synchronization
and failure semantics. SQLite-N remains the logical SQL null: it has neither
an independent export nor the rooted engines' atomic cross-interface selector.
Scheduled full audits use each delivered interpreter; SQLite-R performs one
selected-pair pass per audit call (in addition to its mandatory current read),
not a rewritten interpretation of historical double-pass measurements.
Phase totals separate transition, persistence, current/held reads, audits,
maintenance, collection, release and reopen. Full wall time also includes raw
trace instrumentation; it is not presented as uninstrumented device time.

The job bounds the campaign at 360 seconds externally (300 seconds between
cases and 60 seconds per case internally), checks/replays the saved raw
per-transition trace and final physical endpoint, prints compact JSON summaries,
and uploads source/ledger identities, environment/SQLite options, actual native
fsync/flock probe, compressed traces, final stores and logs even on failure.
The artifact name is `p021-native-fanout-<run_id>-<run_attempt>`, retained for
14 days. The reviewed successful run 40066415944 (job 120790520723, commit
`f12a27c375ede02ebd3a12443485ebea815df837`) is now imported as
`results/native-fanout`. Its original 169 uploaded members, logs and final stores
are preserved, together with the byte-exact nine-source preimage and archive
origin. `results/native-fanout/README.md` records the observed host and scope.
A generated cohort is accepted only after the source-bound verifier succeeds;
case-completion lines alone do not certify a result. Transaction replay compares
the exact ordered JSON input, including dependency arrays after trace decoding.
The Linux smoke test also replays its saved transactions rather than testing
only the live writer. A successful `verified-summary.json` and reviewed Linux
job are required before importing native measurements into manuscript tables.
An old failed run retains its own source identities and is never rebound to a
fixed driver. The separately named `replay_cohort_contents` diagnostic returns
a contents-only status, not fresh-source acceptance. The native three-engine
comparison is now supported by this source-bound cohort, not by the first failed
attempt or the Windows API measurements. Data-only replay confirms all 36 cases,
288 updates, 72 maintenance epochs, 180 scheduled audits, 144 held reads and
36 orderly reopens with zero mismatch. The nine measured sources are unchanged.

```sh
python -B -m experiments.native_fanout_results --check results/native-fanout --tex ../paper/native-fanout.tex
python -B -m experiments.native_fanout_results --check results/native-fanout --check-tex ../paper/native-fanout.tex
python -B -m unittest tests.test_native_fanout_results -v
```

The generated table shows SQLite-N lowest in lifecycle median for all four
configurations. FrontierStore is lower than SQLite-R at width two; SQLite-R is
lower at width eight. The comparison is three repetitions of a small synthetic
state on one native host, not a universal engine ordering, independent real-world
case, power-loss experiment or physical-device study. Import/replay on Windows
does not re-execute native writers. Narrow `.gitattributes` rules preserve all
nine measured files and imported dataset bytes even with `core.autocrlf=true`;
an owned temporary-Git checkout regression verifies this without changing them.

### Compressed scientific evidence

`python -B -m experiments.verify_artifact` admits the six retained lifecycle
records, the twelve `results/sqlite-fanout` records and the 36
`results/native-fanout` traces through separate exact scientific consumers.
The native consumer checks original-member bytes, accepted artifact/commit,
all nine current/preimage source bindings, bounded strict JSONL, ordered
transactions, endpoints, phase sums, maintenance/lease schedules, saved physical
stores and uploaded-summary equality before admitting only those 36 paths.
For the SQLite-API cohort, it checks the fixed case matrix and
filenames, measured source-before/after bindings, bounded JSON decoding, every
input/current/held endpoint, audit and lease/reclamation schedule, checkpoint
evidence, complete raw-to-summary equality and reaggregated medians. The shared
decoder rejects duplicate members, nonfinite values, truncated gzip and decoded
records above 32 MiB. No directory-wide gzip or archive exception is used;
unconsumed compressed files and packages remain errors, including adjacent
copies with plausible names. Run these admission regressions with
`python -B -m unittest tests.test_compressed_records -v`.

`experiments/closure_scenario.py` actually interleaves two SQLite connections:
separate SELECT groups produce a closed nonendpoint, while one enclosing read
transaction returns a complete endpoint. `results/closure-scenario-20261024.json`
retains both observations. The documented analyzer use case and exact boundary
are in `external_inputs/clangd-usecase.md`; the SQL schedule is owned and is not
an execution or reproduction of clangd issue 1088.

This artifact supports the negative result reported in **Auditable Source–Fact Storage: Transactional Equivalence, Closure Counterexamples, and Representation Costs**. It contains:

- a finite source/fact transition model;
- the FrontierStore typed immutable-segment implementation;
- a standalone segment parser;
- normalized SQLite and whole-image controls;
- a root-selected SQLite image plus canonical audit-export control;
- current tests, bounded histories, and a joint reader/compaction case;
- retained historical rows and current fixed-order measurements;
- deterministic reconstruction scripts, formal arguments, and evidence ledgers.

The stated logical contract is transactionally expressible and does not require FrontierStore. In the retained fixed-order runs, the rooted database/export control has lower persistence-plus-full-audit medians and FrontierStore emits fewer process-accounted bytes. Stored size depends on maintenance and historical retention: the checkpoint and matched-history control below separates those effects. Those timing rows remain descriptive because they were not counterbalanced; the new native lifecycle is separately counterbalanced and does not rewrite them.

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

- The earlier 114-method transcript is under `results/current/code-audit-tests/`. A retained Linux run in `results/current/linux/` passes the 140 methods of its recorded source without skips. Six correctness stages finish in 12.80 seconds and also cover the finite model, 26 process-exit cases, strict SQLite/export boundaries, and the joint update/compaction history. This is separate from the older engine performance measurements and the later API lifecycle/maintenance corrections.
- 775 bounded histories cover 2,925 intermediate logical states; this is the complete Cartesian product for the declared five-operation alphabet at lengths two, three, and four. The former 769-history prefix result is retained under `results/retired/` and is not current evidence.
- 7,750 abstract publication observations materialized through a complete-object map and selector have zero closure, endpoint-membership, or cut-correspondence violations; closed-mix and early-selector negative controls fail as expected.
- The joint case completes 20 updates, one reader, and five compactions over 24 sources and 144 facts.
- Every fresh post-return acquisition equals the complete expected endpoint.
- Every held acquisition remains equal to its captured endpoint.
- Pinned selected files remain present, and released obsolete files are collected.
- All 264 retained current comparison endpoints equal the shared logical oracle.

The historical `results/raw/` files are preserved. In particular, earlier rows contain 80 process-exit cases and seven targeted mutations, but they predate current parser/publication repairs and are not presented as current-source correctness evidence.

Retained-data admission validates all measured numeric cells before aggregation,
including cells not selected by a median. Nonfinite or negative measurements,
unknown run modes, invalid resource fields and unrecognized Boolean values are
rejected by both the historical aggregator and its verifier. Seven data-only
regression methods cover these boundaries and require the unchanged original
data and summaries to pass. Run them in ordinary and optimized Python:

```sh
python -B -m unittest tests.test_historical_admission -v
python -B -O -m unittest tests.test_historical_admission -v
```

These checks validate admissible data and deterministic reconstruction; they
do not recover the missing historical host/source identity or execute a
matched cross-engine lifecycle.

### Historical physical-byte checks

The current joint runner captures the selector bytes at each historical pin
acquisition. While that pin remains active and publication/collection is
quiescent, a standalone parser reconstructs the complete endpoint from the
protected segment bytes. It retains the captured epoch and compares all source,
fact, dependency, reverse, and schema fields with the independent historical
dictionary. The 20-update schedule checks five acquisitions and 45 subsequent
update, compaction, and collection events, for 50 physical-endpoint comparisons.
This is distinct from reading the held in-memory value or checking the current
selector. The older retained joint transcript predates this additional oracle;
current scientific CI executes the updated schedule directly.

Nine portable fixture methods check the explicit-selector reader, including
strict root parsing, missing/truncated files and a structurally valid payload
change that only full endpoint equality detects. Five POSIX methods check real
pinning through compaction and collection, two simultaneous leases with
idempotent release, the complete joint schedule, its changed-history control,
and reader cleanup after an initial parser failure. The current full test
inventory includes these fourteen methods; the historical 132-method count and
its transcripts remain tied to their own source. These are correctness checks,
not new engine timings or a matched three-engine lifecycle comparison.

```text
python -B -m unittest tests.test_historical_bytes -v
```

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

### Checkpoint and matched-history control

`experiments/maintenance_control.py` applies the deterministic workload through
the normalized SQLite delta writer, with automatic checkpoints disabled. It
measures database, WAL and shared-memory bytes before and after a real truncating
checkpoint, its elapsed time, and the equality of the complete decoded endpoint.
A separate retained calculation sums the contents of every image/export pair
and of the last pair; it did not execute latest-only deletion, and these pair
columns exclude unmeasured archive sidecars. The run is retained in
`results/maintenance-control.json`.

| Sources / facts | Before checkpoint | After checkpoint | Checkpoint | All pairs | Latest pair |
|---:|---:|---:|---:|---:|---:|
| 500 / 3,000 | 6,476,256 B | 913,408 B | 5.23 ms | 24,164,763 B | 1,490,968 B |
| 2,000 / 12,000 | 7,613,280 B | 3,022,848 B | 11.33 ms | 69,536,499 B | 5,411,019 B |
| 6,000 / 36,000 | 11,892,888 B | 8,790,016 B | 12.95 ms | 143,777,607 B | 16,019,695 B |

This is a Windows SQLite-API run, not POSIX directory-synchronization evidence
or a joint rerun of the segment engine. The earlier directory ratios are
policy-specific; these measurements do not establish a new cross-engine ranking.
Reproduce from the artifact root with:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B -m experiments.maintenance_control --output /tmp/maintenance-control.json
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest tests.test_maintenance_control -v
```

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

The historical aggregator writes `historical-values.tex`; that file preserves the old historical macro surface but is not an input to the current manuscript. `generate_paper_values.py` independently derives the manuscript's bounded-history, joint-history, retained-row, and campaign-bound macros from retained evidence. Its output must match the decoded text of `results/current/paper-inputs/paper-values.tex` and the paper-side `paper-values.tex`; these text comparisons normalize line endings. The other four paper inputs are likewise retained under `results/current/paper-inputs/` and regenerated from the accepted observations plus the complete passing test transcript. The package verifier fails closed on a mismatch.

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

The retained-data integrity workflow uses a 180-second whole-check timeout and
always attempts to upload raw logs. It runs reconstruction and data-only/finite
checks. The separate scientific workflow also provides the complete owned
correctness entry point and the bounded native lifecycle job described above;
their execution records and evidence scopes are distinct.

## Current executable cases

The historical guarded runner defines named cases for tests, histories, scale comparison, and attempted order control. Its lifetime allowance is exhausted: `run_bounded.py` must not admit another run or reset `resource-accounting.json`. Data-only reconstruction remains usable.

For a separately authorized **new finite correctness run**, the flat artifact repository has one fixed entry point:

```sh
PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 python -B -m experiments.fresh_run \
  --authorize-new-run --out /tmp/p021-owned-checks
```

The destination must be absent and outside the deliverable. This does not reuse the old allowance. It reserves the entire new run before launching, retains failed reservations without refund, checks the old ledger and source bytes afterward, and leaves all raw output in that destination. Six sequential stages cover the complete test inventory, finite histories, owned SQLite and strict-export cases, owned process exits, the joint history, and retained-data reconstruction. There is no scale/throughput matrix, external source-program execution, or downloaded experimental input. See `PLATFORM.md` for limits and accounting scope.

The new Windows diagnostic exercises all test discovery, but is not a passing full-suite result: 24 of the existing 132 methods and all eight new methods pass; ten existing rooted-image methods error on file synchronization and 98 existing methods cannot be imported because their modules require `fcntl`. There are no skipped methods or assertion failures. The separate finite stage checks 775 histories, 2,925 states, and 7,750 abstract cuts. The owned SQLite stage matches eight persisted endpoints and an actual reader spanning another connection's commit, rejects six malformed databases and nine malformed exports, and accepts its valid export control. Six owned SQLite process exits cover WAL and DELETE at before-commit, after-commit, and after-close. These are process-exit and SQLite-API results with the operating system alive; the owned SQL bootstrap does not test the adapters' directory synchronization. Segment publication/recovery and the joint history remain unexecuted on this host. No synchronization or locking mechanism was replaced to obtain these results.

`.github/workflows/scientific-checks.yml` prepares that same finite entry point on Ubuntu 24.04 from the **flat artifact root**, on main pushes or explicit dispatch. It retains a failing job exit and uses an always-run raw-output upload. Its native lifecycle job has the reviewed successful run identified above; this does not retroactively certify a different full-suite source. The older data-only integrity workflow remains separate.

The experimental launcher uses one child, bounded address space, a wall watchdog, concurrent-tree monitoring, and a parent-death signal on supported systems. Monitoring is a bounded operational guard, not a proof of continuous peak memory or progress through uninterruptible kernel I/O.

## Resource accounting

Unknown inherited scientific use is conservatively charged to the full non-reserved allocation rather than treated as zero. Failed and interrupted attempts consume their complete reservations. The historical campaign CPU upper bound reaches 28,800 seconds, with no remaining allowance. The two order-control attempts are retained and excluded from timing inference. See the unchanged `resource-accounting.json` and historical per-case accounting files. New finite-run reservations and observed use are recorded only in each external destination's `run.json` and per-stage accounting; they do not alter historical measurements or replenish that lifetime allowance.

## Separate read-lease / full API lifecycle control

`experiments.history_lifecycle` and its separately invoked
`experiments.history_lifecycle_tests` compare two archive-retention policies on
the same ordinary normalized writer, not three native storage engines. At 8/48,
64/384 and 500/3,000 sources/facts, both preserve one bootstrap read transaction
through six of twelve updates. All-history retains thirteen endpoints; leases
retain current plus the live bootstrap and collect only released archives.
WAL/FULL, zero automatic checkpoint and busy timeout are recorded. Archive
images are closed in DELETE mode and file contents are synchronized; atomic
pair publication and POSIX directory synchronization are outside this API stage.

The corrected maintenance script also converts closed archive images to DELETE
mode, performs actual obsolete-pair deletion, verifies the remaining database/
export endpoint and records removal time and remaining files. Historical rows
are not overwritten or relabeled as an execution of this correction.

The new cohort under `results/review-lifecycle-20261014/` retains six complete
input/output gzip documents (90 event records), all 36 checkpoint results,
72 update inputs/current outputs, 36 held outputs and six close/reopen outputs.
Raw replay and native re-decoding of all 42 remaining archive pairs have zero
discrepancies. Actual lease-directed collection removes 72 archive files. Six
PASSIVE status-zero results remain incomplete and six held TRUNCATE calls busy;
six release TRUNCATE calls complete. All DB/WAL/SHM and archive file lengths are
counted; live WAL/SHM files are never manually removed.

From the artifact root, with assertions enabled and a fresh owned output:

```text
python -B -m experiments.history_lifecycle --authorize-new-study --out <fresh-output-directory>
python -B -m unittest experiments.history_lifecycle_tests tests.test_maintenance_control tests.test_exhaustive tests.test_sql_integer_domain -v
```

Both the command and the imported case function reject optimized Python before
creating an output. The lifecycle tests allocate fresh temporary directories
under the project root; they do not require a caller to redirect the global
temporary directory. WAL setup, archive journal-mode selection and the initial
reader snapshot are executed explicitly.

The 14-method portable subset passes; it is not a new full 140-method POSIX run.
The earlier Linux transcript and all old performance/result files remain
unchanged. A new reservation, failed first attempt and final observations remain
private and do not reset the exhausted historical resource ledger. Detailed
contract and standard-mechanism overlap are in `proofs/lifecycle-contract.md`.
That API cohort does not itself close the common native-persistence/history/
audit-lease comparison; the separate `results/native-fanout` cohort supplies
the scoped native evidence. One fixed-order repetition per size/policy still
supplies only descriptive API costs, not device traffic, statistical engine
ranking or new-store novelty.

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

## Manuscript

When the paper accompanies this artifact, its single entry is `paper/main.tex`
and its compiled manuscript is `paper/main.pdf`. The shared journal text, data,
figures and bibliography are inputs to that entry. Build instructions are in
`paper/README.md`. The source check rejects a second root-level manuscript entry
while allowing included text fragments:

```sh
python -B -m unittest tests.test_primary_wrapper -v
```

## Manuscript schematics

The endpoint-selector and representation diagrams are generated directly in
Python as vector PDFs with embedded fonts. They encode the defined relationships;
the stored engine and lifecycle measurements are unchanged. With Python 3.9+
and Matplotlib, run from the artifact root:

```sh
python -B figures/draw_schematics.py --output figures
```

## Portable path notation

Published execution records use `<PYTHON_ROOT>`, `<NODE_ROOT>`, `<RUNTIME_ROOT>` and `<PROJECTS_ROOT>` for machine-local installation and workspace prefixes. These are path aliases, not literal directories; substitute the corresponding local roots when replaying a command. Recorded interpreter and dependency versions, inputs, measurements and outcomes are unchanged. File inventories bind the published portable serializations; any `recorded_` identity denotes the pre-normalization bytes.
