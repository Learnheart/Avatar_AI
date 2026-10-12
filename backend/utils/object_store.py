"""
The object store: bytes kept unchanged under a key (the originals of what users import).

One store for all users. It knows nothing about users: who owns an object is recorded in
the application database only, and an object is never served by its key. The backend is a
directory (`OBJECT_STORE_DIR`); an S3-compatible store replaces this one class.

Every method blocks; call them off the event loop.
"""
import os
import uuid
from pathlib import Path

_TEMPORARY_SUFFIX = ".part"


class ObjectStoreError(Exception):
    """The object store failed (the directory cannot be written or read)."""


class ObjectNotFound(ObjectStoreError):
    """No object is stored under the key."""


class InvalidObjectKey(ValueError):
    """The key is not a relative path of plain names: it could leave the store."""


class ObjectStore:
    def __init__(self, directory: str) -> None:
        self._root = Path(directory)

    def put(self, key: str, data: bytes) -> None:
        """Store the bytes under the key, replacing what was there. The object appears
        whole or not at all: it is written beside its place, then renamed."""
        path = self._path(key)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}{_TEMPORARY_SUFFIX}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(temporary, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as e:
            raise ObjectStoreError(f"Cannot store the object '{key}': {e}") from e

    def get(self, key: str) -> bytes:
        """The bytes stored under the key. Raises ObjectNotFound."""
        path = self._path(key)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as e:
            raise ObjectNotFound(f"No object '{key}'") from e
        except OSError as e:
            raise ObjectStoreError(f"Cannot read the object '{key}': {e}") from e

    def delete(self, key: str) -> None:
        """Remove the object. A key that holds nothing is not an error."""
        path = self._path(key)
        try:
            path.unlink(missing_ok=True)
        except NotADirectoryError:
            return
        except OSError as e:
            raise ObjectStoreError(f"Cannot delete the object '{key}': {e}") from e

    def _path(self, key: str) -> Path:
        """Where the key lives, inside the directory. Raises InvalidObjectKey."""
        parts = key.split("/")
        if (
            not key
            or "\\" in key
            or "\x00" in key
            or any(part in ("", ".", "..") for part in parts)
            or parts[-1].endswith(_TEMPORARY_SUFFIX)
            or Path(key).is_absolute()
        ):
            raise InvalidObjectKey(f"Not an object key: {key!r}")
        root = self._root.resolve()
        path = (root / key).resolve()
        # A link placed inside the directory must not lead out of it either.
        if not path.is_relative_to(root):
            raise InvalidObjectKey(f"Not an object key: {key!r}")
        return path
