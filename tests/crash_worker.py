"""Child process that terminates at a requested publication boundary."""

from __future__ import annotations

import argparse

from frontierstore.store import FrontierStore
from frontierstore.workload import make_update


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("store")
    parser.add_argument("failpoint")
    parser.add_argument("seed", type=int)
    args = parser.parse_args()
    store = FrontierStore(args.store)
    transaction = make_update(store.state, batch=2, seed=args.seed)
    store.commit(transaction, failpoint=args.failpoint)
    return 91


if __name__ == "__main__":
    raise SystemExit(main())
