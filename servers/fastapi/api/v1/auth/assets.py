from urllib.parse import unquote, urlsplit
import uuid
import os

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles
from api.v1.auth.context import get_current_owner_id, get_current_owner_is_admin


PRIVATE_APP_DATA_ROOTS = {
    "images",
    "exports",
    "uploads",
    "pptx-to-html",
    "pptx-to-json",
}
SHARED_APP_DATA_ROOTS = {"fonts", "templates"}


class _OwnerScopedStaticFiles(StaticFiles):
    def __init__(self, asset_root: str, **kwargs):
        super().__init__(**kwargs)
        self.asset_root = asset_root

    def lookup_path(self, path: str):
        full_path, file_stat = super().lookup_path(path)
        owner_id = get_current_owner_id()
        if full_path and owner_id is not None:
            # Authorize the exact file StaticFiles will serve. Repeated URL
            # decoding at a proxy must not select a different filesystem alias.
            root = os.path.realpath(self.directory)
            relative = os.path.relpath(full_path, root)
            parts = (self.asset_root, *relative.split(os.sep))
            if not _namespace_authorized(parts, user_id=owner_id, is_admin=get_current_owner_is_admin()):
                return "", None
        return full_path, file_stat


def mount_app_data_assets(app: FastAPI, app_data_dir: str) -> None:
    """Expose assets only; each mount independently rejects symlink escape."""
    for root in sorted(PRIVATE_APP_DATA_ROOTS | SHARED_APP_DATA_ROOTS):
        directory = os.path.join(app_data_dir, root)
        os.makedirs(directory, exist_ok=True)
        app.mount(f"/app_data/{root}", _OwnerScopedStaticFiles(root, directory=directory), name=f"app_data_{root}")


def normalized_app_data_parts(path_or_uri: str) -> tuple[str, ...] | None:
    """Return a traversal-safe app_data path split into URL path segments."""
    if not isinstance(path_or_uri, str) or not path_or_uri:
        return None

    try:
        path = urlsplit(path_or_uri).path
    except ValueError:
        return None

    # Proxies and ASGI servers do not all expose encoded paths consistently.
    # Decode repeatedly, with a hard cap that fails closed for pathological
    # nesting, so encoded traversal cannot cross this boundary.
    for _ in range(8):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    else:
        if unquote(path) != path:
            return None

    if "\x00" in path or "\\" in path or not path.startswith("/app_data/"):
        return None

    parts = tuple(path.split("/")[2:])
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts


def is_app_data_path_authorized(
    path_or_uri: str,
    *,
    user_id: uuid.UUID,
    is_admin: bool,
) -> bool:
    """Authorize both the visible namespace and its canonical storage target."""
    parts = normalized_app_data_parts(path_or_uri)
    if not parts or not _namespace_authorized(parts, user_id=user_id, is_admin=is_admin):
        return False

    app_data = os.getenv("APP_DATA_DIRECTORY")
    if not app_data:
        return True
    try:
        # Asset roots may themselves be symlinked to configured storage volumes.
        # Authorize aliases relative to that root, not the whole app-data parent.
        root = os.path.realpath(os.path.join(app_data, parts[0]))
        target = os.path.realpath(os.path.join(app_data, *parts))
        if os.path.commonpath([root, target]) != root:
            return False
        relative = os.path.relpath(target, root)
    except (OSError, ValueError):
        return False
    canonical_parts = (parts[0], *relative.split(os.sep))
    return _namespace_authorized(canonical_parts, user_id=user_id, is_admin=is_admin)


def _namespace_authorized(parts: tuple[str, ...], *, user_id: uuid.UUID, is_admin: bool) -> bool:

    root = parts[0]
    if root in SHARED_APP_DATA_ROOTS:
        return True
    if root not in PRIVATE_APP_DATA_ROOTS or len(parts) < 2:
        return False

    relative_parts = parts[1:]
    if relative_parts[0] == "users":
        return (
            len(relative_parts) >= 3
            and relative_parts[1] == str(user_id)
        )

    # Pre-multi-user assets remain in the legacy root and belong only to the
    # migrated primary administrator.
    return is_admin
