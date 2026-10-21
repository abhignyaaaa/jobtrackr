"""Exercise the S3 storage backend against a mocked S3 (moto)."""
import boto3
import pytest

moto = pytest.importorskip("moto")


def test_s3_backend_roundtrip_and_presigned_url(monkeypatch):
    from app.services import storage as st

    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="jt-private")
        monkeypatch.setattr(st.settings.__class__, "s3_bucket", property(lambda s: "jt-private"), raising=False)
        object.__setattr__(st.settings, "s3_region", "us-east-1")
        object.__setattr__(st.settings, "s3_endpoint_url", None)
        object.__setattr__(st.settings, "s3_access_key_id", "test")
        object.__setattr__(st.settings, "s3_secret_access_key", "test")
        s3 = st.S3Storage()
        s3.bucket = "jt-private"
        key = st.new_key(7, "pdf")
        s3.put(key, b"%PDF-1.4 hello", "application/pdf")
        assert s3.get(key) == b"%PDF-1.4 hello"
        url = s3.presigned_url(key, "cv.pdf", "application/pdf")
        assert "X-Amz-Expires=60" in url and "X-Amz-Signature" in url
        # bucket objects are private: anonymous ACL has no public grant
        acl = s3.client.get_object_acl(Bucket="jt-private", Key=key)
        assert not any(g["Grantee"].get("URI", "").endswith("AllUsers") for g in acl["Grants"])
        s3.delete(key)
        with pytest.raises(Exception):
            s3.get(key)
