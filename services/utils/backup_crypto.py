"""
# services/utils/backup_crypto.py
Sealed snapshots of the VPS brain (plan task C-125 step 6; contract K-77 'Snapshot').

jarvis-agent-svc seals its daily snapshot to one X25519 public key, BACKUP_RECIPIENT_PUBLIC_KEY; only the PC holds
the private key (scripts/ops/backup_key.py keeps it DPAPI-wrapped and decrypts a pulled snapshot). The scheme uses the
`cryptography` library's primitives only (X25519, HKDF-SHA256, ChaCha20-Poly1305); nothing is hand-rolled:

- every file gets a fresh ephemeral X25519 key; HKDF-SHA256 over the shared secret (salt: 16 random bytes; info:
  b'jarvis/snapshot/v1' + ephemeral public key + recipient public key) gives the 32-byte ChaCha20-Poly1305 key;
- the plaintext is cut into chunks of at most `chunk_size` bytes and each chunk is sealed on its own (the STREAM
  construction): nonce = the chunk counter as 11 big-endian bytes + one flag byte (1 on the last chunk, else 0), and
  the associated data is the whole header. A reordered, dropped, truncated, appended or altered chunk, a wrong key
  or a changed header fails to open;
- layout: header = MAGIC (8 bytes) | version (1) | ephemeral public key (32) | salt (16) | chunk size (4, big
  endian); then one record per chunk = ciphertext length (4, big endian) | ciphertext (chunk + 16-byte tag). The
  last record carries the final flag and may hold an empty chunk; nothing may follow it.

Keys are 32 raw bytes, written as 64 hex characters (standard or URL-safe base64 of the 32 bytes is accepted too).
Nothing here logs, and no error message holds key material or plaintext.
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import re
import struct
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"JRVSNAP1"
VERSION = 1
KEY_BYTES = 32
SALT_BYTES = 16
TAG_BYTES = 16
HKDF_INFO = b"jarvis/snapshot/v1"
DEFAULT_CHUNK_SIZE = 1 << 20  # 1 MiB of plaintext per sealed record
MAX_CHUNK_SIZE = 16 << 20
_COUNTER_BYTES = 11
_HEADER = struct.Struct(">8sB32s16sI")
HEADER_BYTES = _HEADER.size
_LENGTH = struct.Struct(">I")
_HEX_KEY = re.compile(r"[0-9a-fA-F]{64}")


class SnapshotCryptoError(ValueError):
    """A key that cannot be used, or a sealed file that does not open (wrong key, damaged, truncated, extended).
    The message is a short reason, never key material or content."""


# ---------------------------------------------------------------- keys


def _key_bytes(text: str | bytes, what: str) -> bytes:
    if isinstance(text, bytes):
        if len(text) == KEY_BYTES:
            return text
        text = text.decode("ascii", "replace")
    value = (text or "").strip()
    if _HEX_KEY.fullmatch(value):
        return bytes.fromhex(value)
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decode(value + "=" * (-len(value) % 4))
        except (binascii.Error, ValueError):
            continue
        if len(raw) == KEY_BYTES:
            return raw
    raise SnapshotCryptoError(f"{what} is not a 32-byte X25519 key (64 hex characters)")


def parse_public_key(text: str | bytes) -> X25519PublicKey:
    """BACKUP_RECIPIENT_PUBLIC_KEY as an X25519 public key; SnapshotCryptoError for anything else."""
    return X25519PublicKey.from_public_bytes(_key_bytes(text, "the recipient public key"))


def parse_private_key(text: str | bytes) -> X25519PrivateKey:
    """The PC's X25519 private key (hex, base64 or 32 raw bytes); SnapshotCryptoError for anything else."""
    return X25519PrivateKey.from_private_bytes(_key_bytes(text, "the private key"))


def public_bytes(key: X25519PublicKey | X25519PrivateKey) -> bytes:
    public = key.public_key() if isinstance(key, X25519PrivateKey) else key
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def public_key_hex(key: X25519PublicKey | X25519PrivateKey) -> str:
    """64 hex characters: the form BACKUP_RECIPIENT_PUBLIC_KEY takes."""
    return public_bytes(key).hex()


def private_key_hex(key: X25519PrivateKey) -> str:
    """The private key as 64 hex characters. Only for the PC's DPAPI-wrapped key file; never print or log it."""
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption()).hex()


def generate_private_key() -> X25519PrivateKey:
    return X25519PrivateKey.generate()


def fingerprint(key: X25519PublicKey | X25519PrivateKey) -> str:
    """A short, public identifier of a key pair (sha256 of 'jarvis-fp:' + the public key hex, first 8 hex)."""
    digest = hashes.Hash(hashes.SHA256())
    digest.update(b"jarvis-fp:" + public_key_hex(key).encode("ascii"))
    return digest.finalize().hex()[:8]


def _derive(shared: bytes, salt: bytes, eph_public: bytes, recipient_public: bytes) -> ChaCha20Poly1305:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
               info=HKDF_INFO + eph_public + recipient_public).derive(shared)
    return ChaCha20Poly1305(key)


def _nonce(counter: int, final: bool) -> bytes:
    if counter >= 1 << (8 * _COUNTER_BYTES):
        raise SnapshotCryptoError("too many chunks")
    return counter.to_bytes(_COUNTER_BYTES, "big") + (b"\x01" if final else b"\x00")


