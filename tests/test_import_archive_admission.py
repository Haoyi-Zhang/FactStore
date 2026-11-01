"""Archive admission must not exempt arbitrary or modified ZIP payloads."""
from pathlib import Path
import shutil
import tempfile
import unittest
from experiments.import_archive_admission import REVIEWED_ARCHIVES,verify

ROOT=Path(__file__).resolve().parents[1]
class ImportArchiveAdmissionTests(unittest.TestCase):
    def test_exact_reviewed_payloads(self):
        self.assertEqual(verify(ROOT),set(REVIEWED_ARCHIVES))

    def test_modified_payloads_reject(self):
        for changed in REVIEWED_ARCHIVES:
            with self.subTest(path=changed),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                for rel in REVIEWED_ARCHIVES:
                    target=root/rel;target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(ROOT/rel,target)
                with (root/changed).open('ab') as handle:handle.write(b'changed')
                with self.assertRaisesRegex(ValueError,'archive differs'):verify(root)

    def test_unregistered_archive_is_not_admitted(self):
        self.assertNotIn('other.zip',verify(ROOT))

if __name__=='__main__':unittest.main()
