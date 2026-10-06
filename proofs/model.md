# Model and correctness arguments

## 1. Scope and state

A logical state is `C=(e,sigma,S,F,R)`. Epoch and schema are positive integers.
`S[i]=(g_i,x_i)` is a source record; `F[f]=(p_f,sigma_f,D_f)` is a fact;
`R[i]` is the sorted, duplicate-free inverse list. Payloads are uninterpreted
text. Every dependency list is nonempty, canonical, and has distinct source
identifiers. Keys equal record identifiers. The implementation's immutable
record types and stable input arguments are part of the interface contract;
arbitrary executable Python objects are not a serialized input language.

Dependency closure requires: every `(i,g)` in a live fact names a live source
at generation `g`; every fact has the active schema; the domains of `R` and `S`
are equal; and `f in R[i]` iff `D_f` names `i`. **Well-formed closed states also
satisfy the generation horizon `1 <= g_i <= e`.** Closure alone does not imply
this horizon. Without it, a caller could supply epoch 1 with source generation
2, then update that source at epoch 2 without obtaining a fresh stamp.
The model validator and independent byte checker now reject that state.
Strict integer checks exclude Boolean generations and schema values.

There is exactly **one live store instance per directory**, including its
writer, readers, and collector. Its lock serializes capture, publication, and
collection. A file lock alone does not refresh another instance's cached root
or share snapshot pins. Recovery and offline checking occur without a
concurrent collector. Inputs remain stable during an operation. After an exception from the guarded byte-publication phase, the instance
rejects current-state operations and collection. It must be abandoned and
reopened, with no overlapping writer or collector, before operations resume.
These are instance-local rules, not a multi-instance recovery protocol.

Bootstrap stamps sources with its epoch. Ordinary transactions assign the next
epoch to changed or reintroduced sources. Compaction advances the publication
epoch without changing source stamps. The persistent name grammar admits
publication epochs through 99,999,999; publication outside that domain is
rejected before persistent output. The frozen histories start at epoch 1 and
are far below this representational limit.

A snapshot is a live handle to one captured `(state, selected-segment-tuple)`.
A *current acquisition* captures the installed tuple under the instance lock;
subsequent operations on an older handle are not new current acquisitions.
An acknowledgement is successful operation return, **after** root-directory
synchronization and installation of both in-memory fields. The durable root
replacement and the later API acknowledgement are distinct events.

## 2. Logical transition and representation

For source changes `Csrc`, explicit fact retractions `E`, and resulting schema
`sigma'`, invalidation is `I=E union (union R[i], i in Csrc)`, plus every old
fact on a schema change. The transition removes `I`, applies source changes,
and admits replacements only after all declared sources exist. Replacement
stamps are constructed from the post-source map. Omitted invalidated facts
remain absent; unaffected omitted facts remain present. Replacing an existing
fact also removes its old inverse memberships.

### Lemma 1: invalidation completeness

Every old fact naming a changed or retracted source is in `I`.

**Proof.** For any such `(f,i)`, exact inverse membership gives `f in R[i]`.
The union for changed source identifiers contains `f`. Explicit retractions and
schema-wide invalidation are included directly. No analysis of payload truth
is used.

### Lemma 2: inverse preservation

Deleting inverse memberships for removed/replaced facts, dropping nonlive
source keys, creating new source keys, and adding memberships for installed
facts yields the exact inverse of the new fact map.

**Proof.** Fix a live source and fact identifier. An unaffected fact retains
both its dependencies and membership. A removed fact loses membership unless
reinstalled naming that source. An installed fact gains precisely the
memberships in its new dependencies. These exhaustive cases give both
inclusions of the relation; explicit source-key handling gives domain equality.
Sorting changes representation, not membership.

### Lemma 3: transition preservation and stamp monotonicity

An accepted transaction from a well-formed closed state produces a well-formed
closed state. No remaining fact uses a pre-update stamp of a changed source.

**Proof.** Lemma 1 removes all old dependents; unchanged facts name only
unchanged sources. Replacements are checked and stamped after source edits.
Lemma 2 gives inverse equality. Schema cutover removes old-schema facts before
installing new-schema records. Old source stamps are at most `e`; every changed
or reintroduced live source receives `e+1`, strictly greater than any old stamp.
Unchanged stamps remain at most the new epoch. Type, identifier, and canonical
ordering clauses follow from construction and validation.

