"""Export an owned normalized SQLite endpoint without producer imports."""
import argparse
import json
from pathlib import Path

from sql_export import export_database
from export_consumer import load, require_endpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('use a fresh export path; do not replace historical evidence')
    value = export_database(args.database, args.output)
    decoded = load(args.output)
    require_endpoint(decoded, value)
    status = 'COMPLETE_LOGICAL_SQL_EXPORT_STRUCTURALLY_VALID'
    if args.expected:
        require_endpoint(decoded, load(args.expected))
        status = 'MATCHES_SUPPLIED_COMPLETE_EXPECTED_ENDPOINT'
    print(json.dumps(dict(status=status, epoch=decoded['epoch'], sources=len(decoded['sources']),
        facts=len(decoded['facts']), export_bytes=args.output.stat().st_size,
        independent_sqlite_page_parser=False, crash_atomic_database_export_pair=False)))


if __name__ == '__main__':
    main()
