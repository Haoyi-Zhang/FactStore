"""Exercise the real data-only verifier CLI without inherited import paths."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ARTIFACT = Path(__file__).resolve().parents[1]


class VerifierCliTests(unittest.TestCase):
    def check_verifier(self, script: str, cwd: Path) -> None:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONOPTIMIZE", None)
        for flags in ((), ("-O",)):
            with self.subTest(cwd=str(cwd), flags=flags):
                completed = subprocess.run(
                    [sys.executable, "-B", *flags, script],
                    cwd=cwd,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(
                    0,
                    completed.returncode,
                    msg=completed.stdout + completed.stderr,
                )
                result = json.loads(completed.stdout)
                self.assertEqual("PACKAGE_DATA_CONSISTENT", result["status"])
                self.assertEqual("CONSISTENT", result["maintenance_state"])

    def test_direct_script_from_artifact_root_without_pythonpath(self) -> None:
        self.check_verifier("experiments/verify_artifact.py", ARTIFACT)

    def test_absolute_script_keeps_file_bound_root_from_other_cwd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="artifact-verifier-cli-") as temporary:
            self.check_verifier(
                str(ARTIFACT / "experiments/verify_artifact.py"), Path(temporary)
            )


if __name__ == "__main__":
    unittest.main()
