import pytest

from app.services.storage import LocalStorage, R2Storage, StorageError, new_key


def test_new_key_shape_and_randomness():
    a, b = new_key("uploads", "docx"), new_key("uploads", "docx")
    assert a != b and a.startswith("uploads/") and a.endswith(".docx")


def test_local_roundtrip_stream_delete(tmp_path):
    st = LocalStorage(tmp_path / "s")
    src = tmp_path / "x.bin"
    src.write_bytes(b"hello" * 100000)
    key = new_key("uploads", "png")
    st.put_file(key, src, "image/png")
    assert st.exists(key)
    size, chunks = st.open_stream(key)
    assert size == 500000 and b"".join(chunks) == src.read_bytes()
    assert st.read_bytes(key) == src.read_bytes()
    st.delete(key)
    assert not st.exists(key)
    with pytest.raises(StorageError):
        st.open_stream(key)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "uploads/../x.docx", "/abs/path.docx", "uploads/short.docx",
                                 "other/" + "a" * 32 + ".docx", "uploads/" + "A" * 32 + ".docx", ""])
def test_local_rejects_unsafe_keys(tmp_path, bad):
    st = LocalStorage(tmp_path / "s")
    with pytest.raises(StorageError):
        st.exists(bad)
    with pytest.raises(StorageError):
        st.open_stream(bad)


def test_read_bytes_size_cap(tmp_path):
    st = LocalStorage(tmp_path / "s")
    src = tmp_path / "x.bin"
    src.write_bytes(b"x" * 100)
    key = new_key("converted", "pdf")
    st.put_file(key, src, "application/pdf")
    with pytest.raises(StorageError):
        st.read_bytes(key, max_bytes=10)


def test_r2_requires_all_settings():
    pytest.importorskip("boto3")
    with pytest.raises(RuntimeError):
        R2Storage("", "bucket", "k", "s")


def test_r2_against_mock_s3(tmp_path):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="sesh-test")
        st = R2Storage("https://s3.us-east-1.amazonaws.com", "sesh-test", "test", "test")
        st.client = boto3.client("s3", region_name="us-east-1")  # point the mock at the same in-memory service
        src = tmp_path / "x.pdf"
        src.write_bytes(b"%PDF-1.4 data")
        key = new_key("converted", "pdf")
        st.put_file(key, src, "application/pdf")
        assert st.exists(key)
        assert st.read_bytes(key) == b"%PDF-1.4 data"
        st.delete(key)
        assert not st.exists(key)
        with pytest.raises(StorageError):
            st.open_stream(key)
