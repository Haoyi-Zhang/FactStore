# Representation, observation, and publication obligations

These are finite hand arguments about the stated interfaces. Selected premises
and counterexamples are exercised by the 114-method suite and bounded histories,
but that execution does not turn the arguments into mechanized verification or
establish a new storage principle.

## 1. Transactional relational reduction

Let a well-formed closed state be C=(e,sigma,S,F,R), with the definitions and
source-generation horizon in `model.md`. Define E(C) by four relations:

* M contains the two pairs (epoch,e) and (schema,sigma).
* Srel contains (i,g,x) for each S[i]=(g,x), with primary key i.
* Frel contains (f,p,sigma_f) for each fact, with primary key f.
* Drel contains (f,i,g) for each (i,g) in that fact's dependencies, with primary
  key (f,i).

The decoder groups Drel by fact, sorts each dependency list, and reconstructs
R[i] as the sorted set of facts whose rows name i, for every source i, including
sources with no dependents. Counts are derived rather than stored separately.
The domain is the image of well-formed closed states, not arbitrary SQL data.
The reference wrappers disable foreign-key enforcement and trust valid prepared
transitions. This argument does not silently give those wrappers an untrusted
input validation interface.

### Lemma A: exact encoding on the closed-state domain

D(E(C))=C. Each source and fact appears in exactly one primary-key row. Grouping
Drel reconstructs its canonical dependency list, including every generation.
Metadata restores e and sigma. By exact inverse closure, recomputing R recovers
its original value, including empty lists. No two closed states have the same
encoding: decoding that common encoding would recover both. Thus E is injective
on this domain even though R is not a separately persisted relation.

### Lemma B: faithful lowering of a valid delta

