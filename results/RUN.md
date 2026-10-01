# Evidence interpretation and reconstruction

## Current retained surfaces

The current artifact admits exactly three directories to the retained fixed-order performance summary:

- `current/current-small/`
- `current/current-medium-retry/`
- `current/current-large/`

The deterministic aggregator reads their observation rows, requires every durable-equality flag to be true, summarizes per-observation update, audit, aligned combined latency, process writes, and final stored bytes, and emits:

- `current/summary/current_summary.csv`
- `current/summary/current_summary.json`
- `current/summary/current_comparison.csv`
- `current/summary/current_order_diagnostics.csv`
- `current/summary/current_order_repetitions.csv`
- `current/summary/current_order_diagnostics.json`
- `current/acceptance.json`

It also regenerates the paper-facing current CSVs and fixed-order macro file. A separate data-only generator reconstructs the bounded-correctness/resource macro file from retained evidence. There are 264 retained observation rows across all three engines and scales.

The pilot is retained but excluded from the main comparison. `current/current-medium/` is excluded because its outer controller was lost while the child continued; `current/current-medium-retry/` is the contained retained rerun. The monitor-failure directories contain no scientific test outcome. `current/order-sensitivity/` failed before observations because of an interface error, and `current/order-counterbalance/` produced no observation before controller termination. All failed attempts are retained and fully charged rather than overwritten.

## Current correctness surfaces

- `current/code-audit-tests/`: 104 of 104 executable methods pass; `record.json` binds the count and success fields to the complete verbose transcript.
- `current/tiny-histories/`: 775 histories, 2,925 states, and 7,750 publication cuts complete with zero violations.
- `current/joint-history/`: 20 updates, one reader, and five compactions complete; fresh and held endpoints, pins, standalone checking, and released-history collection all pass.

Earlier current-suite directories are retained as intermediate evidence. Only `code-audit-tests/` is the source-coupled complete 114-method transcript for the delivered implementation.

## Historical surfaces

Files under `raw/` are retained historical observations. They include the original scale, sensitivity, invalidation, compaction, concurrency, public-text, process-exit, mutation, abstract-cut, and resource rows. The current publication, parsing, source-capture, and relational-reader repairs postdate some of those observations. Historical rows are therefore not promoted to current-source correctness or performance unless the paper explicitly labels them as earlier evidence.

The old raw engine label `NDBM-Rebuild` denotes the unchanged-library Python `dbm.dumb` whole-state wrapper. Historical aggregation maps that label to `DBM-Dumb-Rebuild`; no ndbm experiment exists.

## Data-only commands

From the artifact root:

```sh
mkdir -p ../summary-reconstruction ../current-summary-reconstruction ../paper-input-reconstruction
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_checked_results.py --results results
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_artifact.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_publication_references.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/summarize_results.py --results results --output ../summary-reconstruction
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/summarize_current.py --results results/current --output ../current-summary-reconstruction --paper-dir ../paper-input-reconstruction
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/generate_paper_values.py --output ../paper-input-reconstruction/paper-values.tex
```

Use fresh output directories. Historical reconstruction emits `historical-values.tex`; the separately generated `paper-values.tex` is the current manuscript input and must match `current/paper-inputs/paper-values.tex`. These commands inspect retained files and regenerate derived tables or macros; they do not execute the storage engines.


## Publication-reference inventory

The artifact retains two independent publication-side inventories:

- `reference_audit.csv`, which records bibliographic metadata, the exact manuscript role, inspected support, and a stable locator;
- `publication_reference_manifest.csv`, a compact release inventory of key, title, manuscript role, and stable locator.

The artifact-only command above requires a duplicate-free exact match between those two surfaces and enforces a floor of 55 scholarly entries. The delivered inventory has 62 entries. From the complete project root, the stronger source-coupled check is:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=artifact \
python artifact/experiments/verify_publication_references.py \
  --audit artifact/reference_audit.csv \
  --manifest artifact/publication_reference_manifest.csv \
  --tex paper/main.tex \
  --bib paper/references.bib
```

That mode also requires exact set equality among all citation keys in `paper/main.tex`, all entries in `paper/references.bib`, and both artifact inventories. It requires closed metadata and passage-fit status for all 62 audit rows, checks audited author order, year, venue, title, publication kind, and encoded DOI/arXiv agreement, and rejects citation commands containing multiple keys. Consequently, an uncited padding entry, missing BibTeX record, duplicate key, unresolved audit row, mismatched title/locator, or citation pile makes the verification fail.

## Metric boundaries

- Scale updates use the same untimed logical transition for every engine.
- Persistence return is timed separately from the following full audit.
- Combined latency is aligned per observation before median/p95 aggregation.
- Every retained audit decodes a durable state and compares it with the shared logical oracle.
- Process write bytes are software-accounted block writes, not physical device traffic.
- Final stored bytes are short-run directory sizes.
- Ordinary page cache is retained.
- The current synthetic graph fixes six facts per source, dependency width two, and update batch eight.
- All retained scale cases use the fixed order FrontierStore, SQLite-Normalized, then SQLite-Rooted-Audit.
- No counterbalanced timing observation was admitted, so latency rankings are descriptive for that execution order.

## Resource interpretation

`resource-accounting.json` conservatively charges unknown inherited use to the full non-reserved campaign budget. Attempt reservations, including failures, are not refunded. The final CPU upper bound reaches 28,800 seconds and no scientific-run allowance remains. This is a campaign safety bound, not a performance metric, exact consumption record, or machine fingerprint.

## Journal-only deterministic reconstruction

The journal robustness script reads only the already accepted 264 rows and executes no storage engine:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/journal_robustness.py \
  --results results/current \
  --output results/current/summary/journal-robustness.json
```

It emits JSON, two CSV surfaces, and TeX macros with a fixed bootstrap seed. The outputs remain conditional on the fixed engine order.


## Reviewer-repair validation

`current/reviewer-repairs/verification.json` is a deterministic, non-performance
record for the abstract selector oracle, replacement-only dependency change,
legal closed-nonendpoint construction, rooted-audit double-pass call count, and
all-source syntax parser negative case.  `current/accepted-environment.json`
records the confirmed historical platform surface and explicitly unknown fields
for all three accepted scale cases.  Neither file modifies the frozen 264 timing
rows.

