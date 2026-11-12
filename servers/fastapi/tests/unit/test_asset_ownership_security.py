import os
import uuid

import pytest
from fastapi import HTTPException

from api.v1.auth.assets import is_app_data_path_authorized
from api.v1.auth.context import (
    reset_current_owner_id,
    reset_current_owner_is_admin,
    set_current_owner_id,
    set_current_owner_is_admin,
)
from services.export_task_service import ExportTaskService
from templates import fonts_and_slides_preview
from utils.asset_directory_utils import resolve_app_path_to_filesystem


@pytest.mark.parametrize("is_admin", [False, True])
@pytest.mark.parametrize("directory_alias", [False, True])
def test_asset_aliases_cannot_cross_owner_namespaces(monkeypatch, tmp_path, is_admin, directory_alias):
    app_data = tmp_path / "app_data"
    owner, other = uuid.uuid4(), uuid.uuid4()
    own = app_data / "uploads" / "users" / str(owner) / "own.pptx"
    secret = app_data / "uploads" / "users" / str(other) / "secret.pptx"
    own.parent.mkdir(parents=True)
    secret.parent.mkdir(parents=True)
    own.write_bytes(b"own")
    secret.write_bytes(b"secret")
    alias = own.parent / "alias"
    if directory_alias:
        alias.symlink_to(secret.parent, target_is_directory=True)
        alias = alias / "secret.pptx"
    else:
        alias = alias.with_suffix(".pptx")
        alias.symlink_to(secret)
    own_alias = own.parent / "own-alias.pptx"
    own_alias.symlink_to(own)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    owner_token = set_current_owner_id(owner)
    admin_token = set_current_owner_is_admin(is_admin)
    try:
        own_url = f"/app_data/uploads/users/{owner}/own-alias.pptx"
        assert resolve_app_path_to_filesystem(own_url) == os.path.realpath(own)
        alias_url = "/app_data/" + alias.relative_to(app_data).as_posix()
        for value in (alias_url, "http://localhost:8000" + alias_url, str(alias), alias.as_uri(), "http://localhost:8000" + str(alias)):
            assert resolve_app_path_to_filesystem(value) is None, value
        assert not is_app_data_path_authorized(alias_url, user_id=owner, is_admin=is_admin)
    finally:
        reset_current_owner_is_admin(admin_token)
        reset_current_owner_id(owner_token)


def test_canonical_owner_checks_preserve_symlinked_asset_volume_roots(monkeypatch, tmp_path):
    app_data, volume = tmp_path / "app_data", tmp_path / "uploads-volume"
    app_data.mkdir()
    owner = uuid.uuid4()
    own = volume / "users" / str(owner) / "own.pptx"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"own")
    (app_data / "uploads").symlink_to(volume, target_is_directory=True)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    token = set_current_owner_id(owner)
    try:
        assert resolve_app_path_to_filesystem(f"/app_data/uploads/users/{owner}/own.pptx") == os.path.realpath(own)
    finally:
        reset_current_owner_id(token)


def test_static_asset_requests_reject_cross_owner_aliases(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import middlewares
    from api.v1.auth.assets import mount_app_data_assets
    from api.v1.auth.principal import AuthPrincipal

    owner, other = uuid.uuid4(), uuid.uuid4()
    own = tmp_path / "uploads" / "users" / str(owner) / "own.pptx"
    secret = tmp_path / "uploads" / "users" / str(other) / "secret.pptx"
    own.parent.mkdir(parents=True)
    secret.parent.mkdir(parents=True)
    own.write_bytes(b"own")
    secret.write_bytes(b"secret")
    (own.parent / "alias.pptx").symlink_to(secret)
    (own.parent / "canonical.pptx").write_bytes(b"own")
    (own.parent / "%63anonical.pptx").symlink_to(secret)
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))

    class Session:
        async def scalar(self, _query): return 1

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *_args): return None

    async def principal(*_args): return AuthPrincipal(owner, "member", False, "jwt"), object()
    async def no_proxy(*_args, **_kwargs): return None

    monkeypatch.setattr(middlewares, "is_disable_auth_enabled", lambda: False)
    monkeypatch.setattr(middlewares, "async_session_maker", Context)
    monkeypatch.setattr(middlewares, "resolve_request_principal", principal)
    monkeypatch.setattr(middlewares, "maybe_proxy_presenton_cloud_request", no_proxy)
    app = FastAPI()
    app.add_middleware(middlewares.SessionAuthMiddleware)
    mount_app_data_assets(app, str(tmp_path))
    with TestClient(app) as client:
        assert client.get(f"/app_data/uploads/users/{owner}/own.pptx").content == b"own"
        assert client.get(f"/app_data/uploads/users/{owner}/alias.pptx").status_code == 404
        # TestClient performs one more decode than a raw ASGI HTTP transport.
        assert client.get(f"/app_data/uploads/users/{owner}/%2563anonical.pptx").content == b"own"
        assert client.get(f"/app_data/uploads/users/{owner}/%252563anonical.pptx").status_code == 404


