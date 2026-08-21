"""The one place a workbook's identity is computed and validated.

Gate 1 asks a named human to confirm what a workbook IS before anything is
parsed. That confirmation is only worth something if the bytes the reviewer
confirmed are the bytes the parser later reads. This module defines the single
answer to "which workbook is this?" — a SHA-256 over an immutable byte
sequence — so the UI, the orchestrator, and the tests cannot drift into three
slightly different answers.

Why bytes rather than a path: a filesystem path is a mutable reference. Hashing
a path before Gate 1 and again after parsing proves the file was identical at
two sampled instants, not throughout — and `agents/parser.py` opens the workbook
five separate times. Hashing a byte sequence captured once removes the question
entirely, because bytes cannot change underneath the caller.

What this establishes: WHICH bytes were confirmed and parsed.
What it does NOT establish: that the workbook is correct, that its figures are
right, or that the person who confirmed it understood what they were confirming.
Identity is not validity.
"""

import hashlib
import re

# A SHA-256 digest as hexlify produces it: 64 lowercase hex characters.
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HASH_LENGTH = 64


class WorkbookIdentityError(ValueError):
    """Raised when a workbook is not the one that was confirmed.

    Deliberately not a GateBlockedError: no gate ran. Identity is verified
    BEFORE Gate 1 is invoked, so that a confirmation is never recorded for a
    workbook whose identity was never established. Collapsing the two would
    make "the human did not confirm" and "this is not the file they confirmed"
    read as the same event, and they are not.

    Deliberately not a bare ValueError either: callers need to distinguish a
    mismatched workbook from any other invalid argument.
    """


def sha256_bytes(payload: bytes) -> str:
    """The canonical workbook identity: SHA-256 over the exact bytes supplied.

    Takes bytes, never a path, so there is no window in which the thing hashed
    and the thing used could differ.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise WorkbookIdentityError(
            f"workbook identity is computed over bytes, not {type(payload).__name__}. "
            "Pass the uploaded file's bytes, not a path or a file object."
        )
    return hashlib.sha256(payload).hexdigest()


def validate_hash_format(candidate: object, *, label: str = "workbook hash") -> str:
    """Reject anything that is not a well-formed SHA-256 digest.

    An empty string, None, a truncated digest, or an uppercase one must never
    be able to masquerade as a confirmed identity — a falsy or malformed value
    slipping through would make the control silently optional, which is the
    failure mode this whole recommendation exists to remove.
    """
    if not isinstance(candidate, str):
        raise WorkbookIdentityError(
            f"{label} must be a 64-character hex string, not {type(candidate).__name__}"
        )
    if not _HASH_PATTERN.fullmatch(candidate):
        raise WorkbookIdentityError(
            f"{label} is not a well-formed SHA-256 digest: expected {HASH_LENGTH} "
            f"lowercase hex characters, got {candidate!r}"
        )
    return candidate


def verify_bytes_match(payload: bytes, expected_hash: str) -> str:
    """Confirm `payload` is the workbook identified by `expected_hash`.

    Returns the computed hash when they agree, so callers can record the single
    verified value rather than re-deriving it. Raises WorkbookIdentityError when
    they do not — naming both digests in full, because a truncated digest in an
    error message cannot be checked against anything.
    """
    validate_hash_format(expected_hash, label="confirmed workbook hash")
    actual_hash = sha256_bytes(payload)
    if actual_hash != expected_hash:
        raise WorkbookIdentityError(
            "this workbook is not the one that was confirmed at Gate 1. "
            f"Confirmed: {expected_hash}. Supplied: {actual_hash}. "
            "A file with the same name is still a different workbook. Nothing "
            "has been parsed and no decision has been recorded; re-confirm the "
            "context for the workbook you intend to review."
        )
    return actual_hash
