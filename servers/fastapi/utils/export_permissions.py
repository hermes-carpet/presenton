import errno
import os
from collections.abc import Iterable

DIRECTORY_MODE = 0o750
FILE_MODE = 0o640


def export_reader_gid() -> int | None:
    if not hasattr(os, "getgid"):
        return None
    if os.geteuid() == 0:
        import grp

        for name in ("www-data", "nginx"):
            try:
                return grp.getgrnam(name).gr_gid
            except KeyError:
                pass
    return os.getgid()


def _permissions(filename: str, directory: bool, gid: int | None, *, legacy: bool = False) -> None:
    if os.path.islink(filename):
        return
    try:
        stat = os.stat(filename, follow_symlinks=False)
        # Existing shared-volume directories can be writable but foreign-owned.
        # Generated files still require strict permissions even on those volumes.
        legacy = legacy or (
            directory and hasattr(os, "geteuid")
            and os.geteuid() != 0 and stat.st_uid != os.geteuid()
        )
        if gid is not None and stat.st_gid != gid:
            os.chown(filename, -1, gid)
        mode = DIRECTORY_MODE | (stat.st_mode & 0o3000) if directory else FILE_MODE
        if legacy and directory:
            # Keep existing shared-volume group write and private boundaries.
            mode = stat.st_mode & 0o7770
        os.chmod(filename, mode)
    except OSError as error:
        if legacy and error.errno in {errno.EACCES, errno.EPERM}:
            return
        raise


def finalize_export_permissions(output_path: str, roots: Iterable[str | None], *, recursive: bool = False) -> None:
    """Grant the app/proxy group access without granting other OS users access."""
    if os.path.islink(output_path):
        return
    resolved = os.path.realpath(output_path)
    directory = resolved if os.path.isdir(resolved) else os.path.dirname(resolved)
    boundary = directory
    candidates = []
    configured = [root for root in roots if root]
    for root in configured:
        canonical = os.path.realpath(root)
        try:
            if os.path.commonpath([directory, canonical]) == canonical:
                candidates.append(canonical)
        except ValueError:
            continue
    if configured and not candidates:
        raise OSError(errno.EPERM, "Export artifact is outside managed storage", output_path)
    if candidates:
        boundary = max(candidates, key=len)
    gid = export_reader_gid()
    current = directory
    while True:
        _permissions(current, True, gid, legacy=True)
        if current == boundary:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    if recursive:
        for parent, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(parent, name))]
            _permissions(parent, True, gid)
            for name in files:
                _permissions(os.path.join(parent, name), False, gid)
    else:
        _permissions(directory, True, gid)
        if os.path.isfile(resolved):
            _permissions(resolved, False, gid)