def test_browser_asset_paths_are_owner_scoped_and_traversal_safe():
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()

    assert is_app_data_path_authorized(
        f"/app_data/images/users/{owner_id}/slide.png",
        user_id=owner_id,
        is_admin=False,
    )
    assert not is_app_data_path_authorized(
        f"/app_data/images/users/{other_id}/slide.png",
        user_id=owner_id,
        is_admin=False,
    )
    assert not is_app_data_path_authorized(
        "/app_data/images/%252e%252e/userConfig.json",
        user_id=owner_id,
        is_admin=True,
    )
    assert not is_app_data_path_authorized(
        "/app_data/images/%2525252525252525252e%2525252525252525252e"
        "/userConfig.json",
        user_id=owner_id,
        is_admin=True,
    )
    assert not is_app_data_path_authorized(
        "/app_data/images/legacy.png",
        user_id=owner_id,
        is_admin=False,
    )
    assert is_app_data_path_authorized(
        "/app_data/images/legacy.png",
        user_id=owner_id,
        is_admin=True,
    )


def test_server_side_file_resolution_rejects_other_users_and_symlink_escape(
    monkeypatch, tmp_path
):
    app_data = tmp_path / "app_data"
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    own_file = app_data / "uploads" / "users" / str(owner_id) / "own.pptx"
    other_file = app_data / "uploads" / "users" / str(other_id) / "secret.pptx"
    own_file.parent.mkdir(parents=True)
    other_file.parent.mkdir(parents=True)
    own_file.write_bytes(b"own")
    other_file.write_bytes(b"secret")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    owner_token = set_current_owner_id(owner_id)
    admin_token = set_current_owner_is_admin(False)
    try:
        assert resolve_app_path_to_filesystem(
            f"/app_data/uploads/users/{owner_id}/own.pptx"
        ) == os.path.realpath(own_file)
        assert (
            resolve_app_path_to_filesystem(
                f"/app_data/uploads/users/{other_id}/secret.pptx"
            )
            is None
        )
        assert (
            resolve_app_path_to_filesystem(
                "/app_data/uploads/users/"
                f"{owner_id}/../../{other_id}/secret.pptx"
            )
            is None
        )
    finally:
        reset_current_owner_is_admin(admin_token)
        reset_current_owner_id(owner_token)


def test_server_side_file_resolution_allows_packaged_nextjs_fonts():
    resolved = resolve_app_path_to_filesystem(
        "/vendor/fonts/sans_serif/poppins/Poppins-Regular.ttf"
    )

    assert resolved is not None
    assert resolved.replace(os.sep, "/").endswith(
        "servers/nextjs/public/vendor/fonts/sans_serif/poppins/Poppins-Regular.ttf"
    )


