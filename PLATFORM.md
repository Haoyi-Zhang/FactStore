# Platform and environment scope

## Data-only reconstruction

The summary, paper-input, robustness, reference, and package-verification commands read retained files and do not execute the storage engines. They require a Python 3 interpreter with the standard library. The exact Python minor version used for the accepted engine timings was not recorded, so no particular current interpreter version is claimed to reproduce the timing values.

## Engine execution

The finite abstract enumeration and SQLite integer/text preflight methods are
portable correctness checks. Importing the logical model or a standalone parser
does not load the POSIX engine. Run these without claiming a physical storage or
filesystem result:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m unittest tests.test_exhaustive tests.test_sql_integer_domain -v
```

The delivered engine runner is Linux-specific. It imports `fcntl`, reads `/proc/self/io`, `/proc/meminfo`, and `/proc/<pid>/status`, uses cgroup-v2 `memory.max`, `memory.current`, and `cpu.max`, and calls `os.sched_getaffinity`/`os.sched_setaffinity`. A platform lacking those interfaces is unsupported by the guarded engine commands without code changes. SQLite is provided by Python's standard-library `sqlite3` module, but the accepted runs did not retain the Python or SQLite version/compile options.

`results/current/accepted-environment.json` records every environment field recoverable from the accepted case files and launcher contract. Kernel release, Linux distribution, filesystem type and mount options, storage medium, CPU model, virtualization/container provider, Python version, SQLite version, and an immutable identifier for the exact timed source tree were not recorded and remain explicitly unknown. The current review machine must not be substituted for those missing historical fields.

## Commands

Data-only checks can be run from the artifact root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_checked_results.py --results results
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_artifact.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python experiments/verify_publication_references.py
```

Current-source correctness checks use the same Python environment but also require the Linux interfaces above:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m unittest discover -v tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m experiments.verify_reviewer_repairs --output /tmp/reviewer-repairs.json
```

The accepted large timing cases are retained evidence, not a command promised to reproduce their values on an unspecified new host.

## Separately budgeted finite runs

`python -B -m experiments.fresh_run --authorize-new-run --out /tmp/p021-owned-checks`
requires an absent output directory outside the delivered project. The old
28,800-second lifetime ledger remains exhausted and byte-unchanged. Every new
destination receives its own nonrefundable reservation and raw stdout/stderr,
including failed or unlaunched stages; it is not a resume or allowance reset.

The Linux controller and inherited child tree are pinned to one CPU. Six
sequential stages each reserve at most 130 wall seconds, leaving 60 seconds of
the 840-second whole-run policy envelope for controller and cleanup. Per-command
cleanup has a five-second wait. Each process has a one-GiB address-space limit;
the reviewed schedule has at most three processes (controller, stage, and one
owned crash worker), for a three-GiB design bound. Process count is a property of
this fixed reviewed schedule, not an `RLIMIT_NPROC` claim. The stage workers also
have a CPU limit and a parent-death signal, and timeout cleanup targets the owned
process group. Failed gates remain failures. The prepared Ubuntu 24.04 job has a
15-minute job timeout, including checkout/setup/upload overhead.

Linux waited-child CPU accounting includes descendants accounted through their
waited parent; its RSS field is a lifetime maximum, not a simultaneous-tree or
per-stage peak. Every run records the current interpreter, SQLite, platform,
source bytes, and old-ledger digest. These new identifiers are not backfilled
into historical timing records. Workflow dependency downloads are outside the
correctness entry point's zero experimental-input-download count.

Windows is supported only with `--windows-diagnostic` and cannot return an
overall passing result. It retains actual import/synchronization errors, without
stubbing `fcntl`, omitting fsync, or labeling unavailable cases as successful
skips. A five-times-wall reservation is a conservative diagnostic policy charge,
not a measured CPU total or a hard resource guarantee. Windows process CPU is
read from completed worker handles; the owned nested SQLite workers are metered
separately. No aggregate-tree memory peak is measured. The original full-suite
Linux storage coverage remains a runtime gap until the prepared run executes.