**Cost qualification.** The affected-set equation describes which memberships
change, not the total implementation cost. The implementation copies complete
maps and reverse sets and validates the complete resulting closure. Reverse
lookup avoids scanning all facts *to locate the affected set*. It does not make
complete transition, serialization, snapshot copying, or validation work
proportional only to the affected set.

### Lemma 4: selected-chain interpretation

A base enumerates the complete state. An ordinary delta, folded in the six
specified record phases over the preceding selected state, yields exactly the
state declared by its footer and new root.

**Proof.** Every changed source, removed/replaced fact, and changed inverse key
is emitted as its resulting value or deletion; other keys are unchanged. Fact
deletion precedes replacement. A base resets all maps. Induction over the
selected chain establishes equality of every map, epoch, schema, and count.
Counts alone would not establish equality.

The precomputed adapter now checks this missing premise explicitly. It copies
the supplied state and delta, rejects duplicate deletion records and
source/reverse put-delete overlaps, folds the exact six-phase edit, requires
closure, and compares the full result to the claimed state. Every changed or
inserted source must carry the new epoch. Fact deletion followed by replacement
remains permitted. The checked values, not mutable caller maps, are published.
Thus a closed claimed state paired with an empty, inconsistent delta is
rejected before persistence. This is a full-map refinement check, not a proof
that the producer computed the correct semantic facts.

A selected-chain interpreter reads only root-named, confined files. An orphan
final file or a partial temporary file is therefore inert. This holds for
quiescent recovery/checking, not an unpinned disk reader racing reclamation.

## 3. Theorems aligned with the manuscript

### Theorem 1: current-acquisition closure and acknowledged freshness

All live snapshots denote well-formed closed states. Suppose update `U`
returns successfully at epoch `e_U`, changing/retracting identifiers `C_U`.
For every current acquisition **invoked after that return**, its captured epoch
is at least `e_U`. For every fact in that acquisition and every dependency on
an identifier in `C_U`, the required stamp equals the source stamp in the
captured state and is at least `e_U`. A retracted source is either absent,
with no dependent fact, or has been reintroduced at a later epoch.

**Proof.** Bootstrap and Lemmas 3–4 establish closure by induction. Installation
and acquisition use the same lock; after successful return, a later acquisition
cannot capture a pre-`U` installed state. Later transactions preserve stamp
monotonicity, including after retraction/reintroduction; compaction leaves
stamps unchanged. Closure then equates every retained dependency to its current
source stamp. If a source is absent, closure excludes a dependent fact.

This theorem does **not** require an older pinned snapshot to refresh or forget
its older facts. An acquisition overlapping `U` may capture either adjacent
installed state according to lock order. Re-reading a handle acquired before
`U` remains governed by Theorem 2. There is no global revocation of historical
snapshots and no freshness obligation for arbitrary cached or second-instance
readers.

### Theorem 2: live snapshot stability

A live snapshot retains the complete state and selected tuple captured at
acquisition while the same instance publishes later updates or compacts.

**Proof.** Publication replaces private maps instead of mutating an installed
state. Public copies do not expose mutable dependency lists or map aliases;
the precomputed adapter captures its inputs. Pin registration occurs with
capture under the same lock used by collection. Therefore later installation
cannot change the captured observations, and its selected files remain in the
preservation set until release. A closed handle has no pin obligation. This is
an instance-local, not merely process-local, assertion.

### Theorem 3: conditional failure-prefix visibility

Assume complete byte writes and successful file and directory synchronization with their stated
persistence effects and atomic same-filesystem root replacement. Process
termination at a named publication boundary leaves the canonical root selecting
the complete old or complete new state, never a partially published segment.

**Proof.** Before root replacement, the canonical selector still names only the
old durable chain. The new segment may be partial at a temporary path or
complete but unselected; neither changes interpretation. The replacement root
is itself written and synchronized at a temporary path. Before replacement,
the selected new segment and its directory entry are synchronized. Atomic
replacement therefore switches between two complete selectors with complete
referents. Final root-directory synchronization establishes the declared
durability point; successful API return additionally installs the in-memory
pair. Recovery ignores unselected same-epoch leftovers before a subsequent
publication. Repeating this reasoning after quiescent reopen preserves a
prefix of accepted publications, possibly including the interrupted attempt.

