"""Encrypted local index for imported copies in the Android app sandbox."""
import base64
import hashlib
import hmac
import json
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from cryptography.fernet import Fernet, InvalidToken

ITERATIONS = 250_000

class VaultStore:
    def __init__(self, user_dir):
        self.root = Path(user_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_dir = self.root / "saved_items"
        self.files_dir.mkdir(exist_ok=True)
        self.config = self.root / "settings.json"
        self.vault_file = self.root / "vault.bin"
        self.entries = []
        self.key = None
        self.salt = None

    def setup(self, code):
        self.salt = __import__("os").urandom(16)
        self.key = hashlib.pbkdf2_hmac("sha256", code.encode(), self.salt, ITERATIONS, 32)
        digest = hashlib.sha256(b"wx-verify" + self.key).hexdigest()
        self.config.write_text(json.dumps({"salt": base64.b64encode(self.salt).decode(), "verifier": digest}), encoding="utf-8")
        self._save()

    def unlock(self, code):
        try:
            cfg = json.loads(self.config.read_text(encoding="utf-8"))
            salt = base64.b64decode(cfg["salt"])
            key = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, ITERATIONS, 32)
            if not hmac.compare_digest(hashlib.sha256(b"wx-verify" + key).hexdigest(), cfg["verifier"]):
                return False
            self.salt, self.key = salt, key
            self.entries = json.loads(self._fernet().decrypt(self.vault_file.read_bytes()).decode()) if self.vault_file.exists() else []
            return True
        except (OSError, ValueError, KeyError, InvalidToken):
            return False

    def _fernet(self):
        return Fernet(base64.urlsafe_b64encode(self.key))

    def _save(self):
        if self.key:
            blob = self._fernet().encrypt(json.dumps(self.entries).encode())
            tmp = self.vault_file.with_suffix(".tmp")
            tmp.write_bytes(blob)
            tmp.replace(self.vault_file)

    def add_copy(self, source):
        src = Path(source)
        if not src.exists() or not (src.is_file() or src.is_dir()):
            raise FileNotFoundError("That item is no longer available.")
        eid = uuid.uuid4().hex[:12]
        folder = self.files_dir / eid
        folder.mkdir()
        target = folder / src.name
        try:
            if src.is_dir():
                shutil.copytree(src, target, symlinks=True)
                kind = "Folder"
            else:
                shutil.copy2(src, target)
                kind = "File"
            self.entries.append({"id": eid, "name": src.name, "path": str(target), "kind": kind,
                                 "added": datetime.now().strftime("%Y-%m-%d %H:%M"), "note": ""})
            self._save()
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def add_archive(self, source):
        """Safely unpack a user-selected ZIP as a managed folder copy."""
        src = Path(source)
        if not zipfile.is_zipfile(src):
            raise ValueError("Choose a valid ZIP archive.")
        eid = uuid.uuid4().hex[:12]
        folder = self.files_dir / eid
        folder.mkdir()
        name = src.stem or "Imported folder"
        target = folder / name
        target.mkdir()
        try:
            with zipfile.ZipFile(src) as archive:
                for item in archive.infolist():
                    rel = PurePosixPath(item.filename)
                    if rel.is_absolute() or ".." in rel.parts:
                        raise ValueError("The ZIP contains an unsafe path.")
                    out = target.joinpath(*rel.parts)
                    if target.resolve() not in out.resolve().parents and out.resolve() != target.resolve():
                        raise ValueError("The ZIP contains an unsafe path.")
                    if item.is_dir():
                        out.mkdir(parents=True, exist_ok=True)
                    else:
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item) as inp, out.open("wb") as dest:
                            shutil.copyfileobj(inp, dest)
            self.entries.append({"id": eid, "name": name, "path": str(target), "kind": "Folder",
                                 "added": datetime.now().strftime("%Y-%m-%d %H:%M"), "note": ""})
            self._save()
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def create(self, name, kind):
        eid = uuid.uuid4().hex[:12]
        folder = self.files_dir / eid
        folder.mkdir()
        path = folder / name
        if kind == "Folder":
            path.mkdir()
        else:
            if not path.suffix:
                path = path.with_suffix(".txt")
            path.write_text("", encoding="utf-8")
        self.entries.append({"id": eid, "name": path.name, "path": str(path), "kind": kind,
                             "added": datetime.now().strftime("%Y-%m-%d %H:%M"), "note": ""})
        self._save()

    def remove(self, ids):
        ids = set(ids)
        gone = [e for e in self.entries if e["id"] in ids]
        self.entries = [e for e in self.entries if e["id"] not in ids]
        root = self.files_dir.resolve()
        for entry in gone:
            path = Path(entry["path"]).resolve()
            if root in path.parents:
                shutil.rmtree(path.parent, ignore_errors=True)
        self._save()
