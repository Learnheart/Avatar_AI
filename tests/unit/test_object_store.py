"""The object store, on a real directory."""
import os

import pytest

from backend.utils.object_store import (
    InvalidObjectKey,
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
)

DATA = bytes(range(256)) * 3


@pytest.fixture
def store(config) -> ObjectStore:
    return ObjectStore(config.OBJECT_STORE_DIR)


def test_put_then_get_returns_the_same_bytes(store):
    store.put("imports/abc", DATA)

    assert store.get("imports/abc") == DATA


def test_the_directory_is_created_by_the_first_put(config, tmp_path):
    assert not (tmp_path / "objects").exists()

    ObjectStore(config.OBJECT_STORE_DIR).put("imports/abc", DATA)

    assert (tmp_path / "objects" / "imports" / "abc").read_bytes() == DATA


def test_a_second_put_replaces_the_object_and_leaves_no_other_file(store, tmp_path):
    store.put("imports/abc", DATA)

    store.put("imports/abc", b"new")

    assert store.get("imports/abc") == b"new"
    assert os.listdir(tmp_path / "objects" / "imports") == ["abc"]


def test_delete_removes_the_object_and_a_second_delete_is_harmless(store):
    store.put("imports/abc", DATA)

    store.delete("imports/abc")
    store.delete("imports/abc")
    store.delete("never/stored")

    with pytest.raises(ObjectNotFound):
        store.get("imports/abc")


def test_a_missing_key_is_not_found(store):
    store.put("imports/abc", DATA)

    for key in ("imports/other", "imports", "imports/abc/deeper", "nothing/here"):
        with pytest.raises(ObjectNotFound):
            store.get(key)


@pytest.mark.parametrize(
    "key",
    [
        "", "/etc/passwd", "../outside", "imports/../../outside", "imports/..", ".",
        "imports/./abc", "imports//abc", "imports/abc/", "imports\\abc", "..\\outside",
        "C:\\outside", "imports/abc\x00", "imports/abc.part",
    ],
)
def test_a_key_cannot_leave_the_store_directory(store, tmp_path, key):
    (tmp_path / "outside").write_bytes(b"not an object")

    for call in (lambda: store.put(key, DATA), lambda: store.get(key), lambda: store.delete(key)):
        with pytest.raises(InvalidObjectKey):
            call()

    assert (tmp_path / "outside").read_bytes() == b"not an object"
    assert not (tmp_path / "objects").exists()


def test_a_link_inside_the_directory_does_not_lead_out_of_it(store, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "secret").write_bytes(b"secret")
    (tmp_path / "objects").mkdir()
    (tmp_path / "objects" / "link").symlink_to(tmp_path / "elsewhere")

    with pytest.raises(InvalidObjectKey):
        store.get("link/secret")
    with pytest.raises(InvalidObjectKey):
        store.put("link/new", DATA)
    assert not (tmp_path / "elsewhere" / "new").exists()


def test_the_objects_survive_a_new_instance_on_the_same_directory(config):
    ObjectStore(config.OBJECT_STORE_DIR).put("imports/abc", DATA)

    assert ObjectStore(config.OBJECT_STORE_DIR).get("imports/abc") == DATA


def test_a_directory_that_cannot_be_written_is_a_store_error(tmp_path):
    (tmp_path / "file").write_bytes(b"in the way")
    store = ObjectStore(str(tmp_path / "file" / "objects"))

    with pytest.raises(ObjectStoreError):
        store.put("imports/abc", DATA)