Take one next-epoch delta whose six-phase fold over C equals closed C'.  Let I
be the logical invalidation set, let N be the replacement map, and define
J = I union dom(N).  Exclude source put/delete and reverse put/delete overlap as
the declared adapter does.  Inside one SQL write transaction, delete every fact
row and complete dependency group named by J; apply the source edits; insert
every replacement fact, payload/schema row, and complete new dependency group;
finally replace e and sigma.  The resulting tables are E(C').

For sources, disjoint puts/deletes commute, and untouched rows are retained.
For facts outside J, the old row and dependency group are unchanged.  Every fact
in I is removed unless N supplies its replacement.  Every fact in dom(N) first
loses its complete old dependency group even when I is empty, then receives the
replacement group.  This prevents residual edges when a fact changes only its
dependency set.  For example, with live a@1 and b@1 and p initially depending on
a@1, a replacement of p that depends on b@1 has I empty but p in J; the final
D relation contains only (p,b,1), and the derived inverse has R[a]=empty and
R[b]={p}.  Closure of C' determines R' uniquely from S' and those groups;
independent reverse updates need no separate table.  The metadata updates supply
the exact post-state epoch/schema.  These component equalities prove the assertion.

Schema cutover is included: all old facts disappear or are replaced at the new
schema. Source retraction and later reintroduction are included provided C'
obeys the new-generation rule. A logical compaction observation can be represented
by changing only the publication epoch; this does not emulate physical segment
selection, pins, or reclamation.

The delivered SQLite-N wrapper applies a shared precomputed delta; it does not
independently derive the invalidation set with the SQL join used in this existence
argument. The executable path therefore checks atomic application of a validated
delta and one-snapshot materialization. Join-based invalidation derivation and its
cost remain theorem-level rather than separately executed or timed evidence.

### Theorem A: endpoint observations through a transactional encoding

Assume (i) one serialized writer accepts only the valid deltas above; (ii) its
write transaction commits atomically; (iii) all statements materializing one
observation read one transaction snapshot; and (iv) a read transaction begun
after a writer's successful return observes that commit or a successor, rather
than reusing or selecting a predecessor transaction. Then materialized logical
observations equal complete committed endpoints, are closed, and satisfy the
same current-acquisition freshness and private historical-value stability
requirements as the logical FrontierStore interface.

Induct on committed writes using Lemmas A and B. A read transaction selects one
committed database state, so all four relations decode to one C_j; closure and
the source horizon follow from the induction hypothesis. By premise (iv), a fresh read started after acknowledgement cannot
select a state preceding that committed write.
Any later update to a changed source receives a still later epoch, and a later
retraction removes its dependent facts. Thus live dependencies on a source
changed by that acknowledgement cannot refer to an earlier generation. A
materialized private value remains equal to the selected C_j after its read
transaction ends. This does not promise indefinitely retained database pages
or transferable disk pins.

Durable-prefix behavior is conditional on the underlying engine's transaction
recovery contract, not proved by this encoding. The encoding also does not
supply an independent parser of database physical pages or the selected-file
bound. Consequently the logical guarantees alone cannot distinguish FrontierStore
from a correct transactional composition. A meaningful storage contribution would
still require a distinct representation/checker contract or evidenced operational
advantage. This is a reduction of the stated semantics, not a universal claim
that no specialized store can be worthwhile, and not a new benchmark result.


## 2a. Independent abstract publication/selection check

For each final transition in the declared finite history universe, the bounded
checker represents publication as a map from object names to complete immutable
logical states plus a selector naming one object.  It replays abstract events:
the new object becomes available only after the footer event, while the selector
changes only at root replacement.  Materialization follows the selector; it is
not assigned from the expected endpoint.  A separate explicit cut oracle then
requires the old endpoint before root replacement and the new endpoint from root
replacement onward, in addition to closure and endpoint-membership checks.

The deterministic validation retains two negative cases.  First, the legal
two-source construction below yields a dependency-closed value equal to neither
endpoint and is rejected as NON_ENDPOINT_OBSERVATION.  Second, forcing the new
selector before root replacement selects a complete closed endpoint but is
rejected as CUT_ENDPOINT_MISMATCH.  The 7,750 observations therefore check the
abstract selector rule and endpoint identity under this finite model.  They do
not model torn bytes, write-cache ordering, or device failure.

## 2. Proposition 1: closure does not imply endpoint identity

The following is a symbolic two-source, one-fact schedule, not a measured run.

C0 has e=1, schema=1, S[a]=(1,"stable"), S[b]=(1,"old source"),
F[p]=("old fact",1,[(a,1)]), R[a]=[p], R[b]=[]. A writer's valid C1 at e=2
changes b to (2,"new source") and explicitly replaces p by
("new fact",1,[(a,1)]). The source a, dependencies, schema, and reverse map do
not change. Both endpoints are closed and satisfy the horizon. The fact's
explicit replacement is legal even though its dependency source did not change;
payloads are uninterpreted.

A reader first completes the metadata SELECT and then the source SELECT at C0.
The writer now atomically commits C1. Without an enclosing read transaction,
the reader completes its fact and dependency SELECTs at C1. It materializes H:
old e and S, new fact payload, and the exact common inverse relation.

H is closed: the only dependency remains a@1; its schema matches; both inverse
lists are exact; all counts and the source horizon hold. Yet H differs from C0
in p's payload and from C1 in e and b. The discrepancy also persists when the
observation omits the epoch, because b and p differ on opposite sides. Thus a
closure assertion cannot serve as an endpoint-atomicity oracle.

The SQLite wrapper formerly used precisely these four independent completed
SELECTs. It now begins one explicit read transaction before reading metadata and
closes it after materialization. In WAL mode, the first read fixes the snapshot.
The deterministic regression commits through another connection between the
source and fact statements and requires C0, followed by C1 on a fresh read. That
case passes in the current 114-method suite. The current performance harness reads
for equality after each timed write with no overlapping writer; all accepted
normalized decodes equal the shared logical endpoint.

Official API premises: SQLite, "Isolation In SQLite", sections "Isolation And
Concurrency" and "Summary", https://www.sqlite.org/isolation.html; SQLite,
"Transaction", sections 2.1 and 2.3, https://www.sqlite.org/lang_transaction.html.
Read on 10 September 2026. No library internals are modified.

## 3. Preflight capture and rejection atomicity

A frozen dataclass does not freeze caller-owned maps or dependency lists. Persistent entry points therefore first copy the complete supplied bootstrap or endpoint and validate closure on that private value. Update entry points additionally copy the supplied delta, fold its six phases over a private current endpoint, require exact equality with the proposed next endpoint, enforce unit epoch advance and changed-source stamping, and only then begin durable mutation. Create with replacement performs this preflight before deleting an existing target.

The executable regressions give every persistent adapter a deliberately inconsistent endpoint/delta pair and require both rejection and an unchanged durable decode; separate cases require invalid replacement bootstrap input to preserve the existing directory. These cases establish rejection atomicity for validation failures. They do not prove transactional replacement of an entire directory if later file creation, synchronization, or root replacement fails.

## 4. Complete raw writes and failed-publication quarantine

A raw binary write may report a positive prefix shorter than the supplied
record. Treating every nonexceptional return as a complete record can lead to
synchronizing and naming an incomplete segment or root. An ensuing successful
synchronization does not repair the missing suffix. This is an API-level counterexample. The current suite simulates positive
short progress, invalid/no progress, and a root-write failure; those controlled
cases pass. No claim is made that the host filesystem spontaneously produced a
short write in the performance runs.

The writer now maintains an unwritten suffix and repeatedly writes that suffix.
Each reported count must be an integer, not Boolean, in [1, remaining_length].
The loop invariant is: the emitted bytes followed by the remaining suffix equal
the requested record in order. A positive legal count preserves this invariant
and strictly decreases the suffix length. After at most the requested byte count
of successful calls the suffix is empty and the full record has been emitted.
An exception or invalid/no-progress count raises instead of reaching the next
publication phase. This reasoning assumes honest returned counts and ordinary
sequential file-position semantics; it is not an arbitrary device-failure model.
Header, every body record, footer, and temporary root use the same write-all rule.

Once the byte-publication phase begins, an exception marks the owning instance
unavailable before releasing its update lock. All current-state access, snapshot
acquisition, preview, publication, compaction, and collection check this flag.
Installation of the new state and selected tuple is inside that guarded phase.
Therefore a caught error after root replacement cannot lead the same instance's
collector to delete the new root's referents using the stale cached tuple. The
object must be abandoned and reopened quiescently. Existing handles retain their
private in-memory values, but no pin transfers to a different instance. Prewrite
validation rejection leaves the old, unchanged instance usable.

The argument is scoped to exceptions escaping the guarded phase; process exit
still follows the conditional prefix theorem. Snapshot close now serializes its
closed-flag check and pin decrement under the instance lock, so concurrent close
calls on one handle cannot consume another handle's pin. The current suite executes a bounded concurrent-close case and the joint
history exercises ordinary held/released pins. These selected schedules do not
establish all possible thread interleavings.

Official API premise: Python, "io — Core tools for working with streams", Raw
I/O and RawIOBase.write, https://docs.python.org/3/library/io.html, read on
10 September 2026. Only the API contract is used; no runtime identifier is needed.

## 5. Strict representation admission

Equality of decoded values is not a type proof. For a one-fact footer, Boolean
true or floating 1.0 can compare equal to integer 1 in the implementation
language. Likewise a Boolean parent can compare equal to epoch 1. Both parsers
now check strict integer types before count/parent comparison. They independently
check the same identifier grammar, the equality of the eight-digit filename and
header epoch, unit-step delta epochs, and new-epoch stamps on changed sources.
An exact redundant old source record remains legal. A changed payload retaining
an old stamp is not made valid by closure when that source has no dependents.

Duplicate JSON object members are rejected instead of interpreted last-value
wins. Mode/kind types are checked before membership operations, preventing
container-valued discriminants from escaping the witness path. Source and reverse
put/delete overlap is rejected even when folding would erase the offending row.
These are explicit admission predicates, not proof that every malformed byte
string returns a diagnostic under arbitrary memory, recursion, filesystem, or
process failures. Extra uninterpreted fields and harmless lexical variation are
not an authenticity claim; the writer emits a deterministic encoding.

## 6. Why a selected-file count is not a work or space bound

Fix N unchanged live facts with one dependency each and K no-op delta publications
after a base. Both parsers reconstruct and check all live facts after every
selected segment. They therefore visit at least N(K+1) fact records during
closure checking although each delta can have constant-size content. This is
an implementation lower bound on that family, not a lower bound on all checkers.
A faster incremental certificate would require a separate soundness argument.

Separately, pin each of q successive complete compacted bases, each of byte size
at least B. Even with zero selected deltas (K=0), the retained bases occupy at
least qB bytes until those pins are released. Each current root still selects
one file. Neither construction contradicts the conditional K+1 selected-file
theorem; both prevent interpreting it as bounded total recovery CPU or space.

## 7. Root-selected database/export null and comparison boundary

The generic rooted database/export control has three durable components for each
published epoch: a complete immutable normalized SQLite image, a complete
canonical audit export, and one small root naming both filenames. Before an
update it re-reads the selected root and rejects a stale cached writer. Once byte
publication begins, any escaping exception quarantines the cached instance; a
caller must reopen from the selected root. If the selector did not advance, a
quiescent reopen may remove only unselected same-epoch image/export orphans before
retry. If the selector advanced before the exception, reopen observes the new
pair. These rules prevent continued operation from a cached endpoint whose
relationship to the durable selector is indeterminate. The publisher
completes and synchronizes both images and their directories before replacing
the root. The export parser imports only the standard library and accepts exactly
one object containing epoch, schema, sources, facts, and reverse maps. It checks
strict integer types, identifier grammar, source-generation horizon, one schema,
nonempty ordered dependencies, and exact reverse membership. Audit succeeds only
when the selected database decode, export decode, and expected logical state are
equal.

This construction does not independently parse SQLite pages and is not an
optimized extension to SQLite. It externalizes the logical state into a second
complete format, thereby matching the old-or-new cross-interface selector while
paying complete-image write and retention cost. Six dedicated export methods plus selector-grammar regressions cover valid pairs, malformed exports, cross-pair disagreement, selector binding, and duplicate fields. All pass.

The retained timing wrapper calls the complete selected-pair reader twice per
audit sample: durable_state performs one SQLite decode, one export parse, and one
full equality comparison; audit_report independently repeats the same three
operations.  The wrapper then compares the first decoded state with the shared
logical oracle.  The frozen timings describe this delivered double-pass path;
they are not divided by two or reinterpreted as an optimized single-pass audit.

The retained current comparison audits after every update. Across 500/3,000,
2,000/12,000, and 6,000/36,000 source/fact scales, all 264 measured endpoints are
equal to the shared logical oracle. In the fixed execution order FrontierStore,
SQLite-Normalized, then SQLite-Rooted-Audit, pooled and every per-repetition
median favor the rooted composition. The pooled values are 128.01 versus 219.77
ms, 722.78 versus 979.06 ms, and 2,548.22 versus 2,884.69 ms. Rooted audit is
lower on 44/48, 20/24, and 11/16 aligned update identities. SQLite rooted audit uses
27.75, 107.96, and 324.58 times as many process-accounted write bytes as
FrontierStore and retains 17.91, 22.40, and 17.81 times as many directory bytes. Those counters are not
device traffic, and the short runs retain every rooted image.

Two bounded attempts to obtain a counterbalanced large-scale comparison produced
no admissible observation: the first failed before data because of an interface
error, and the corrected launch ended before final accounting. Both reservations
remain charged. The failed attempts establish neither an order effect nor its
absence, so the timing order is descriptive for the retained execution design.

The exact relational reduction, not the timings, terminates the claim that the
frozen logical contract requires a specialized engine. The comparison adds a
bounded representation tradeoff: less software-accounted output and retained
space for FrontierStore, but no measured full-audit latency advantage in the
retained fixed-order runs. It does not prove that the rooted composition is
generally faster, nor is it an impossibility theorem for incrementally certified
or authenticated auditable storage.

