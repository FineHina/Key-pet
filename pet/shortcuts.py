"""Validated, persistent shortcuts; launch requests use lazily imported Qt APIs."""
import os
from pathlib import Path
from urllib.parse import urlsplit


# Documents must not become an alternate route for launching code or shortcuts.
_UNSAFE_FILE_SUFFIXES = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs",
    ".vbe", ".js", ".jse", ".wsf", ".wsh", ".lnk", ".url", ".scr",
    ".pif", ".hta", ".cpl", ".dll", ".msi", ".msp", ".msc", ".reg",
    ".py", ".pyw", ".pyc", ".pyo", ".sh", ".bash", ".zsh", ".fish",
    ".command", ".run", ".app", ".appimage", ".jar", ".pl", ".rb",
    ".php", ".ps2", ".msh", ".sct", ".scf", ".application", ".gadget",
})
_EXECUTABLE_HEADERS = (
    b"MZ", b"\x7fELF", b"#!", b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
)


def _default_opener(kind, target):
    if kind == "app":
        from PySide6.QtCore import QProcess
        result = QProcess.startDetached(target, [])
        return bool(result[0] if isinstance(result, tuple) else result)
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    url = QUrl(target) if kind == "web" else QUrl.fromLocalFile(target)
    return bool(QDesktopServices.openUrl(url))


class ShortcutsService:
    """The injected opener receives (kind, target) and returns request acceptance."""

    def __init__(self, storage, opener=None):
        self.storage = storage
        self.opener = _default_opener if opener is None else opener

    @staticmethod
    def _dict(row):
        return {"id": row["id"], "name": row["name"], "kind": row["type"],
                "target": row["target"], "position": row["position"]}

    @staticmethod
    def _validate(name, kind, target):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Shortcut name must contain 1 to 80 characters")
        if not isinstance(kind, str) or kind not in ("app", "file", "folder", "web"):
            raise ValueError("Shortcut kind must be app, file, folder or web")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("Shortcut target must be nonblank")
        name, target = name.strip(), target.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in target):
            raise ValueError("Shortcut target must not contain control characters")
        if kind == "web":
            try:
                url = urlsplit(target)
                if (url.scheme.lower() not in ("http", "https") or not url.netloc
                        or not url.hostname or url.username is not None
                        or url.password is not None or "@" in url.netloc
                        or "\\" in target or any(char.isspace() for char in target)
                        or any(char in url.netloc for char in '%<>"')):
                    raise ValueError("Web shortcuts require an HTTP(S) URL without credentials")
                # Accessing port also rejects malformed or out-of-range ports.
                url.port
            except ValueError as exc:
                raise ValueError("Web shortcuts require a valid HTTP(S) URL without credentials") from exc
            return name, kind, target

        # Reject UNC shares and Windows device namespaces before any filesystem access.
        if target.replace("\\", "/").startswith("//"):
            raise ValueError("Network and device paths are not allowed")
        path = Path(target)
        if not path.is_absolute():
            raise ValueError("Shortcut paths must be absolute")
        if kind == "app" and path.suffix.lower() != ".exe":
            raise ValueError("App shortcuts require an .exe file without arguments")
        if kind == "file" and path.suffix.lower() in _UNSAFE_FILE_SUFFIXES:
            raise ValueError("Executable and script files are not allowed as documents")
        if os.name == "nt":
            if ":" in path.drive[2:] or any(":" in part for part in path.parts[1:]):
                raise ValueError("Alternate streams and device paths are not allowed")
            if any(part.endswith((" ", ".")) for part in path.parts[1:]):
                raise ValueError("Ambiguous Windows paths are not allowed")
        try:
            path = path.resolve(strict=True)
            if str(path).replace("\\", "/").startswith("//"):
                raise ValueError("Network paths are not allowed")
            if kind == "folder":
                if not path.is_dir():
                    raise ValueError("Folder shortcuts require an existing directory")
            elif not path.is_file():
                raise ValueError("App and file shortcuts require an existing regular file")
            elif kind == "app":
                if path.suffix.lower() != ".exe":
                    raise ValueError("App shortcuts require an .exe file without arguments")
            else:
                if path.suffix.lower() in _UNSAFE_FILE_SUFFIXES:
                    raise ValueError("Executable and script files are not allowed as documents")
                if os.name != "nt" and os.access(path, os.X_OK):
                    raise ValueError("Executable files are not allowed as documents")
                with path.open("rb") as stream:
                    header = stream.read(128).lstrip(b"\xef\xbb\xbf \t\r\n")
                if header.startswith(_EXECUTABLE_HEADERS) or header.lower().startswith(b"<?php"):
                    raise ValueError("Executable and script files are not allowed as documents")
        except (OSError, RuntimeError) as exc:
            raise ValueError("Shortcut target must be an accessible existing local path") from exc
        return name, kind, str(path)

    def _get(self, id):
        if isinstance(id, bool) or not isinstance(id, int) or id < 1:
            raise ValueError("Shortcut id must be a positive integer")
        row = self.storage.conn.execute("SELECT * FROM shortcuts WHERE id = ?", (id,)).fetchone()
        if row is None:
            raise ValueError("Shortcut not found")
        return row

    def list_all(self):
        return [self._dict(row) for row in self.storage.conn.execute(
            "SELECT * FROM shortcuts ORDER BY position, id")]

    def add(self, name, kind, target):
        name, kind, target = self._validate(name, kind, target)
        with self.storage.transaction():
            position = self.storage.conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM shortcuts").fetchone()[0]
            cursor = self.storage.conn.execute(
                "INSERT INTO shortcuts (name, type, target, position) VALUES (?, ?, ?, ?)",
                (name, kind, target, position))
            return self._dict(self._get(cursor.lastrowid))

    def edit(self, id, name, kind, target):
        name, kind, target = self._validate(name, kind, target)
        with self.storage.transaction():
            self._get(id)
            self.storage.conn.execute(
                "UPDATE shortcuts SET name = ?, type = ?, target = ? WHERE id = ?",
                (name, kind, target, id))
            return self._dict(self._get(id))

    def delete(self, id):
        """Delete only the configuration row, never the target or its contents."""
        with self.storage.transaction():
            self._get(id)
            self.storage.conn.execute("DELETE FROM shortcuts WHERE id = ?", (id,))

    def reorder(self, id, direction):
        """Swap with the adjacent configured shortcut; boundaries are no-ops."""
        if isinstance(direction, bool) or not isinstance(direction, int) or direction not in (-1, 1):
            raise ValueError("Shortcut direction must be -1 or +1")
        with self.storage.transaction():
            self._get(id)
            items = self.list_all()
            index = next(index for index, item in enumerate(items) if item["id"] == id)
            neighbor = index + direction
            if 0 <= neighbor < len(items):
                item, other = items[index], items[neighbor]
                self.storage.conn.executemany("UPDATE shortcuts SET position = ? WHERE id = ?",
                                             ((other["position"], id), (item["position"], other["id"])))
            return self.list_all()

    def check(self, id):
        """Validate the stored target without opening or modifying it."""
        item = self._dict(self._get(id))
        return self._validate(item["name"], item["kind"], item["target"])[2]

    def open(self, id):
        """Return success for an accepted launch request, not application completion."""
        item = self._dict(self._get(id))
        _, kind, target = self._validate(item["name"], item["kind"], item["target"])
        try:
            accepted = self.opener(kind, target)
        except Exception as exc:
            raise ValueError("Shortcut launch request failed") from exc
        if not accepted:
            raise ValueError("Shortcut launch request was not accepted")
        return dict(item, status="success")
