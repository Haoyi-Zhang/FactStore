# Reviewed native fanout/cadence cohort

This is the actual three-representation Linux campaign from GitHub Actions run
40066415944, job 120790520723, commit
`f12a27c375ede02ebd3a12443485ebea815df837` (2026-10-24).
The uploaded artifact is 12549104460; its downloaded ZIP SHA-256 is
`54a6411b32062ab724723bc0191496f782f58dc7d87ce4eda70a68cc062e9ff1`.
`origin.json` maps all 169 original archive members to their byte-preserved
local paths and binds that reviewed origin. Execution logs are in `execution/`.
The full driver/engine/workload/ledger preimage is preserved byte-for-byte as
nine `source-preimage/<original-path>.txt` inputs; these are archival text, not
alternate executable modules. Source-before and source-after are original CI
records, not rebound to publication edits.

The native writers ran on Ubuntu 24.04.5, Linux 6.17.0-1022-azure, ext4,
Python 3.12.15, SQLite 3.45.1, on an Intel Xeon Platinum 8573C with four available
CPUs. The complete observed environment, clocks, SQLite source ID/options,
memory, affinity and native fsync/replace/flock probe are retained. The campaign
used a 1.5 GiB address-space limit and a bounded fresh runner-temp destination.
Mount options, physical storage and virtualization details remain unknown;
ordinary page cache was retained and swap was configured, not excluded.

There are 36 cases: 64 sources, 384 initial facts, dependency widths 2/8,
scheduled audit intervals 1/4, three repetitions and three native arms.
All six engine permutations occur twice across 12 paired blocks; every engine
occupies every slot once per width/cadence. The exact ordered input stream is
equal across engines, repetitions and cadences within each width. Across all
cases, replay checks 288 updates, 72 matched maintenance epochs, 180 scheduled
audits, 144 post-collection held reads and 36 orderly reopens with zero mismatch.
Bootstrap/final acceptance audits are additional to the 180 scheduled audits.

Each trace retains ten full input/output events, phase nanoseconds, physical
selectors/protected files, current and held endpoints, checkpoints, collection,
release and sampled footprints. Final physical stores are retained and decoded
again by the consumer. The bootstrap lease spans update four and its maintenance
and collection; it is then released. Maintenance advances all arms to the same
empty logical epoch at updates four/eight. SQLite-N uses native FULL/WAL and
SQLite's own read transaction/checkpoint lifetime. SQLite-R uses its unchanged
durable pair publication and a minimal single-owner, confined pair collector.
The common boundary is quiescent post-return durable current/leased equality.
SQLite-N has no independent export or atomic cross-interface selector. No
power cut, concurrent writer, cross-process lease or device-traffic measurement
is supplied by these results.

From the artifact root, data-only verification and table generation are:

```sh
python -B -m experiments.native_fanout_results --check results/native-fanout --tex ../paper/native-fanout.tex
python -B -m experiments.native_fanout_results --check results/native-fanout --check-tex ../paper/native-fanout.tex
python -B -m unittest tests.test_native_fanout_results -v
```

The consumer checks exact original-member bytes, all nine current/preimage
source identities, bounded strict JSONL decoding, ordered transaction replay,
all endpoints/schedules/phase sums, final physical stores, and exact equality
with the uploaded verified summary. It regenerates the table from raw case
totals through the unchanged native verifier. This import/replay on Windows
is not a native execution or a Windows persistence surrogate.

Seven methods in the separate `tests.test_native_fanout_results` module check
this consumer, tampered raw/preimage/summary/order inputs, strict bounded JSONL,
unknown compressed neighbors and a byte-exact owned Git checkout. Full-suite
discovery executes these methods. The baseline split excludes their identifiers
only from the frozen 132-method historical count, never from execution; the
existing twelve compressed-record method identifiers remain unchanged.

SQLite-N has the lowest median lifecycle wall time in all four configurations.
FrontierStore is lower than SQLite-R at width two; SQLite-R is lower at width
eight. Sparse audits reduce lifecycle and audit totals in each arm. These are
three-repetition, single-host implementation results, not a universal ranking.
Wall time includes bootstrap, trace instrumentation and checks; separately
aggregated phase medians do not sum to the median wall. All phase totals,
including collection, release and reopen omitted from the compact table, remain
in `verified-summary.json` and raw per-transition traces.

The older `sqlite-fanout` Windows API study and fixed-order three-scale results
remain separate with their original values, scope and negative findings.
The first failed native artifact and its replay failure remain separate private
review evidence; none of its generated cases is relabeled as accepted data.