The write-all loop and failure quarantine argument are detailed in
`representation.md`, Section 4. Their selected boundary cases pass in the
retained 114-method transcript. The ten historical process-exit placements predate the current
publication repairs and are not power-loss experiments. The theorem
asserts neither raw-device durability nor correctness after failed system calls
followed by continued use of the old instance. It is not an exactly-once
application retry protocol.

### Theorem 4: compaction equivalence and collection safety

Compaction preserves `(sigma,S,F,R)` while changing the physical epoch and
selected chain. Collection preserves every file in the current selected tuple
and every live tuple pinned through the same instance.

**Proof.** A new base enumerates the old maps, so Lemma 4 reconstructs precisely
those observations. Under the instance lock, collection forms the union of the
current tuple and all positive-count pins before choosing deletions. Capture
cannot interleave between that union and deletion. A file outside the union is
not needed by any protected reader. Cross-instance/process pins and concurrent
unpinned offline parsers are excluded.

### Theorem 5: conditional selected-segment-count bound

If the active policy permits at most `K` deltas after the selected base, open
and full checking need at most `K+1` **selected files for that root**, independent
of publications preceding the base.

**Proof.** The selected list contains exactly one base and at most `K` following
deltas. The base resets every map, so no predecessor file is required.

This is not a constant-time or byte-cost bound. Decoding depends on all
selected records, and checking each intermediate closure may revisit the
reconstructed maps. The root list itself costs `O(K)` entries. The complete
base can be large. Space occupied by obsolete files or any number of live pins
is outside this count. Without compaction, the suffix can grow linearly. The
implementation does not automatically enforce a universal compaction policy.

## 4. Atomicity is stronger than dependency closure

### Proposition 1: split decisions do not implement whole-snapshot atomicity

Specify an update by two complete endpoint observations, `O0` and `O1`, with
both source and fact components different. Require every successful concurrent
observation to equal an endpoint (and obey real-time order after an acknowledged
update). Suppose family decisions are independently visible and a reader can
observe between them, without a common selector, lock, or validation/retry.
Then the split implementation cannot satisfy this specification for all
schedules.

**Proof.** Whichever family becomes visible first, an intervening observation
contains one new and one old component, so equals neither endpoint. Choosing
that observation yields the counterexample. Making each family individually
durable cannot remove the observation. A lock excluding the interval, a
multiversion selector, or a validation/retry protocol can supply equivalent
reader semantics; a single physical root is sufficient but not uniquely
necessary. No general impossibility of split physical layouts is claimed.

**Counterexample to a closure-only interpretation.** Let `O0` contain
`a@1,b@1,p(a@1,b@1)` and let `O1` contain `a@2,b@1,p'(a@2,b@1)`, with exact
inverse maps. First remove `p` and its memberships, then update `a`, then install
`p'` and its memberships. The two intermediate states have no facts and exact
empty inverse lists. Both are dependency closed, but neither equals `O0` or
`O1`. Hence dependency closure alone admits the retraction-first protocol and
cannot prove the stronger common-decision necessity claim. The manuscript and
claim ledger use the endpoint specification explicitly.

### Retained-Logic Snapshot Bridge

LogicScan's structured logic storage and later retrieval motivate a retained
fact interface (its Sections 3–4). This project adds, rather than attributes to
that work, an update interface with complete declared source dependencies and
whole-snapshot observations.

Map retained source text to `S`, uninterpreted retained logic to `F`, and a
complete producer-supplied source association to `D`; construct `R` as its exact
inverse. Under this adapter premise, the required source/fact observation is
precisely `(sigma,S,F,R)`. The preceding split-decision counterexample therefore
applies, and Theorems 1–4 give conditional storage preservation for the mapped
state. This is a formal reduction of an added storage requirement, not proof
that LogicScan exposes complete provenance, has the defect, or provides these
storage guarantees. No model execution, contract execution, or semantic-audit
accuracy is part of the bridge.

