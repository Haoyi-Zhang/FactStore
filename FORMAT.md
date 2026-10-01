# FrontierStore durable format

## 1. Directory and visibility rule

A store directory contains a canonical `ROOT` file, a `segments/` directory,
and a writer-lock file. Only `ROOT` selects query-visible state. A segment that
exists under its final name but is absent from the selected list is an orphan;
it is not consulted during open, query, recovery, or independent checking.
Temporary files begin with a dot and are never valid selected paths.

`ROOT` is one canonical JSON object with these fields:

- `format`: `frontierstore-root`;
- `epoch`: the positive epoch of the selected tip;
- `schema`: the active positive schema;
- `counts`: exact `sources`, `facts`, and `reverse` live counts;
- `segments`: an ordered nonempty list of relative final segment names.

A selected name must match `segment-NNNNNNNN.jsonl` and resolve inside the
store's `segments/` directory. The first selected segment is a base. Every later
selected segment is a delta whose parent is the preceding selected epoch and whose epoch is exactly
one greater. A segment filename encodes its header epoch. The
last selected epoch and schema must equal the root fields.

## 2. Segment framing

Each segment is newline-delimited canonical JSON. It begins with one header,
contains zero or more typed records, and ends with one footer.

The header fields are:

- `kind`: `header`;
- `format`: `frontierstore-segment`;
- `epoch`: positive segment epoch;
- `parent`: null for a base or the immediately preceding selected epoch for a
  delta;
- `mode`: `base` or `delta`;
- `schema`: the logical schema after applying the segment.

The footer fields are:

- `kind`: `footer`;
- `records`: exact number of records between header and footer;
- `counts`: exact live counts after the segment is folded.

The online loader and the independent checker reject absent framing, malformed
JSON, wrong field types, record-count disagreement, or live-count disagreement.

## 3. Typed records and phase order

A base enumerates complete live state in this phase order:

1. `source_put`;
2. `fact_put`;
3. `reverse_put`.

A delta uses this phase order:

1. `source_put`;
2. `source_del`;
3. `fact_del`;
4. `fact_put`;
5. `reverse_put`;
6. `reverse_del`.

Identifiers are unique within each record class in a segment. Record meanings
are:

- `source_put`: identifier, positive generation, and text payload;
- `source_del`: identifier to remove;
- `fact_del`: fact identifier to remove;
- `fact_put`: identifier, text payload, active schema, and a nonempty sorted
  dependency list of `{source, generation}` pairs;
- `reverse_put`: live source identifier and a sorted duplicate-free fact list;
- `reverse_del`: source identifier whose inverse entry is removed.

A base resets all logical maps before its records are applied. A delta replaces
or deletes only named keys. The ordered phases give a single interpretation to
a same-identifier fact replacement: old membership is removed before the new
fact is inserted.

## 4. Closure checked after every selected segment

After folding each selected segment, the following conditions must hold:

1. every fact dependency names a live source at exactly its visible generation;
2. every fact carries the segment's resulting schema;
3. the reverse-map domain equals the live-source domain;
4. a fact appears in a source's reverse list exactly when its dependency list
   names that source;
5. reverse lists and dependency lists are canonical and duplicate free;
6. footer counts match the reconstructed maps.

The root repeats the final epoch, schema, and counts. Checking every selected
prefix exposes an invalid intermediate publication rather than allowing a later
delta to conceal it.

## 5. Publication sequence and named termination boundaries

An ordinary update constructs its post-state before writing. It then performs:

1. create a temporary segment and write its header;
2. write typed records;
3. write the footer;
4. synchronize the temporary segment file;
5. atomically assign the final segment name and synchronize `segments/`;
6. write a temporary root naming the extended selected chain;
7. synchronize the temporary root;
8. atomically replace `ROOT`;
9. synchronize the store directory;
10. install the new in-memory state and selected tuple, then return successfully.

Each raw write completes its entire byte sequence using a checked progress loop.
A zero, invalid, or missing progress count is an error; synchronization is never
used as a substitute for a missing suffix.

The retained failure campaign terminates a fresh process at ten named positions spanning
segment construction, segment durability, root construction, root replacement,
and root-directory synchronization. Before root replacement, `ROOT` still
selects the old chain. At or after replacement in the process-termination model,
it selects the complete new chain, whose new segment was already synchronized
and named. The final directory synchronization is the persistence step; successful return after
in-memory installation is the acknowledgement boundary.