def test_template_preview_pptx_remains_resolvable_for_its_owner(
    monkeypatch,
    tmp_path,
):
    app_data = tmp_path / "app_data"
    owner_id = uuid.uuid4()
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    owner_token = set_current_owner_id(owner_id)
    try:
        session_dir = fonts_and_slides_preview._get_template_preview_session_dir(
            uuid.uuid4()
        )
        pptx_path = os.path.join(session_dir, "presentation.pptx")
        with open(pptx_path, "wb") as pptx_file:
            pptx_file.write(b"pptx")
        (pptx_url,) = fonts_and_slides_preview._public_urls_for_local_paths(
            [pptx_path]
        )

        assert pptx_url.startswith(f"/app_data/uploads/users/{owner_id}/")
        assert resolve_app_path_to_filesystem(pptx_url) == os.path.realpath(
            pptx_path
        )
    finally:
        reset_current_owner_id(owner_token)


def test_conversion_artifacts_are_moved_into_the_owner_namespace(
    monkeypatch, tmp_path
):
    app_data = tmp_path / "app_data"
    owner_id = uuid.uuid4()
    source_dir = app_data / "pptx-to-json" / "session-1"
    output_path = source_dir / "presentation.json"
    image_path = source_dir / "images" / "slide.png"
    image_path.parent.mkdir(parents=True)
    output_path.write_text("{}", encoding="utf-8")
    image_path.write_bytes(b"image")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    owner_token = set_current_owner_id(owner_id)
    try:
        rewritten = ExportTaskService._scope_conversion_artifacts(
            str(output_path),
            {
                "layouts": [
                    {
                        "image": "/app_data/pptx-to-json/session-1/images/slide.png"
                    }
                ]
            },
            "pptx-to-json",
        )
    finally:
        reset_current_owner_id(owner_token)

    target_dir = (
        app_data / "pptx-to-json" / "users" / str(owner_id) / "session-1"
    )
    assert not source_dir.exists()
    assert (target_dir / "images" / "slide.png").is_file()
    assert rewritten["layouts"][0]["image"] == (
        f"/app_data/pptx-to-json/users/{owner_id}/session-1/images/slide.png"
    )


def test_conversion_relative_assets_are_absolutized_before_owner_scoping(
    monkeypatch, tmp_path
):
    app_data = tmp_path / "app_data"
    source_dir = app_data / "pptx-to-json" / "session-1"
    output_path = source_dir / "presentation.json"
    image_path = source_dir / "images" / "slide.png"
    image_path.parent.mkdir(parents=True)
    output_path.write_text("{}", encoding="utf-8")
    image_path.write_bytes(b"image")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    rewritten = ExportTaskService._absolutize_conversion_asset_urls(
        str(output_path),
        {
            "layouts": [
                {
                    "elements": [
                        {"type": "image", "data": "images/slide.png"},
                        {"type": "image", "data": "images/../../secret.png"},
                    ]
                }
            ]
        },
        "pptx-to-json",
    )

    elements = rewritten["layouts"][0]["elements"]
    assert elements[0]["data"] == (
        "/app_data/pptx-to-json/session-1/images/slide.png"
    )
    assert elements[1]["data"] == "images/../../secret.png"


def test_conversion_artifacts_cannot_move_an_unrelated_app_data_directory(
    monkeypatch, tmp_path
):
    app_data = tmp_path / "app_data"
    owner_id = uuid.uuid4()
    unrelated_dir = app_data / "uploads" / "users" / str(uuid.uuid4())
    output_path = unrelated_dir / "presentation.json"
    unrelated_dir.mkdir(parents=True)
    output_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    owner_token = set_current_owner_id(owner_id)
    try:
        with pytest.raises(HTTPException, match="outside its asset directory"):
            ExportTaskService._scope_conversion_artifacts(
                str(output_path),
                {},
                "pptx-to-json",
            )
    finally:
        reset_current_owner_id(owner_token)

    assert output_path.is_file()


def test_export_cannot_move_another_users_file(monkeypatch, tmp_path):
    app_data = tmp_path / "app_data"
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    other_export = (
        app_data / "exports" / "users" / str(other_id) / "private.pptx"
    )
    other_export.parent.mkdir(parents=True)
    other_export.write_bytes(b"private")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    owner_token = set_current_owner_id(owner_id)
    try:
        with pytest.raises(HTTPException, match="outside its asset directory"):
            ExportTaskService._move_export_to_owner(str(other_export))
    finally:
        reset_current_owner_id(owner_token)

    assert other_export.read_bytes() == b"private"
