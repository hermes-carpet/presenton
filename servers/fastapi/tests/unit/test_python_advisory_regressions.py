import pytest


def test_authlib_rejects_unsigned_json_jws_and_preserves_signed_messages():
    from authlib.jose import JsonWebSignature
    from authlib.jose.errors import JoseError

    jws = JsonWebSignature(algorithms=["HS256"])
    key = b"presenton-regression-control-key-32-bytes"
    payload = b'{"sub":"security-control"}'
    signed = jws.serialize_compact({"alg": "HS256"}, payload, key)
    assert jws.deserialize_compact(signed, key)["payload"] == payload
    with pytest.raises(JoseError):
        jws.deserialize_compact(signed, b"different-control-key-with-32-bytes")
    with pytest.raises((JoseError, ValueError)):
        jws.deserialize_json({"payload": "eyJyb2xlIjoiYWRtaW4ifQ", "signatures": []}, key)


def test_authlib_rejects_empty_hmac_keys_and_non_loopback_cleartext_urls():
    from authlib.common.security import is_secure_transport
    from authlib.jose import JsonWebSignature
    from authlib.jose.errors import JoseError

    with pytest.raises((JoseError, ValueError)):
        JsonWebSignature(algorithms=["HS256"]).serialize_compact({"alg": "HS256"}, b"payload", b"")
    assert is_secure_transport("https://provider.example/token")
    assert is_secure_transport("http://localhost:8000/token")
    assert is_secure_transport("http://127.0.0.1:8000/token")
    for url in ("http://localhost:80@external.example/callback", "http://127.0.0.1:80@external.example/callback"):
        assert not is_secure_transport(url)
