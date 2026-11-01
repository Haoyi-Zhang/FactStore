"""Admit only the two reviewed, byte-bound consumer data archives."""
import hashlib
from pathlib import PurePosixPath
import zipfile

REVIEWED_ARCHIVES = {
    'data/import-releases/sources.zip': '94d7f6ee01e175ae20b8656338a0cb62665d96e8a6030bdd7a42f42e06e7e65d',
    'results/import-lifecycle/native-evidence.zip': 'b20c7894dcd6570bd673e477cd5484af40ae145e2ed54d32173c52cd7cfa3725',
}

def verify(root):
    for rel,digest in REVIEWED_ARCHIVES.items():
        path=root/rel
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('reviewed consumer data archive differs: '+rel)
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            if not names or len(names)!=len(set(names)):
                raise ValueError('consumer archive duplicate or empty member set')
            if sum(item.file_size for item in archive.infolist())>128*1024*1024:
                raise ValueError('consumer archive expanded-byte bound')
            for name in names:
                if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name:
                    raise ValueError('unsafe consumer archive member')
            if archive.testzip() is not None:
                raise ValueError('corrupt consumer archive member')
    # This checks package identity and safe member admission, not scientific
    # validity. Source/dependency reconstruction and decoding all three final
    # stores remain separate tests in test_import_lifecycle.
    return set(REVIEWED_ARCHIVES)
