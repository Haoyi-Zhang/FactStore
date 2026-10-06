"""FrontierStore reference implementation."""

from typing import TYPE_CHECKING

from .model import Fact, Source, State, Transaction, TransitionError, apply_transaction, check_closure

if TYPE_CHECKING:
    from .store import FrontierStore, Snapshot


def __getattr__(name: str):
    # Logical/model and standalone-parser imports need no POSIX storage APIs.
    # Requesting a concrete store retains its original Linux requirements.
    if name in {"FrontierStore", "Snapshot"}:
        from .store import FrontierStore, Snapshot
        return FrontierStore if name == "FrontierStore" else Snapshot
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Fact",
    "Source",
    "State",
    "Transaction",
    "TransitionError",
    "apply_transaction",
    "check_closure",
    "FrontierStore",
    "Snapshot",
]
