from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import encrypted_backup as backup


class EncryptedBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "private-source"
        self.source.mkdir()
        (self.source / "people.json").write_text('[{"name":"Private Person","id":"S123456"}]', encoding="utf-8")
        (self.source / "photo.bin").write_bytes(bytes(range(256)) * 2)

    def tearDown(self):
        self.temp.cleanup()

    def pack(self, name="backup"):
        archive = self.root / name
        with patch.dict("os.environ", {"RECOVERY_BACKUP_PASSWORD": "long test recovery passphrase"}):
            backup.pack(self.source, archive, chunk_size=73)
        return archive

    def unpack(self, archive, destination, password="long test recovery passphrase"):
        with patch.dict("os.environ", {"RECOVERY_BACKUP_PASSWORD": password}):
            backup.unpack(archive, destination)

    def test_round_trip_is_chunked_and_public_metadata_has_no_private_data(self):
        archive = self.pack()
        restored = self.root / "restored"
        chunks = sorted(archive.glob("chunk-*.bin"))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(p.stat().st_size <= backup.MAX_CHUNK_SIZE + 28 for p in chunks))
        public = (archive / "manifest.json").read_bytes() + b"".join(p.read_bytes() for p in chunks)
        for secret in (b"Private Person", b"S123456", b"people.json", b"photo.bin"):
            self.assertNotIn(secret, public)
        self.unpack(archive, restored)
        self.assertEqual((restored / "people.json").read_bytes(), (self.source / "people.json").read_bytes())
        self.assertEqual((restored / "photo.bin").read_bytes(), (self.source / "photo.bin").read_bytes())

    def test_wrong_password_and_modified_chunk_leave_no_partial_restore(self):
        archive = self.pack()
        destination = self.root / "restore-wrong"
        with self.assertRaises(backup.BackupError):
            self.unpack(archive, destination, "incorrect password")
        self.assertFalse(destination.exists())

        chunk = sorted(archive.glob("chunk-*.bin"))[0]
        value = bytearray(chunk.read_bytes())
        value[-1] ^= 0x01
        chunk.write_bytes(value)
        with self.assertRaises(backup.BackupError):
            self.unpack(archive, destination)
        self.assertFalse(destination.exists())

    def test_truncated_chunk_and_reordered_chunks_are_rejected(self):
        archive = self.pack()
        chunks = sorted(archive.glob("chunk-*.bin"))
        last = chunks[-1]
        last.write_bytes(last.read_bytes()[:-1])
        with self.assertRaises(backup.BackupError):
            self.unpack(archive, self.root / "restore-truncated")

        archive = self.pack("backup-reorder")
        chunks = sorted(archive.glob("chunk-*.bin"))
        self.assertGreater(len(chunks), 1)
        first_data = chunks[0].read_bytes()
        second_data = chunks[1].read_bytes()
        chunks[0].write_bytes(second_data)
        chunks[1].write_bytes(first_data)
        with self.assertRaises(backup.BackupError):
            self.unpack(archive, self.root / "restore-reordered")

    def test_rejects_unsafe_tar_paths_without_publishing_partial_files(self):
        archive = self.root / "unsafe-backup"
        def malicious_tar(_source, archive_path):
            import io
            import tarfile
            with tarfile.open(archive_path, "w:gz") as tar:
                info = tarfile.TarInfo("../escape.txt")
                payload = b"escape"
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))

        with patch.dict("os.environ", {"RECOVERY_BACKUP_PASSWORD": "long test recovery passphrase"}), \
                unittest.mock.patch.object(backup, "_create_tar", malicious_tar):
            backup.pack(self.source, archive, chunk_size=97)
        destination = self.root / "restore-unsafe"
        with self.assertRaises(backup.BackupError):
            self.unpack(archive, destination)
        self.assertFalse(destination.exists())
        self.assertFalse((self.root / "escape.txt").exists())

    def test_rejects_windows_ads_devices_and_trailing_dot_or_space_aliases(self):
        import io
        import tarfile

        unsafe_names = (
            "nested/file.txt:private-stream",
            "nested/CON.txt",
            "nested/LPT1.log",
            "nested/name.",
            "nested/name ",
            "nested/bad?.txt",
        )
        for name in unsafe_names:
            with self.subTest(name=name):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as tar:
                    info = tarfile.TarInfo(name)
                    info.size = 0
                    tar.addfile(info)
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r:") as tar:
                    with self.assertRaises(backup.BackupError):
                        backup._safe_members(tar)

    def test_pack_refuses_destination_inside_source_before_staging(self):
        destination = self.source / "backup"
        with patch.dict("os.environ", {"RECOVERY_BACKUP_PASSWORD": "long test recovery passphrase"}):
            with self.assertRaises(backup.BackupError):
                backup.pack(self.source, destination, chunk_size=97)
        self.assertFalse(destination.exists())
        self.assertEqual(sorted(path.name for path in self.source.iterdir()), ["people.json", "photo.bin"])

    def test_refuses_nonempty_destination_and_requires_environment_password(self):
        occupied = self.root / "occupied"
        occupied.mkdir()
        marker = occupied / "keep"
        marker.write_text("x")
        with patch.dict("os.environ", {"RECOVERY_BACKUP_PASSWORD": "passphrase"}):
            with self.assertRaises(backup.BackupError):
                backup.pack(self.source, occupied)
        self.assertTrue(marker.is_file())
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(backup.BackupError):
                backup.pack(self.source, self.root / "no-password")


if __name__ == "__main__":
    unittest.main()
