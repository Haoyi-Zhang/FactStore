"""Fail-closed admission and accounting for named repair/reproduction cases.

Unknown inherited work is charged to the full non-reserve policy envelope rather
than treated as zero. Each admitted attempt reserves its complete case-specific
CPU envelope before launch; failed, interrupted, and uncollected attempts are not
refunded automatically. These cases may consume the protected quarter because
they are precisely repair and clean-reproduction work, never a new broad matrix.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
from functools import partial
import json
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3
ADDRESS_LIMIT = 500 * 1024 ** 2
WALL_LIMIT = 170.0
CASES = {
    "current-small": {
        "command": ["-m", "experiments.current_validation", "--mode", "small"],
        "cpu_reservation_seconds": 600.0,
        "explicit_case_worker_threads": 0,
        "max_processes": 2,
        "address_limit_bytes": 768 * 1024 ** 2,
    },
    "current-medium": {
        "command": ["-m", "experiments.current_validation", "--mode", "medium"],
        "cpu_reservation_seconds": 900.0,
        "explicit_case_worker_threads": 0,
        "max_processes": 2,
        "address_limit_bytes": 1280 * 1024 ** 2,
    },
    "current-large": {
        "command": ["-m", "experiments.current_validation", "--mode", "large"],
        "cpu_reservation_seconds": 1200.0,
        "explicit_case_worker_threads": 0,
        "max_processes": 2,
        "address_limit_bytes": 2048 * 1024 ** 2,
    },
    "current-pilot": {
        "command": ["-m", "experiments.current_validation", "--mode", "pilot"],
        "cpu_reservation_seconds": 360.0,
        "explicit_case_worker_threads": 0,
        "max_processes": 2,
        "address_limit_bytes": 500 * 1024 ** 2,
    },
    "order-sensitivity": {
        "command": ["-m", "experiments.order_sensitivity"],
        "cpu_reservation_seconds": 600.0,
        "explicit_case_worker_threads": 0,
        "max_processes": 2,
        "address_limit_bytes": 2048 * 1024 ** 2,
    },
    "order-counterbalance": {
        "command": ["-m", "experiments.order_sensitivity"],
        "cpu_reservation_seconds": 480.0,
        "explicit_case_worker_threads": 0,
        "max_processes": 2,
        "address_limit_bytes": 2048 * 1024 ** 2,
    },
    "boundary-tests": {
        "command": ["-m", "unittest", "-v", "tests.test_store", "tests.test_boundaries", "tests.test_representation", "tests.test_audit_export"],
        "cpu_reservation_seconds": 240.0,
        "explicit_case_worker_threads": 2,
        "max_processes": 3,
        "address_limit_bytes": 500 * 1024 ** 2,
    },
    "joint-history": {
        "command": ["-m", "experiments.joint_history"],
        "cpu_reservation_seconds": 240.0,
        "explicit_case_worker_threads": 1,
        "max_processes": 2,
        "address_limit_bytes": 500 * 1024 ** 2,
    },
    "tiny-histories": {
        "command": ["-m", "experiments.exhaustive"],
        "cpu_reservation_seconds": 120.0,
        "explicit_case_worker_threads": 0,
        "max_processes": 2,
        "address_limit_bytes": 500 * 1024 ** 2,
    },
}


def fail(reason: str) -> int:
    print(json.dumps({"status": "BLOCKED", "reason": reason, "experiment_started": False}))
    return 2


def finite_number(value) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def process_tree(root_pid: int) -> tuple[int, int, int]:
    """Sample SUM of RSS and threads across the current descendant tree.

    Sampling is not a continuous high-water mark; process identities and lifetimes
    can change between reads. Named cases may not fork descendants or detach.
    """
    rows = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = {}
            for line in (entry / "status").read_text().splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                if key not in {"PPid", "VmRSS", "Threads"}:
                    continue
                parts = value.strip().split()
                if parts:
                    fields[key] = parts[0]
            rows[int(entry.name)] = (int(fields.get("PPid", 0)), int(fields.get("VmRSS", 0)) * 1024,
                                     int(fields.get("Threads", 1)))
        except (OSError, ValueError, KeyError):
            continue
    descendants = {root_pid}
    while True:
        enlarged = descendants | {pid for pid, row in rows.items() if row[0] in descendants}
        if enlarged == descendants:
            break
        descendants = enlarged
    return (sum(rows[p][1] for p in descendants if p in rows),
            sum(rows[p][2] for p in descendants if p in rows), len(descendants))


def headroom() -> tuple[int, int]:
    """Read Linux availability once at admission, without a stress test."""
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, rest = line.split(":", 1)
        mem[key] = int(rest.split()[0]) * 1024
    if mem.get("SwapTotal", -1) != 0:
        raise ValueError("no-swap premise not established")
    available = mem["MemAvailable"]
    cg = Path("/sys/fs/cgroup")
    limit = (cg / "memory.max").read_text().strip()
    if limit != "max":
        available = min(available, int(limit) - int((cg / "memory.current").read_text()))
    workers = len(os.sched_getaffinity(0))
    quota, period = (cg / "cpu.max").read_text().split()
    if quota != "max":
        workers = min(workers, int(quota) // int(period))
    # The child is pinned to one CPU; the controller performs only monitoring.
    if workers < 2:
        raise ValueError("two-CPU admission headroom not established")
    if available < 1600 * 1024 ** 2:
        raise ValueError("insufficient headroom for the 1.2-GiB sampled RSS stop and overhead")
    return available, min(workers, 4)


def write_json_durable(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def child_limits(address_limit: int, expected_parent_pid: int) -> None:
    # A separate process group makes timeout cleanup reliable, but without a
    # parent-death signal it can also outlive an externally terminated runner.
    # Set the signal first and close the fork/prctl race before applying limits.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != expected_parent_pid:
        os.kill(os.getpid(), signal.SIGKILL)
    resource.setrlimit(resource.RLIMIT_AS, (address_limit, address_limit))
    resource.setrlimit(resource.RLIMIT_CPU, (165, 166))
    allowed = sorted(os.sched_getaffinity(0))
    if not allowed:
        raise RuntimeError("no CPU affinity available")
    os.sched_setaffinity(0, {allowed[0]})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ledger_path = ROOT / "resource-accounting.json"
    # Exclusive lock also prevents two admitted campaign runners at once.
    with ledger_path.open("r+", encoding="utf-8") as ledger_file:
        fcntl.flock(ledger_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = json.load(ledger_file)
        upper = ledger.get("campaign_cpu_upper_bound_seconds")
        downloads = ledger.get("external_download_upper_bound_bytes")
        expanded = ledger.get("expanded_input_upper_bound_bytes")
        if not all(finite_number(v) for v in (upper, downloads, expanded)):
            return fail("unknown campaign CPU/download/expanded-input upper bound")
        if downloads > GIB or expanded > 2 * GIB:
            return fail("cumulative input cap exceeded")
        spec = CASES[args.case]
        reservation = float(spec["cpu_reservation_seconds"])
        if upper + reservation > float(ledger.get("campaign_cpu_limit_seconds", 28800)):
            return fail("attempt would exceed the cumulative campaign CPU cap")
        if ledger.get("admission") != "REPAIR_REPRODUCTION_ONLY_UNDER_CONSERVATIVE_POLICY_ENVELOPE":
            return fail("ledger does not admit repair/reproduction cases")
        if args.output.exists():
            return fail("output already exists; never erase retained or interrupted evidence")
        try:
            available, worker_headroom = headroom()
        except (OSError, ValueError, KeyError) as error:
            return fail(str(error))
        address_limit = int(spec["address_limit_bytes"])
        if address_limit <= 0:
            return fail("invalid child address-space envelope")
        args.output.mkdir(parents=True, exist_ok=False)
        # Charge the full reserved amount even when a run fails or its process is killed.
        ledger["campaign_cpu_upper_bound_seconds"] = upper + reservation
        ledger["recorded_attempt_reservations_seconds"] += reservation
        remaining = float(ledger.get("campaign_cpu_limit_seconds", 28800)) - upper - reservation
        ledger["remaining_experiment_allowance_seconds"] = remaining
        ledger["remaining_repair_reproduction_allowance_seconds"] = remaining
        ledger_file.seek(0); json.dump(ledger, ledger_file, indent=2)
        ledger_file.write("\n"); ledger_file.truncate(); ledger_file.flush(); os.fsync(ledger_file.fileno())
        attempt = {
            "case": args.case,
            "status": "RESERVED",
            "charged_cpu_reservation_seconds": reservation,
            "campaign_charge_refunded": False,
            "scientific_acceptance": "NOT_INFERRED",
        }
        write_json_durable(args.output / "attempt.json", attempt)
        start = time.monotonic()
        before = resource.getrusage(resource.RUSAGE_SELF)
        children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        rss_peak = tasks_peak = processes_peak = 0
        outcome = "UNFINISHED"
        monitor_error = None
        cmd = [sys.executable, *spec["command"]]
        if args.case == "joint-history":
            cmd += ["--output", str((args.output / "observations.json").resolve())]
        elif args.case == "tiny-histories":
            cmd += ["--output", str((args.output / "observations.json").resolve())]
        elif args.case in {"current-pilot", "current-small", "current-medium", "current-large", "order-sensitivity", "order-counterbalance"}:
            cmd += ["--output", str((args.output / "observations.json").resolve())]
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1",
                   OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
        proc = None
        try:
            with (args.output / "stdout.txt").open("w") as stdout, (args.output / "stderr.txt").open("w") as stderr:
                proc = subprocess.Popen(
                    cmd, cwd=ROOT, env=env, stdout=stdout, stderr=stderr,
                    start_new_session=True,
                    preexec_fn=partial(child_limits, address_limit, os.getpid()),
                )
                attempt["status"] = "ACTIVE"
                write_json_durable(args.output / "attempt.json", attempt)
                while proc.poll() is None:
                    try:
                        rss, tasks, processes = process_tree(os.getpid())
                    except (OSError, ValueError, IndexError) as error:
                        monitor_error = f"{type(error).__name__}: {error}"
                        outcome = "MONITOR_FAILURE"
                        break
                    rss_peak, tasks_peak, processes_peak = max(rss_peak, rss), max(tasks_peak, tasks), max(processes_peak, processes)
                    if time.monotonic() - start > WALL_LIMIT:
                        outcome = "WALL_LIMIT"; break
                    if rss > int(1.2 * GIB) or processes > int(spec["max_processes"]):
                        outcome = "RESOURCE_LIMIT"; break
                    time.sleep(.02)
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                if outcome == "UNFINISHED":
                    outcome = "CASE_COMPLETED" if proc.returncode == 0 else "CASE_FAILED"
        finally:
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL); proc.wait(timeout=5)
            own = resource.getrusage(resource.RUSAGE_SELF)
            child = resource.getrusage(resource.RUSAGE_CHILDREN)
            report = {
                "case": args.case, "status": outcome,
                "wall_seconds_to_accounting": time.monotonic()-start,
                "user_cpu_seconds_to_accounting": own.ru_utime-before.ru_utime+child.ru_utime-children_before.ru_utime,
                "system_cpu_seconds_to_accounting": own.ru_stime-before.ru_stime+child.ru_stime-children_before.ru_stime,
                "sampled_concurrent_tree_rss_peak_bytes": rss_peak,
                "sampled_aggregate_threads_peak": tasks_peak,
                "sampled_process_count_peak": processes_peak,
                "sampled_peak_is_continuous_bound": False,
                "aggregate_address_space_design_bound_bytes": address_limit * int(spec["max_processes"]),
                "exit_code": None if proc is None else proc.returncode,
                "admission_available_memory_bytes": available,
                "admission_worker_headroom": worker_headroom,
                "charged_cpu_reservation_seconds_including_controller_and_validation": reservation,
                "campaign_charge_refunded": False,
                "monitor_error": monitor_error,
                "explicit_case_worker_threads_bound": int(spec["explicit_case_worker_threads"]),
                "process_count_stop": int(spec["max_processes"]),
                "child_cpu_affinity_count": 1,
                "child_address_space_limit_bytes": address_limit,
                "sampled_os_threads_are_worker_count": False,
            }
            write_json_durable(args.output / "accounting.json", report)
            attempt["status"] = outcome
            attempt["exit_code"] = None if proc is None else proc.returncode
            write_json_durable(args.output / "attempt.json", attempt)
    print(json.dumps({"status": outcome, "scientific_acceptance": "NOT_INFERRED"}))
    return 0 if outcome == "CASE_COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
