import errno
import os
from types import SimpleNamespace

import pytest

from services.export_task_service import ExportTaskService
from utils import export_permissions

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX mode and group assertions")


def mode(path):
    return path.stat().st_mode & 0o777


def test_export_and_conversion_sidecars_have_group_only_read_access(tmp_path):
    root = tmp_path / "data"
    session = root / "pptx-to-html" / "session"
    (session / "assets").mkdir(parents=True)
    root.chmod(0o755)
    for name in ("index.html", "assets/image.png", "assets/font.woff2"):
        (session / name).write_text(name)
    export_permissions.finalize_export_permissions(str(session / "index.html"), [str(root)], recursive=True)
    for folder in (root, session.parent, session, session / "assets"):
        assert mode(folder) == 0o750
    for name in ("index.html", "assets/image.png", "assets/font.woff2"):
        assert mode(session / name) == 0o640
        assert (session / name).stat().st_gid == export_permissions.export_reader_gid()
        assert (session / name).read_text() == name


def test_managed_private_and_shared_volume_boundaries_are_preserved(tmp_path):
    for permissions in (0o700, 0o2770):
        root = tmp_path / str(permissions)
        (root / "exports").mkdir(parents=True)
        root.chmod(permissions)
        output = root / "exports" / "deck.pdf"
        output.write_text("pdf")
        original_mode = root.stat().st_mode & 0o7777
        export_permissions.finalize_export_permissions(str(output), [str(root)])
        assert root.stat().st_mode & 0o7777 == original_mode
        assert mode(output) == 0o640


def test_symlinks_and_outputs_outside_managed_roots_do_not_change_targets(tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    external.chmod(0o755)
    victim = external / "secret"
    victim.write_text("secret")
    victim.chmod(0o600)
    (root / "link").symlink_to(external, target_is_directory=True)
    export_permissions.finalize_export_permissions(str(root), [str(root)], recursive=True)
    export_permissions.finalize_export_permissions(str(root / "link"), [str(root)], recursive=True)
    assert mode(victim) == 0o600
    assert mode(external) == 0o755
    with pytest.raises(OSError, match="outside managed storage"):
        export_permissions.finalize_export_permissions(str(victim), [str(root)])


@pytest.mark.parametrize("owner", [None, "owner-123"])
def test_conversion_scoping_secures_entire_session_and_preserves_asset_urls(tmp_path, monkeypatch, owner):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr("services.export_task_service.get_current_owner_id", lambda: owner)
    session = tmp_path / "pptx-to-html" / "session"
    (session / "assets").mkdir(parents=True)
    output = session / "index.html"
    output.write_text('<img src="assets/image.png">')
    (session / "assets" / "image.png").write_bytes(b"png")
    result = ExportTaskService._scope_conversion_artifacts(str(output), {"url": "/app_data/pptx-to-html/session/assets/image.png"}, "pptx-to-html")
    target = session if owner is None else session.parent / "users" / owner / "session"
    assert mode(target / "assets") == 0o750
    assert mode(target / "assets" / "image.png") == 0o640
    assert (target / "index.html").read_text() == '<img src="assets/image.png">'
    expected = "/app_data/pptx-to-html/" + ("" if owner is None else f"users/{owner}/") + "session/assets/image.png"
    assert result["url"] == expected


def test_foreign_legacy_ancestors_are_best_effort_but_new_artifacts_fail_closed(tmp_path, monkeypatch):
    output = tmp_path / "deck.pdf"
    output.write_text("pdf")
    real_chmod = os.chmod

    def restricted_chmod(filename, permissions):
        if str(filename) == str(tmp_path):
            raise PermissionError(errno.EPERM, "foreign-owned volume")
        return real_chmod(filename, permissions)

    monkeypatch.setattr(os, "chmod", restricted_chmod)
    export_permissions._permissions(str(tmp_path), True, None, legacy=True)
    with pytest.raises(PermissionError):
        export_permissions._permissions(str(tmp_path), True, None)
    export_permissions._permissions(str(output), False, None)
    assert mode(output) == 0o640


def test_foreign_shared_directory_denial_does_not_skip_file_protection(tmp_path, monkeypatch):
    output = tmp_path / "deck.pdf"
    output.write_text("pdf")
    fake_stat = SimpleNamespace(st_mode=0o2770, st_gid=os.getgid(), st_uid=os.geteuid() + 1)
    with monkeypatch.context() as context:
        context.setattr(os, "geteuid", lambda: 10000)
        context.setattr(os, "stat", lambda *args, **kwargs: fake_stat)

        def denied(*args, **kwargs):
            raise PermissionError(errno.EPERM, "foreign-owned volume")

        context.setattr(os, "chmod", denied)
        export_permissions._permissions(str(tmp_path), True, os.getgid())
        with pytest.raises(PermissionError):
            export_permissions._permissions(str(output), False, os.getgid())
