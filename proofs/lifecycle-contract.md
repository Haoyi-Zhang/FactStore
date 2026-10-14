# History, maintenance and the ordinary transaction baseline

## Exact observation and retention obligations

At event t let c_t be the current complete committed endpoint, and H_t the finite
set of endpoints acquired while current and still held by live handles. Required
observations are c_t on a new acquisition after acknowledgement, and the captured
endpoint h on every repeated materialization of a handle for h in H_t. Handle
release removes that obligation; it does not retrospectively revoke private
values already materialized. The basic logical contract does not require an
unbounded disk archive or reopening an arbitrary past endpoint after release.

For a comparison that additionally promises independently readable historical
disk bytes, fix the same audit-lease set L_t and interpretation rule for every
representation. Each selected disk object for c_t or h in L_t must remain
available and decode to its exact endpoint. Reclamation may remove only objects
outside this protected set (and unreferenced dependencies of selected chains).
An all-history policy meets these obligations while retaining extra endpoints;
it is not an equal-retention space baseline for a current-plus-leases policy.
Capacity admission/backpressure, compaction epochs, audit cadence, failed-return
rules and conditional persistence premises must also be matched before ranking.

## Standard sufficiency argument

Under the relational theorem in `representation.md`, every accepted serialized
write gives the same endpoint and a complete read transaction gives one committed
snapshot. SQLite WAL readers fix their end mark on the first read and retain it
until the transaction ends. Thus a current-only read transaction or materialized
value supplies the basic logical history contract without a special store.
Holding a database read transaction can delay checkpoint progress; that cost is
ordinary snapshot retention, not an additional specialized-engine guarantee.

In the owned archive control, a backup occurs with no concurrent writer; its
closed database image and export are checked against the same prepared endpoint.
For quiescent API reads, preserving both files for current and leased endpoints
therefore preserves the required observations. Deleting only recorded, unleased
archive pairs cannot change any protected decode. This is the ordinary
reachability/pinning argument, not a new collector or durable-selection proof.
The control does not synchronize directory entries or atomically publish a pair
through a root, and consequently is not an execution of SQLite-R or FrontierStore.

## What must be charged

Use one publication/lease/release history and report bootstrap, logical transition
construction, commit/publication, required reads/audits, history creation,
checkpoint/compaction, reclamation, close and fresh reopen. Report cumulative
operation time and event-sampled regular-file lengths separately from one final
directory sample. Include DB, WAL, SHM, exports, current roots, obsolete/pinned
objects and temporary files where present. File length is not device bytes
written or an absolute physical-space lower bound. A successful PASSIVE status
alone is not full checkpoint completion: compare WAL frames with frames moved
back; successful TRUNCATE additionally reports zero frames and a zero-length WAL.

The earlier maintenance script computed its latest-pair column from two file
sizes without performing deletion. Read-only opens of WAL-mode backups can also
leave per-image WAL/SHM sidecars. The corrected control converts closed archive
images to DELETE mode using SQLite, actually removes obsolete pairs, checks the
remaining endpoint through database and export decoders, and reports collection
time and the entire remaining file set. It never unlinks a live WAL/SHM file.
Old rows remain unchanged, with their pair-content rather than reclaimed-directory
meaning stated explicitly in the manuscript.

## Fresh evidence and unresolved cross-engine comparison

The 2026-10-14 separate SQLite-API cohort has six cases at 8/48, 64/384 and
500/3,000 sources/facts, two archive policies, and twelve identical updates per
policy. Both hold a bootstrap read transaction through six updates, then release
it. All-history retains thirteen endpoints; lease-directed collection retains
current plus the live bootstrap and ends with one endpoint. All 72 current update
outputs, 36 held outputs, six close/reopen outputs and 42 final archive pairs
match their complete endpoints. The replay counts are not new proofs or novelty.
Six PASSIVE results report status zero while incomplete; six held TRUNCATE calls
are busy; all six release TRUNCATE calls complete. All 36 checkpoint observations
are retained, with 90 full raw event records and 72 actual archive-file deletions.

Only the API stage ran on Windows. Neither POSIX directory synchronization nor
fcntl locking was stubbed; the segment engine was not run. A native same-history,
same-persistence FrontierStore/SQLite-R/SQLite-N lifecycle comparison remains
unclosed and must include the stronger audit-lease interface where claimed.
Current timings are one descriptive repetition in fixed policy order with page
cache retained, not a statistical ranking, deployment benefit or device result.

Primary API sources read for this argument (2026-10-14):

- https://www.sqlite.org/isolation.html - complete page, separate connections,
  snapshot behavior and same-connection caveats.
- https://www.sqlite.org/lang_transaction.html - complete page, Sections 2.1-2.3
  read versus write transactions, deferred first read, implicit transactions.
- https://www.sqlite.org/wal.html - complete page; Sections 2.1-2.3 and 6 supply
  checkpoint/end-mark/reader-gap premises; Section 5 discusses closed read-only
  archives. No library vulnerability reproducer or modified engine was used.
- https://www.sqlite.org/c3ref/wal_checkpoint_v2.html - complete API page,
  PASSIVE/FULL/RESTART/TRUNCATE and frame outputs.
- https://www.sqlite.org/pragma.html - only relevant sections wal_autocheckpoint
  and wal_checkpoint (not a full read of all PRAGMAs), including three-column
  result semantics. These are API premises, not experimental datasets.
