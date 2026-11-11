"""Pure journal read checked at admission and again before the final transport write."""
from dataclasses import asdict

from . import controller_authority, native_journal


def require_session(db, key, *, expected_incarnation, process):
    receipt = native_journal.creation(db, key)
    if receipt is None:
        other = db.execute("SELECT 1 FROM native_session_creations WHERE server_id=? AND native_session_id=?",
                           (key.server_id, key.native_session_id)).fetchone()
        native_journal.require(False, native_journal.IDENTITY_MISMATCH if other else native_journal.CREATION_UNPROVEN)
    intent = receipt["data"]["intent"]
    native_journal.ordinary_authority(intent["controller_authority"])
    controller_authority.require_entry({"controller_authority": intent["controller_authority"],
        "controller_incarnation": intent["controller_incarnation"]}, expected_incarnation=expected_incarnation)
    native_journal.process_identity(process)
    native_journal.require(asdict(process) == receipt["data"]["observed"]["process"], native_journal.IDENTITY_MISMATCH)
    return receipt
