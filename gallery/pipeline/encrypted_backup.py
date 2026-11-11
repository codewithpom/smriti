"""Password-protected, chunked backup archives for private gallery state.

CLI password source: RECOVERY_BACKUP_PASSWORD. Never put the password in argv,
the archive, or repository files. Public archive metadata contains no source
paths or identities. Requires ``cryptography``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
from typing import Iterable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


FORMAT = "smriti-encrypted-backup-v1"
DEFAULT_CHUNK_SIZE = 20 * 1024 * 1024
MAX_CHUNK_SIZE = 20 * 1024 * 1024
_SALT_BYTES = 16
_NONCE_BYTES = 12
_KEY_BYTES = 32
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1


class BackupError(Exception):
    """Invalid, unsafe, or unauthenticated backup operation."""


def _password_from_environment() -> bytes:
    value = os.environ.get("RECOVERY_BACKUP_PASSWORD")
    if not value:
        raise BackupError("RECOVERY_BACKUP_PASSWORD must contain the backup password")
    return value.encode("utf-8")


def _derive_key(password: bytes, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=_KEY_BYTES, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(password)


def _aad(salt: bytes, chunk_size: int, total_size: int, count: int, index: int) -> bytes:
    return (f"{FORMAT}\0{salt.hex()}\0{chunk_size}\0{total_size}\0{count}\0{index}").encode("ascii")


def _source_files(source: Path) -> Iterable[tuple[Path, str]]:
    if not source.is_dir():
        raise BackupError("source must be an existing directory")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise BackupError("source tree may not contain symbolic links")
        rel = path.relative_to(source).as_posix()
        _validate_portable_path(rel)
        if path.is_dir():
            yield path, rel + "/"
        elif path.is_file():
            yield path, rel
        else:
            raise BackupError("source tree contains an unsupported filesystem entry")


def _create_tar(source: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for path, name in _source_files(source):
            info = tar.gettarinfo(str(path), arcname=name)
            # Do not preserve host-specific ownership, names, or timestamps.
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            if info.isdir():
                tar.addfile(info)
            else:
                with path.open("rb") as stream:
                    tar.addfile(info, stream)


def _prepare_output(destination: Path) -> tuple[Path, bool]:
    destination = destination.absolute()
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise BackupError("destination must not exist or must be empty")
        return destination, True
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination, False


def _publish_temp(temp: Path, destination: Path, was_empty: bool) -> None:
    if was_empty:
        destination.rmdir()
    try:
        temp.replace(destination)
    except Exception:
        if was_empty and not destination.exists():
            destination.mkdir()
        raise


def _pack(source: Path, destination: Path, password: bytes, chunk_size: int) -> None:
    if not password:
        raise BackupError("password must not be empty")
    if not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise BackupError("chunk size must be between 1 byte and 20 MiB")
    if not source.is_dir():
        raise BackupError("source must be an existing directory")
    source = source.resolve()
    destination = destination.resolve(strict=False)
    if source == destination or source in destination.parents or destination in source.parents:
        raise BackupError("source and destination must not contain one another")
    destination, was_empty = _prepare_output(destination)
    temp = Path(tempfile.mkdtemp(prefix="smriti-encrypted-backup-"))
    staged = Path(tempfile.mkdtemp(prefix=".encrypted-backup-", dir=destination.parent))
    try:
        archive = temp / "payload.tar.gz"
        _create_tar(source, archive)
        total_size = archive.stat().st_size
        count = max(1, (total_size + chunk_size - 1) // chunk_size)
        salt = os.urandom(_SALT_BYTES)
        aes = AESGCM(_derive_key(password, salt))
        manifest = {
            "format": FORMAT,
            "kdf": {"name": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P, "salt": salt.hex()},
            "cipher": "AES-256-GCM",
            "chunk_size": chunk_size,
            "total_size": total_size,
            "chunk_count": count,
        }
        with archive.open("rb") as stream:
            for index in range(count):
                plain = stream.read(chunk_size)
                nonce = os.urandom(_NONCE_BYTES)
                encrypted = nonce + aes.encrypt(nonce, plain, _aad(salt, chunk_size, total_size, count, index))
                (staged / f"chunk-{index:06d}.bin").write_bytes(encrypted)
        (staged / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        archive.unlink()
        _publish_temp(staged, destination, was_empty)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def pack(source: Path | str, destination: Path | str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
    """Pack a directory using RECOVERY_BACKUP_PASSWORD."""
    _pack(Path(source), Path(destination), _password_from_environment(), chunk_size)


def _read_manifest(archive_dir: Path) -> tuple[dict, bytes]:
    try:
        manifest_path = archive_dir / "manifest.json"
        raw = manifest_path.read_bytes()
        if len(raw) > 4096:
            raise BackupError("invalid encrypted archive")
        manifest = json.loads(raw)
        if manifest.get("format") != FORMAT or manifest.get("cipher") != "AES-256-GCM":
            raise BackupError("unsupported encrypted archive")
        kdf = manifest["kdf"]
        salt = bytes.fromhex(kdf["salt"])
        chunk_size, total_size, count = (manifest[k] for k in ("chunk_size", "total_size", "chunk_count"))
        if (kdf.get("name") != "scrypt" or kdf.get("n") != _SCRYPT_N or kdf.get("r") != _SCRYPT_R or kdf.get("p") != _SCRYPT_P
                or len(salt) != _SALT_BYTES or type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK_SIZE
                or type(total_size) is not int or total_size < 1 or type(count) is not int or count < 1
                or count != (total_size + chunk_size - 1) // chunk_size):
            raise BackupError("invalid encrypted archive")
        entries = list(archive_dir.iterdir())
        if count != len(entries) - 1 or any(p.is_symlink() or not p.is_file() for p in entries):
            raise BackupError("invalid encrypted archive")
        expected = {"manifest.json", *(f"chunk-{i:06d}.bin" for i in range(count))}
        actual = {p.name for p in entries}
        if actual != expected:
            raise BackupError("invalid encrypted archive")
        return manifest, salt
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("invalid encrypted archive") from exc


def _validate_portable_path(name: str, *, is_dir: bool = False) -> str:
    """Validate a POSIX archive name before mapping it onto Windows or POSIX."""
    raw = name[:-1] if is_dir and name.endswith("/") else name
    path = PurePosixPath(raw)
    parts = path.parts
    if not raw or path.is_absolute() or not parts or raw != "/".join(parts):
        raise BackupError("archive contains an unsafe or unsupported path")
    reserved = {"CON", "PRN", "AUX", "NUL"}
    for part in parts:
        if (part in (".", "..") or part.endswith((".", " "))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)):
            raise BackupError("archive contains an unsafe or unsupported path")
        device = part.split(".", 1)[0].upper()
        if device in reserved or (len(device) == 4 and device[:3] in {"COM", "LPT"} and device[3] in "123456789¹²³"):
            raise BackupError("archive contains an unsafe or unsupported path")
    return "/".join(parts)


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    seen: set[str] = set()
    files: set[str] = set()
    directories: set[str] = set()
    for member in members:
        name = member.name
        if "\\" in name or not (member.isdir() or member.isfile()):
            raise BackupError("archive contains an unsafe or unsupported path")
        normalized = _validate_portable_path(name, is_dir=member.isdir())
        portable = normalized.casefold()
        components = portable.split("/")
        ancestors = {"/".join(components[:i]) for i in range(1, len(components))}
        if portable in seen or ancestors & files:
            raise BackupError("archive contains an unsafe or unsupported path")
        if member.isfile():
            if portable in directories or portable in {"/".join(components[:i]) for i in range(1, len(components))}:
                raise BackupError("archive contains an unsafe or unsupported path")
            files.add(portable)
        else:
            if portable in files:
                raise BackupError("archive contains an unsafe or unsupported path")
            directories.add(portable)
        directories.update(ancestors)
        seen.add(portable)
    return members


def _unpack(archive_dir: Path, destination: Path, password: bytes) -> None:
    if not password:
        raise BackupError("password must not be empty")
    if not archive_dir.is_dir():
        raise BackupError("encrypted archive directory does not exist")
    manifest, salt = _read_manifest(archive_dir)
    chunk_size, total_size, count = (manifest[k] for k in ("chunk_size", "total_size", "chunk_count"))
    destination, was_empty = _prepare_output(destination)
    temp = Path(tempfile.mkdtemp(prefix=".encrypted-restore-", dir=destination.parent))
    private_temp = Path(tempfile.mkdtemp(prefix="smriti-encrypted-restore-"))
    try:
        aes = AESGCM(_derive_key(password, salt))
        tar_path = private_temp / "payload.tar.gz"
        written = 0
        with tar_path.open("wb") as output:
            for index in range(count):
                data = (archive_dir / f"chunk-{index:06d}.bin").read_bytes()
                expected_plain = min(chunk_size, total_size - written)
                if len(data) != _NONCE_BYTES + expected_plain + 16:
                    raise BackupError("encrypted archive is incomplete or invalid")
                try:
                    plain = aes.decrypt(data[:_NONCE_BYTES], data[_NONCE_BYTES:], _aad(salt, chunk_size, total_size, count, index))
                except Exception as exc:
                    raise BackupError("encrypted archive authentication failed") from exc
                output.write(plain)
                written += len(plain)
        if written != total_size:
            raise BackupError("encrypted archive is incomplete or invalid")
        extracted = temp / "restored"
        extracted.mkdir()
        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                members = _safe_members(tar)
                for member in members:
                    rel = PurePosixPath(member.name)
                    target = extracted.joinpath(*rel.parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = tar.extractfile(member)
                        if source is None:
                            raise BackupError("archive contains an unreadable file")
                        with source, target.open("xb") as output:
                            shutil.copyfileobj(source, output)
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError("encrypted archive payload is invalid") from exc
        _publish_temp(extracted, destination, was_empty)
    except Exception:
        if was_empty and not destination.exists():
            destination.mkdir()
        shutil.rmtree(temp, ignore_errors=True)
        shutil.rmtree(private_temp, ignore_errors=True)
        raise
    shutil.rmtree(temp, ignore_errors=True)
    shutil.rmtree(private_temp, ignore_errors=True)


def unpack(archive_dir: Path | str, destination: Path | str) -> None:
    """Restore an encrypted directory using RECOVERY_BACKUP_PASSWORD."""
    _unpack(Path(archive_dir), Path(destination), _password_from_environment())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or restore a password-protected gallery recovery backup")
    commands = parser.add_subparsers(dest="command", required=True)
    pack_parser = commands.add_parser("pack", help="encrypt a directory into chunks")
    pack_parser.add_argument("source_dir", type=Path)
    pack_parser.add_argument("archive_dir", type=Path)
    unpack_parser = commands.add_parser("unpack", help="restore an encrypted chunk directory")
    unpack_parser.add_argument("archive_dir", type=Path)
    unpack_parser.add_argument("destination_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            pack(args.source_dir, args.archive_dir)
        else:
            unpack(args.archive_dir, args.destination_dir)
    except (BackupError, OSError) as exc:
        # Filesystem exceptions can include private identity-bearing filenames.
        detail = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
        print(f"backup failed: {detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
