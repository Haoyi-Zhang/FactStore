# Platform and environment scope

## Data-only reconstruction

The summary, paper-input, robustness, reference, and package-verification commands read retained files and do not execute the storage engines. They require a Python 3 interpreter with the standard library. The exact Python minor version used for the accepted engine timings was not recorded, so no particular current interpreter version is claimed to reproduce the timing values.

## Engine execution

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