## 5. Standalone checking and current evidence limits

A violated predicate implemented by the standalone parser yields rejection and
a first witness. The parser imports no writer, transition, or shared-model code,
but it follows the same documented format and is not formally verified or
organizationally independent. It checks declared structure, not authenticity or
semantic completeness. A maliciously replaced but internally closed history can
be accepted, and an analyzer-omitted dependency cannot be inferred from payloads.

The retained passing transcript covers 114 methods in eight modules: 23 core-store cases, 12 transition-boundary cases, 23 representation cases, 24 input-capture/preflight cases, 16 strict-format cases, 11 rooted-audit/export cases, two complete-enumeration cases, and three journal-evidence/reference-surface cases. The delivered source additionally contains six SQLite integer/text-domain methods, twelve data-only evidence-admission methods, and eight finite-run admission/coverage methods, making 140 in total; those 26 are not covered by this retained transcript. Changes to existing methods are likewise not retroactively validated by that transcript. The separately budgeted Windows run passes 24 existing methods plus eight runner methods, but has ten synchronization errors and 98 existing methods unavailable through POSIX-only imports. It is not a complete passing storage suite and does not weaken the publication premises above.
The current bounded model completes all $5^4+5^3+5^2=775$ histories in the declared finite alphabet and 2,925 intermediate states.  For each final transition it replays ten abstract publication events into a complete-object map and selector, materializes the selected object, and then applies a separately defined cut/endpoint oracle plus closure, horizon, and inverse checks.  All 7,750 observations pass.  A closed mixed value and an early new selector are retained negative controls.  This is an abstract selector model, not a concrete filesystem or device-failure model.  The former 769-history output omitted six length-two histories and is retained only as explicitly retired evidence.

The current joint operational case uses 24 sources, 144 facts, one reader, 20
updates, and five compactions. After every successful update return, a fresh
acquisition equals the complete expected endpoint while one held acquisition
remains equal to its captured endpoint. Pinned files remain present, released
obsolete files are collected, and the quiescent standalone checker accepts after
every step.

The 80 process-exit rows and seven mutation rows under `results/raw/` target an
earlier source state. They are retained but are not promoted to correctness evidence for the repaired
implementation after the publication and parser changes. Likewise, earlier
closure-only reader rows do not establish exact endpoint identity. The current
claims use the complete suite, bounded histories, joint case, and accepted
endpoint comparisons instead.

## 6. Representability, interface-matched null, and scientific decision

`representation.md` proves an exact relational encoding and valid-transition
lowering for the closed-state domain. With one read transaction, that encoding
supplies logical endpoint identity, closure, current-acquisition freshness, and
materialized historical-value stability. It does not supply a separate parser of
database pages or the physical segment-pin protocol. Logical semantics alone
therefore do not establish specialized-engine novelty.

The same file gives a closed, horizon-respecting nonendpoint observation when
separate SELECTs straddle a commit. The repaired normalized reader begins one
explicit read transaction, and the deterministic regression appears in the
retained 114-method transcript; the new owned SQLite stage independently checks
that reader across a commit without claiming complete storage-suite coverage.

The generic rooted inspectability control writes a complete immutable SQLite
image and a complete canonical audit export, then atomically replaces one root
naming both. A standard-library-only parser checks the export, and the audit
requires equal database/export decodes and equality with the shared logical
state. Across retained 500/3,000, 2,000/12,000, and 6,000/36,000 source/fact
scales, all 264 measured endpoints equal the oracle. Under the fixed engine order,
pooled and per-repetition medians favor the rooted control, while FrontierStore
writes and retains substantially fewer bytes. No counterbalanced observation was
admitted, so the latency order is a descriptive property of those runs rather
than a general performance result.

The exact relational encoding is sufficient to terminate logical necessity for
a specialized engine even if the timing rows are ignored. Current checker work
can still be at least N(K+1) for N persistent facts and K empty deltas, and q
pinned complete bases can retain at least qB bytes even when K=0. These are
implementation-bound families, not universal lower bounds. A future positive
result would need an incremental sound certificate or a controlled deployment
constraint in which the byte/retention difference is itself decisive.
