"""Separately authorized finite correctness run; never reopen the old allowance.

Only the fixed, reviewed Python stages below can run. This is not a throughput
driver, downloader, source-program executor, or configurable command runner.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
WHOLE_SECONDS = 840
COMMAND_SECONDS = 130  # Six reservations (780 s) plus 60 s controller/cleanup.
ADDRESS_BYTES = 1024 ** 3
STAGES = ("unit", "finite", "sqlite-strict", "crash", "joint", "retained")


def write_json(path: Path, value: dict) -> None:
    # LF is intentional: raw records can be compared across hosts.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inventory(root: Path) -> list[str]:
    """Static inventory, not evidence that a test body ran."""
    identifiers = []
    for path in sorted((root / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for method in node.body:
                    if isinstance(method, ast.FunctionDef) and method.name.startswith("test"):
                        identifiers.append(f"tests.{path.stem}.{node.name}.{method.name}")
    return sorted(identifiers)


def source_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): digest(p)
            for p in sorted(root.rglob("*.py")) if ".git" not in p.relative_to(root).parts}


def new_output(path: Path) -> Path:
    path = path.resolve()
    protected = [ROOT]
    if (ROOT.parent / "paper").is_dir():
        protected.append(ROOT.parent)
    if any(path == p or p in path.parents for p in protected):
        raise ValueError("raw attempts must be outside the delivered project")
    path.mkdir(parents=True, exist_ok=False)
    return path


def old_ledger(root: Path) -> dict:
    ledger = json.loads((root / "resource-accounting.json").read_text(encoding="utf-8"))
    if (ledger["campaign_cpu_upper_bound_seconds"] != 28800
            or ledger["remaining_experiment_allowance_seconds"] != 0
            or ledger["remaining_repair_reproduction_allowance_seconds"] != 0):
        raise ValueError("the retained exhausted nonrefundable ledger must remain intact")
    return ledger


def linux_limits(expected_parent: int) -> None:
    import ctypes
    import resource
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "parent-death guard failed")
    if os.getppid() != expected_parent:
        os.kill(os.getpid(), signal.SIGKILL)
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_BYTES, ADDRESS_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (165, 166))
    allowed = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {allowed[0]})


def child_cpu(process: subprocess.Popen) -> dict:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        get_times = ctypes.WinDLL("kernel32", use_last_error=True).GetProcessTimes
        get_times.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        get_times.restype = wintypes.BOOL
        values = [wintypes.FILETIME() for _ in range(4)]
        if not get_times(wintypes.HANDLE(int(process._handle)), *(ctypes.byref(v) for v in values)):
            return {"user_seconds": None, "system_seconds": None, "scope": "unavailable"}
        seconds = lambda v: ((int(v.dwHighDateTime) << 32) + int(v.dwLowDateTime)) / 10_000_000
        return {"user_seconds": seconds(values[3]), "system_seconds": seconds(values[2]),
                "scope": "direct child only; terminated grandchildren excluded"}
    return {"user_seconds": None, "system_seconds": None, "scope": "see waited-child rusage"}


def execute(stage: str, output: Path, deadline: float) -> dict:
    from functools import partial
    case = output / stage
    case.mkdir()
    scratch = case / "scratch"
    scratch.mkdir()
    seconds = min(COMMAND_SECONDS, max(0, deadline - time.monotonic() - 10))
    record = {"stage": stage, "status": "RESERVED", "refunded": False,
              "wall_reservation_seconds": seconds,
              "cpu_policy_reservation_seconds": seconds if sys.platform == "linux" else seconds * 5,
              "cpu_reservation_is_measurement": False}
    write_json(case / "accounting.json", record)
    command = [sys.executable, "-B", "-m", "experiments.owned_checks", "--case", stage,
               "--output", str(case / "checks.json"), "--scratch", str(scratch)]
    record["command"] = command
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT),
               TMP=str(scratch), TEMP=str(scratch), TMPDIR=str(scratch),
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    start = time.monotonic()
    own_cpu = time.process_time()
    before = None
    if sys.platform == "linux":
        import resource
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
    process = None
    # Always create raw output, including an unlaunched stage.
    with (case / "stdout.txt").open("w", encoding="utf-8", newline="\n") as stdout, \
            (case / "stderr.txt").open("w", encoding="utf-8", newline="\n") as stderr:
        try:
            if seconds < 1:
                record["status"] = "WHOLE_LIMIT_NOT_LAUNCHED"
            else:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stdout, stderr=stderr,
                    start_new_session=sys.platform == "linux",
                    preexec_fn=partial(linux_limits, os.getpid()) if sys.platform == "linux" else None)
                try:
                    process.wait(timeout=seconds)
                    record["status"] = "COMPLETED" if process.returncode == 0 else "FAILED"
                except subprocess.TimeoutExpired:
                    record["status"] = "WALL_LIMIT"
        except Exception as error:
            record["status"] = "LAUNCH_FAILED"
            record["error"] = f"{type(error).__name__}: {error}"
        finally:
            if process is not None:
                # On Linux also clear surviving descendants if the worker has
                # already exited. The reviewed Windows children have their own
                # 20-second timeout; no detached process is created.
                if sys.platform == "linux":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                record["exit_code"] = process.returncode
                record["child_cpu"] = child_cpu(process)
            record["wall_seconds"] = time.monotonic() - start
            record["controller_cpu_seconds"] = time.process_time() - own_cpu
            if before is not None:
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                record["child_cpu"] = {
                    "user_seconds": after.ru_utime - before.ru_utime,
                    "system_seconds": after.ru_stime - before.ru_stime,
                    "scope": "waited child and descendants accounted through that child",
                }
                record["waited_children_lifetime_max_rss_bytes"] = after.ru_maxrss * 1024
                record["rss_scope"] = "lifetime maximum, not per-stage or concurrent-tree peak"
            else:
                record["concurrent_tree_peak_rss_bytes"] = None
                record["rss_scope"] = "not measured on Windows"
            write_json(case / "accounting.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize-new-run", action="store_true", required=True)
    parser.add_argument("--windows-diagnostic", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if sys.flags.optimize:
        parser.error("assertions must remain enabled")
    if sys.platform != "linux" and not (sys.platform == "win32" and args.windows_diagnostic):
        parser.error("Linux required; Windows can only collect explicit failed/partial diagnostics")
    old_ledger(ROOT)
    output = new_output(args.out)
    start = time.monotonic()
    deadline = start + WHOLE_SECONDS
    cpu = time.process_time()
    original = digest(ROOT / "resource-accounting.json")
    hashes = source_hashes(ROOT)
    run = {"status": "RESERVED", "refunded": False, "separate_user_authorized_run": True,
           "old_ledger_sha256": original, "old_allowance_reused": False,
           "whole_wall_limit_seconds": WHOLE_SECONDS, "command_wall_limit_seconds": COMMAND_SECONDS,
           "cpu_policy_reservation_seconds": WHOLE_SECONDS if sys.platform == "linux" else WHOLE_SECONDS * 5,
           "reservation_is_actual_cpu": False, "input_download_bytes": 0,
           "inputs": "owned finite synthetic values only; no source program execution",
           "python": sys.version, "python_executable": sys.executable,
           "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
           "source_sha256": hashes, "static_unit_inventory": source_inventory(ROOT), "stages": []}
    run["reservation_allocation"] = {
        "stage_seconds": COMMAND_SECONDS * len(STAGES),
        "controller_cleanup_seconds": WHOLE_SECONDS - COMMAND_SECONDS * len(STAGES),
        "windows_multiplier_is_policy_only": 5,
    }
    write_json(output / "run.json", run)  # Entire reservation charged before the first launch.
    try:
        if sys.platform == "linux":
            import resource
            # Controller and its entire inherited tree use one CPU. Together
            # with the whole-wall watchdog this is the CPU policy envelope.
            os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
            resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_BYTES, ADDRESS_BYTES))
            run["linux_limits"] = {"affinity_cpu_count": 1,
                "per_process_address_bytes": ADDRESS_BYTES,
                "reviewed_schedule_max_processes": 3,
                "address_space_design_bound_bytes": 3 * ADDRESS_BYTES,
                "process_count_scope": "fixed reviewed schedule, not RLIMIT_NPROC",
                "os_release": Path("/etc/os-release").read_text(encoding="utf-8")}
        for stage in STAGES:
            run["stages"].append(execute(stage, output, deadline))
            write_json(output / "run.json", run)
        run["old_ledger_unchanged"] = digest(ROOT / "resource-accounting.json") == original
        run["source_unchanged"] = source_hashes(ROOT) == hashes
        run["status"] = "COMPLETED" if (sys.platform == "linux"
            and all(row["status"] == "COMPLETED" for row in run["stages"])
            and run["old_ledger_unchanged"] and run["source_unchanged"]) else "FAILED_OR_PARTIAL"
    except Exception as error:
        run["status"] = "CONTROLLER_FAILED"
        run["error"] = f"{type(error).__name__}: {error}"
    finally:
        run["wall_seconds"] = time.monotonic() - start
        run["controller_cpu_seconds"] = time.process_time() - cpu
        direct = sum((row.get("child_cpu", {}).get("user_seconds") or 0)
                     + (row.get("child_cpu", {}).get("system_seconds") or 0)
                     for row in run["stages"])
        nested = 0.0
        if sys.platform == "win32" and (output / "crash/checks.json").is_file():
            crash = json.loads((output / "crash/checks.json").read_text(encoding="utf-8"))
            for row in crash.get("sqlite_process_exits", []):
                child = row.get("child_cpu", {})
                nested += (child.get("user_seconds") or 0) + (child.get("system_seconds") or 0)
        run["observed_process_cpu_seconds_lower_bound"] = run["controller_cpu_seconds"] + direct + nested
        run["cpu_observation_scope"] = "controller instrumented interval plus waited workers; Windows nested SQL workers counted separately, Linux rusage already includes them; not editing or historical use"
        run["nested_sql_worker_cpu_seconds"] = nested if sys.platform == "win32" else None
        run["windows_cpu_boundary"] = "direct-child lower bounds only; no aggregate/peak guarantee" if sys.platform == "win32" else None
        write_json(output / "run.json", run)
    print(json.dumps({"status": run["status"], "output": str(output), "stages": len(run["stages"])}))
    return 0 if run["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