A retry after an old-root recovery may encounter a final orphan with the next
epoch name. Because no current or pinned root selects it, the writer removes it,
synchronizes the segment directory, and reuses the epoch. A selected or pinned
segment is never overwritten.

## 6. Compaction and collection

Compaction writes a base segment containing the complete current source, fact,
and reverse maps, then publishes a root whose selected list contains only that
base. Source generations and fact dependencies are unchanged; only the physical
epoch and selected representation change. Collection preserves every segment in
the current selected list and in every live in-process snapshot pin, and removes
only other final or temporary files.

If policy permits at most `K` acknowledged deltas after a selected base, open
and complete checking process at most `K + 1` selected segments. The format
alone does not impose that policy. Without compaction, a safe chain may grow
with update history.

## 7. Reader, failure, and finite-name premises

Only one live instance owns a directory. The local state/segment tuple is
captured atomically at snapshot acquisition; an old live pin remains historical.
Acquisitions invoked after a successful update return select that epoch or a
later one. A lock file does not synchronize cached state or pins across instances.
Any exception from the guarded byte-publication phase permanently disables new
current-state operations, including collection, on that instance. Abandon it
and quiescently reopen. Existing handles retain in-memory values; their pins do
not transfer to a reopened instance. Prewrite validation rejection does not
disable the unchanged instance. Closing one handle checks and releases its pin
under the instance lock, even when that handle is closed concurrently.
Opening or independently checking disk bytes requires no concurrent publisher
or collector. Named process exits leave the operating system alive.

Source generations satisfy 0 < g <= selected epoch, independently checked by
both parsers. A changed source must be stamped at the new epoch. Precomputed
deltas are captured, folded, checked for closure, and compared against the
complete supplied next state before publication. Duplicate delete identifiers
and conflicting source/reverse phases are rejected; fact replacement may
legitimately delete and reinstall the same fact.

Canonical segment names encode exactly eight epoch digits. Publication rejects
an epoch outside 1 through 99,999,999 before writing any next segment. The K+1
selected-file bound does not bound total bytes, parsing/checking CPU, root bytes,
or obsolete histories retained by pins.

## 8. Preflight capture and mutation boundary

Bootstrap input is privately captured and closure-checked before an existing target directory is removed. For every update adapter, the proposed endpoint and delta are privately captured; the six-phase fold over the current endpoint must reconstruct the supplied next endpoint exactly, and the next epoch and changed-source stamps must agree, before persistent mutation begins. Rejection at this preflight boundary leaves the previously selected durable state unchanged. This rule does not make replacement of an existing whole directory atomic after an I/O failure.

## 9. Decoder predicates and relational materialization

Counts have exactly the three named families and nonnegative integer values;
Booleans and floats are not integer counts. Delta parents are strict positive
integers. All record and dependency identifiers use the model's bounded grammar.
Duplicate JSON object members are rejected, and mode/kind types are checked
before dispatch. A delta source record differing from its previous value must
carry the delta epoch, even when its resulting state would otherwise be closed.
No-op source puts may retain their previous stamp. These checks are independently
implemented rather than imported from the writer by the offline checker.

The normalized relational comparator materializes metadata, sources, facts, and
dependencies in one read transaction. One SQL write transaction is not enough
when an observation uses several completed autocommit SELECTs. See
`proofs/representation.md` for the exact logical reduction and a closed but
nonendpoint counterexample. The normalized reader and its closed-nonendpoint regression are executed in the
current 114-method suite. The accepted comparison materializes every normalized
observation in one read transaction; all accepted durable decodes equal the
shared logical oracle.


## Abstract publication evidence and timed rooted-audit scope

The current bounded publication record uses `abstract-selector-object-v1`.
For each abstract cut, the checker constructs a complete-object map and visible
selector, materializes the selected object, then applies a separately defined
old/new cut oracle and closure checks.  A closed mixed observation must fail with
`NON_ENDPOINT_OBSERVATION`; a complete new object selected before root replacement
must fail with `CUT_ENDPOINT_MISMATCH`.  This is an abstract selector model, not
a filesystem or device-failure model.

The retained SQLite rooted-audit timing path performs two full selected-pair
reads per sample.  Each read decodes the SQLite image, parses the canonical
export, and compares the complete values.  The wrapper then compares the first
decoded value with the logical oracle.  Existing timing rows retain that
specific double-pass meaning.

All current Python sources are parsed read-only with `ast.parse`; a syntax error
in any unimported `.py` file fails verification.  The current complete executable
suite is the retained 114-method transcript.  Earlier test counts remain only in
explicitly historical records.

