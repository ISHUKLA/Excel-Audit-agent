"""Tests for core/workbook_identity.py — the single definition of "which
workbook is this?".

Covers the clean case and the messy one: blank, truncated, uppercase, and
non-string values must never be able to masquerade as a confirmed identity.
"""

import hashlib

import pytest

from core.workbook_identity import (
    HASH_LENGTH,
    WorkbookIdentityError,
    sha256_bytes,
    validate_hash_format,
    verify_bytes_match,
)

WORKBOOK = b"PK\x03\x04 pretend this is a real xlsx"
OTHER = b"PK\x03\x04 a different workbook entirely"


def test_hash_is_a_plain_sha256_of_the_bytes():
    """No salting, no normalisation — anyone can reproduce this by hand with
    sha256sum, which is what makes it checkable evidence."""
    assert sha256_bytes(WORKBOOK) == hashlib.sha256(WORKBOOK).hexdigest()
    assert len(sha256_bytes(WORKBOOK)) == HASH_LENGTH


def test_identical_bytes_hash_identically_and_different_bytes_do_not():
    assert sha256_bytes(WORKBOOK) == sha256_bytes(bytes(WORKBOOK))
    assert sha256_bytes(WORKBOOK) != sha256_bytes(OTHER)


def test_a_single_changed_byte_changes_the_hash():
    """The property the whole control rests on."""
    altered = bytearray(WORKBOOK)
    altered[-1] ^= 0x01
    assert sha256_bytes(bytes(altered)) != sha256_bytes(WORKBOOK)


def test_empty_bytes_are_hashable_not_an_error():
    """An empty upload is a real thing that can happen; it has an identity and
    should fail later on parsing, not be conflated with a malformed hash."""
    assert sha256_bytes(b"") == hashlib.sha256(b"").hexdigest()


def test_hashing_a_path_string_is_refused():
    """The defect this module exists to prevent: hashing the NAME of a mutable
    file rather than its contents."""
    with pytest.raises(WorkbookIdentityError, match="bytes, not str"):
        sha256_bytes("/tmp/provisions.xlsx")


# --------------------------------------------------------------------------
# format validation — a malformed hash must never look present
# --------------------------------------------------------------------------


def test_a_well_formed_digest_is_returned_unchanged():
    digest = sha256_bytes(WORKBOOK)
    assert validate_hash_format(digest) == digest


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "abc",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        " " + "a" * 63,
    ],
)
def test_malformed_digests_are_refused(bad):
    with pytest.raises(WorkbookIdentityError):
        validate_hash_format(bad)


@pytest.mark.parametrize("bad", [None, 0, b"a" * 64, ["a" * 64]])
def test_non_string_digests_are_refused(bad):
    """None and empty string are the dangerous ones: a falsy value slipping
    through would make the binding silently optional."""
    with pytest.raises(WorkbookIdentityError):
        validate_hash_format(bad)


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------


def test_matching_bytes_return_the_verified_hash():
    digest = sha256_bytes(WORKBOOK)
    assert verify_bytes_match(WORKBOOK, digest) == digest


def test_different_bytes_are_refused():
    with pytest.raises(WorkbookIdentityError, match="not the one that was confirmed"):
        verify_bytes_match(OTHER, sha256_bytes(WORKBOOK))


def test_the_error_names_both_digests_in_full():
    """A truncated digest in an error message cannot be checked against
    anything, so both appear at full length."""
    confirmed = sha256_bytes(WORKBOOK)
    with pytest.raises(WorkbookIdentityError) as caught:
        verify_bytes_match(OTHER, confirmed)
    message = str(caught.value)
    assert confirmed in message
    assert sha256_bytes(OTHER) in message


def test_the_error_says_nothing_was_parsed_or_recorded():
    with pytest.raises(WorkbookIdentityError) as caught:
        verify_bytes_match(OTHER, sha256_bytes(WORKBOOK))
    message = str(caught.value).lower()
    assert "nothing has been parsed" in message
    assert "same name is still a different workbook" in message


def test_a_malformed_expected_hash_is_refused_before_any_comparison():
    """Verification must not silently pass because the expected value was junk."""
    for bad in ("", "not-a-hash", None):
        with pytest.raises(WorkbookIdentityError):
            verify_bytes_match(WORKBOOK, bad)
