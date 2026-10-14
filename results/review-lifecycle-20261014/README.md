# Separate ordinary SQLite read-lease and lifecycle control

This cohort uses one repetition per size and policy. The active
normalized writer is unchanged. Scales are 8/48, 64/384 and 500/3,000 sources/facts;
each of the two policies uses the same twelve updates and required bootstrap
reader lease, released after update six. All-history retains thirteen archive
endpoints. Lease-directed collection retains current plus the live bootstrap,
and ends with one archive endpoint. This is a policy comparison inside an ordinary
SQLite API control, not a same-native-persistence three-engine comparison.

`summary.json` retains runtime/clock settings and every checkpoint/footprint;
six `sources-*.json.gz` documents retain complete bootstrap inputs, every
transaction, current and held outputs, file inventories and removed filenames.
`verification.json` records full raw replay and actual re-decoding of all final
archive database/export pairs. The shared logical model is used in replay; this
does not constitute an independently authored transition theory.

The package validator decodes each of these six declared gzip JSON records
with a 32 MiB per-file bound, checks the complete twelve-update sequence and
typed counts, and matches every aggregate field to the corresponding summary.
Other compressed bundles remain disallowed. This is data-format and
record/summary checking, not an additional native experiment.

Actual counts: 6 cases, 72 update inputs/current outputs, 36 held update outputs,
6 close/reopen outputs, 90 event records, 36 checkpoint results, 42 final archive
pairs re-decoded, 72 archive files actually removed, zero discrepancies.
`finite.json` separately replays the existing 775-history/2,925-state/7,750-cut
abstract model, with zero violations; it is not a native filesystem crash test.
`units.stderr.log` retains 14 passing selected methods (including four separately
invoked new control methods), not a passing new 140-method POSIX suite.

The initial failure log is retained. Twelve of fourteen selected methods passed;
two detected unexpected WAL/SHM sidecars from read-only opening of WAL-mode archive
backups. The corrected control converts the closed archive to DELETE mode through
SQLite before closing. It also corrects the old maintenance script's computed
last-pair value into actual deletion plus retained-pair verification and removal
time. No live WAL/SHM is manually unlinked, and no native synchronization or
locking routine is stubbed. Initial and intermediate attempts are not merged
into the final cohort or timing rows.

The earlier maintenance JSON and all other old data remain byte-unchanged. Its
latest-pair column was a pair-content sum, not actual latest-only collection;
those columns did not inventory archive sidecars. The paper corrects this meaning
without replacing old figures or attributing new timings to the old source.

## Descriptive full API costs

| Sources | Policy | Final files (bytes) | Event-sampled maximum (bytes) | Complete API ms | Checkpoints ms | Removal ms |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 8 | all | 777545 | 909465 | 170.44 | 2.61 | 0.03 |
| 8 | leases | 144114 | 401491 | 157.47 | 2.59 | 6.24 |
| 64 | all | 3148476 | 3721188 | 469.35 | 3.19 | 0.03 |
| 64 | leases | 426915 | 1616101 | 466.10 | 3.56 | 6.16 |
| 500 | all | 19126103 | 20448847 | 3126.32 | 7.09 | 0.03 |
| 500 | leases | 2344585 | 6875451 | 3488.58 | 7.07 | 9.61 |

The complete interval includes bootstrap and state construction, validated delta
commit, fresh and held reads, content-flushed archive backup/export/checking,
actual archive collection, checkpoint calls, shutdown and fresh reopen. Raw-log
serialization is excluded; other witness/inventory work is included. Component
times are selected subintervals, not an exhaustive partition. No winner is
inferred from one fixed-order repetition with retained page cache.

Six held PASSIVE results have status zero but fewer checkpointed than logged
frames. Six held TRUNCATE results are busy; after release all six TRUNCATE calls
complete. At 500 sources both policies have 2,735,296 active DB/WAL/SHM bytes
before release maintenance and 831,488 afterward. After final close/reopen,
884,736 active-database/sidecar bytes remain and are included in the totals.
This last read can create zero-WAL/SHM bookkeeping; it is not omitted from size.
File lengths are not allocated-device space, cumulative device traffic, flash
write amplification, or an absolute storage lower bound.

Host fields were actually queried: i7-12700KF (12 cores/20 logical processors),
Windows 11 build 28000, NTFS. Runtime is Python 3.12.14, SQLite 3.53.1; source ID
and full clock implementations/resolutions are in the summary. CIM fields are
in `environment-host.json`. These are not backfilled into inherited Linux runs.

## Reproduction and remaining contract

From `artifact/`, assertions enabled, with a fresh output under the candidate or
private P021 root and TEMP/TMP/TMPDIR confined to P021:

```text
python -B -m experiments.history_lifecycle --authorize-new-study --out <fresh-owned-output>
python -B -m unittest tests.test_maintenance_control tests.test_exhaustive tests.test_sql_integer_domain experiments.history_lifecycle_tests -v
python -B -m experiments.exhaustive --output <owned-output>/finite.json
```

The outer private runner records separate nonrefundable new reservations and
enforces child wall timeouts. It does not reset the exhausted old ledger or
claim a hard Windows CPU/address-space bound or a measured aggregate memory peak.
The new primary-source argument and exact retention contract are in
`proofs/lifecycle-contract.md`.

Directory-entry synchronization and atomic pair/root publication are not supplied
by this Windows API stage. No FrontierStore/SQLite-R native lifecycle was run.
The outstanding discriminating comparison must match current/historical audit
leases, conditional durable publication, compaction epochs, audit cadence and
quiescent reclamation, charging all DB/WAL/export, checkpoint and collection
costs. Ordinary transaction/snapshot/backup/retention mechanisms are not new
storage algorithms; generated counts establish neither priority nor admission.