def looks_sealed(head: bytes) -> bool:
    """True when head (the first bytes of a file) starts with this format's magic."""
    return head[:len(MAGIC)] == MAGIC


# ---------------------------------------------------------------- sealing


class SealingWriter:
    """A write-only file object that seals everything written to it into `out` (see the module docstring). Use it as
    tarfile's fileobj in stream mode ('w|'); close() seals the last chunk with the final flag (out stays open).
    bytes_in / bytes_out count the plaintext taken and the sealed bytes written."""

    def __init__(self, out: BinaryIO, recipient: X25519PublicKey, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        if not 1 <= int(chunk_size) <= MAX_CHUNK_SIZE:
            raise SnapshotCryptoError("chunk size out of range")
        self._out = out
        self._chunk = int(chunk_size)
        eph = X25519PrivateKey.generate()
        eph_public = public_bytes(eph)
        recipient_public = public_bytes(recipient)
        salt = os.urandom(SALT_BYTES)
        self._header = _HEADER.pack(MAGIC, VERSION, eph_public, salt, self._chunk)
        self._aead = _derive(eph.exchange(recipient), salt, eph_public, recipient_public)
        self._buffer = bytearray()
        self._counter = 0
        self.closed = False
        self.bytes_in = 0
        self.bytes_out = 0
        self._emit(self._header)

    def _emit(self, data: bytes) -> None:
        self._out.write(data)
        self.bytes_out += len(data)

    def _seal(self, chunk: bytes, final: bool) -> None:
        sealed = self._aead.encrypt(_nonce(self._counter, final), bytes(chunk), self._header)
        self._counter += 1
        self._emit(_LENGTH.pack(len(sealed)) + sealed)

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self.closed:
            raise ValueError("write to a closed SealingWriter")
        view = memoryview(data).cast("B")
        self._buffer += view
        self.bytes_in += len(view)
        # keep at least one byte back while the stream is open, so the final flag always lands on a chunk
        while len(self._buffer) > self._chunk:
            self._seal(self._buffer[:self._chunk], final=False)
            del self._buffer[:self._chunk]
        return len(view)

    def flush(self) -> None:
        self._out.flush()

    def close(self) -> None:
        if self.closed:
            return
        self._seal(self._buffer, final=True)
        self._buffer = bytearray()
        self.closed = True
        self._out.flush()

    def __enter__(self) -> SealingWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        if exc[0] is None:
            self.close()


def seal_bytes(data: bytes, recipient: X25519PublicKey, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> bytes:
    out = io.BytesIO()
    with SealingWriter(out, recipient, chunk_size=chunk_size) as writer:
        writer.write(data)
    return out.getvalue()


# ---------------------------------------------------------------- opening


def _read_exact(src: BinaryIO, size: int) -> bytes:
    data = b""
    while len(data) < size:
        part = src.read(size - len(data))
        if not part:
            break
        data += part
    return data


def open_stream(src: BinaryIO, dst: BinaryIO, private_key: X25519PrivateKey) -> int:
    """Opens a sealed stream into dst, chunk by chunk, and returns the plaintext size. Raises SnapshotCryptoError when
    the header, a chunk or the end does not verify: dst may then hold a verified prefix, so a caller writes it to a
    temporary file and keeps it only after this returns (scripts/ops/backup_key.py does)."""
    header = _read_exact(src, HEADER_BYTES)
    if len(header) != HEADER_BYTES:
        raise SnapshotCryptoError("not a JARVIS snapshot (too short)")
    magic, version, eph_public, salt, chunk = _HEADER.unpack(header)
    if magic != MAGIC:
        raise SnapshotCryptoError("not a JARVIS snapshot")
    if version != VERSION:
        raise SnapshotCryptoError("unknown snapshot version")
    if not 1 <= chunk <= MAX_CHUNK_SIZE:
        raise SnapshotCryptoError("bad chunk size")
    try:
        shared = private_key.exchange(X25519PublicKey.from_public_bytes(eph_public))
    except ValueError:
        raise SnapshotCryptoError("bad ephemeral key") from None
    aead = _derive(shared, salt, eph_public, public_bytes(private_key))
    counter = 0
    total = 0
    while True:
        length_raw = _read_exact(src, _LENGTH.size)
        if len(length_raw) != _LENGTH.size:
            raise SnapshotCryptoError("truncated snapshot (no final chunk)")
        (length,) = _LENGTH.unpack(length_raw)
        if not TAG_BYTES <= length <= chunk + TAG_BYTES:
            raise SnapshotCryptoError("bad chunk length")
        sealed = _read_exact(src, length)
        if len(sealed) != length:
            raise SnapshotCryptoError("truncated snapshot")
        plain: bytes | None = None
        final = False
        for flag in (False, True):
            try:
                plain = aead.decrypt(_nonce(counter, flag), sealed, header)
            except InvalidTag:
                continue
            final = flag
            break
        if plain is None:
            raise SnapshotCryptoError("a chunk does not verify (wrong key or damaged file)")
        dst.write(plain)
        total += len(plain)
        counter += 1
        if final:
            if src.read(1):
                raise SnapshotCryptoError("data after the final chunk")
            return total


def open_bytes(blob: bytes, private_key: X25519PrivateKey) -> bytes:
    out = io.BytesIO()
    open_stream(io.BytesIO(blob), out, private_key)
    return out.getvalue()
