"""Per-directory consent for loading a project's own ``.kiro/skills``.

A ``SKILL.md`` is prose, not code, but it enters the agent's context and can
instruct the agent to run anything. Loading one out of whatever repository the
operator happens to open is therefore an execution-adjacent decision: a cloned
repository could ship instructions the operator never read. This module is the
consent record that gates it.

Trust is keyed on the **canonical** project directory (``os.path.realpath``),
because the directory *is* the resource. Keying on any softer identity -- a
display name, a slug, an index entry -- leaves the unkeyed component forgeable:
a second name aliasing one directory would grant itself separate trust, and a
rename would orphan the record.

Storage is ``<data home>/trust/project-skills.json``. That directory is already
a whole-directory entry on the keystone deny list, so the agent's own file tools
can neither read nor write this store; like every other keystone reader, this
module opens the path directly rather than through the agent file gate.

The gate fails **closed** everywhere: an unreadable store, a malformed store, or
an unreadable config all yield "nothing is trusted" rather than a permissive
default. Refusing to load a skill costs the operator a click; loading one they
did not consent to cannot be undone.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from kiro_crew import platform_compat
from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.paths import config_dir
from kiro_crew.sel import sel

logger = logging.getLogger(__name__)

#: Subdirectory of the data home holding trust-root material. Shared with the
#: SEL signing key so a single keystone entry covers both.
_TRUST_SUBDIR = "trust"

_STORE_FILENAME = "project-skills.json"

#: Owner-only: a world-readable grant list tells a local attacker which
#: directories are worth planting a SKILL.md in.
_STORE_MODE = 0o600

#: Current on-disk schema version. A store written by a newer build is treated
#: as unreadable (fail closed) rather than guessed at.
_SCHEMA_VERSION = 1

#: Bound the per-decision cost of a pathological store. Mirrors the app-trust
#: reader: truncate to the first N rather than denying outright, since an
#: append-ordered list keeps the operator's real grants at the front.
_MAX_GRANT_ENTRIES = 512

#: Cached ``(stat_signature, frozenset_of_keys)``. The enforcement read happens
#: on the event loop during dashboard listing, so re-parsing the store on every
#: skill would be a syscall per row; a stat signature is one syscall total.
_cache: tuple[tuple[int, int, int], frozenset[str]] | None = None


def store_path() -> Path:
    """Absolute path of the grant store."""
    return config_dir() / _TRUST_SUBDIR / _STORE_FILENAME


def canonical_key(project_dir: str | Path | None) -> str | None:
    """Return the canonical trust key for *project_dir*, or ``None``.

    ``None`` means "this value cannot identify a project directory", which every
    caller must treat as untrusted. A relative path, a file, a dangling symlink
    and a nonexistent path all land here: a value that cannot name a real
    directory has no business matching a grant.

    Resolution is ``os.path.realpath``, so a symlink cannot alias its way to a
    grant belonging to a different real directory.

    This performs filesystem syscalls. Callers on the event loop should resolve
    once per request and pass the result down rather than calling it per skill.
    """
    if project_dir is None:
        return None
    raw = str(project_dir).strip()
    if not raw:
        return None
    try:
        expanded = os.path.expanduser(raw)
        if not os.path.isabs(expanded):
            return None
        real = os.path.realpath(expanded)
        if not os.path.isdir(real):
            return None
    except (OSError, ValueError):
        return None
    return real


def _project_skills_enabled() -> bool:
    """The operator's hard off switch.

    Independent of any grant: with this false, project skills are impossible
    even for a directory that carries one. Fails closed -- an unreadable config
    disables the feature rather than enabling it.
    """
    try:
        from kiro_crew.config.loader import KiroCrewConfig

        return bool(KiroCrewConfig.load().skills.project_skills_enabled)
    except Exception as exc:  # noqa: BLE001 - unreadable policy must fail closed
        logger.error(
            "skills.project_skills_enabled unreadable (%s); " "refusing every project-skills grant",
            exc,
        )
        return False


def _store_signature(path: Path) -> tuple[int, int, int] | None:
    """Cheap change-detector for the store: ``(mtime_ns, size, inode)``."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _parse_store(text: str) -> frozenset[str]:
    """Parse store *text* into a set of canonical keys, failing closed.

    Every malformed shape yields the empty set: a store we cannot understand
    grants nothing.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("%s: not valid JSON (%s); ignoring every grant", _STORE_FILENAME, exc)
        return frozenset()
    if not isinstance(data, dict):
        logger.error("%s: not a JSON object; ignoring every grant", _STORE_FILENAME)
        return frozenset()
    version = data.get("version")
    if version != _SCHEMA_VERSION:
        logger.error(
            "%s: schema version %r is not %d; ignoring every grant",
            _STORE_FILENAME,
            version,
            _SCHEMA_VERSION,
        )
        return frozenset()
    raw = data.get("granted")
    if not isinstance(raw, list):
        logger.error("%s: 'granted' is not an array; ignoring every grant", _STORE_FILENAME)
        return frozenset()
    if len(raw) > _MAX_GRANT_ENTRIES:
        logger.error(
            "%s: %d entries exceeds the %d cap; considering only the first %d",
            _STORE_FILENAME,
            len(raw),
            _MAX_GRANT_ENTRIES,
            _MAX_GRANT_ENTRIES,
        )
        raw = raw[:_MAX_GRANT_ENTRIES]
    keys: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        # Stored keys are already canonical, but an absolute-path check still
        # applies: a relative entry in a hand-edited store must not match a
        # caller's canonical key by accident.
        if isinstance(path, str) and path and os.path.isabs(path):
            keys.add(path)
    return frozenset(keys)


def trusted_keys() -> frozenset[str]:
    """Every canonical directory the operator has granted, or an empty set.

    Result is cached against the store's stat signature, so repeated
    enforcement reads within one listing cost a single ``stat``.
    """
    global _cache
    if not _project_skills_enabled():
        return frozenset()
    path = store_path()
    signature = _store_signature(path)
    if signature is None:
        _cache = None
        return frozenset()
    cached = _cache
    if cached is not None and cached[0] == signature:
        return cached[1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("%s: unreadable (%s); ignoring every grant", _STORE_FILENAME, exc)
        _cache = None
        return frozenset()
    keys = _parse_store(text)
    _cache = (signature, keys)
    return keys


def is_project_trusted(project_dir: str | Path | None) -> bool:
    """Whether *project_dir*'s own ``.kiro/skills`` may be loaded."""
    key = canonical_key(project_dir)
    if key is None:
        return False
    return key in trusted_keys()


def is_key_trusted(key: str | None) -> bool:
    """Membership test for an already-canonical key.

    Split out so a hot path can resolve the key once off the event loop and
    then test membership without further syscalls.
    """
    if not key:
        return False
    return key in trusted_keys()


def _trust_dir() -> Path:
    """The trust directory, verified to be a real directory.

    A pre-planted symlink here would redirect the grant write somewhere the
    agent can author, letting it forge a grant for a directory the operator
    never approved. Only the link is removed, never its target.
    """
    directory = config_dir() / _TRUST_SUBDIR
    if directory.is_symlink():
        logger.error("%s is a symlink; removing the link before writing trust state", directory)
        directory.unlink()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


@contextmanager
def _locked_store(*, exclusive: bool = True) -> Iterator[None]:
    """Hold a lock on the store for the duration of the block.

    One lock spans an entire read-modify-write so two concurrent grants cannot
    lose an update, and a revoke racing a grant cannot leave a revoked
    directory trusted.
    """
    lock_path = _trust_dir() / (_STORE_FILENAME + ".lock")
    lock_path.touch(exist_ok=True)
    # "r+" not "r": Windows msvcrt.locking needs write access on the fd, and a
    # read-only handle degrades the lock to a silent no-op.
    with open(lock_path, "r+") as handle:
        with platform_compat.file_lock(handle.fileno(), exclusive=exclusive):
            yield


def _read_entries_unlocked() -> list[dict[str, Any]]:
    """Read raw grant entries. Caller must hold the lock."""
    path = store_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("version") != _SCHEMA_VERSION:
        return []
    raw = data.get("granted")
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _write_entries_unlocked(entries: list[dict[str, Any]]) -> None:
    """Persist grant *entries*. Caller must hold the lock."""
    global _cache
    _trust_dir()
    payload = {"version": _SCHEMA_VERSION, "granted": entries}
    atomic_write(
        store_path(),
        json.dumps(payload, indent=2) + "\n",
        mode=_STORE_MODE,
    )
    # The next read re-stats and re-parses rather than trusting a value this
    # process cached before the write.
    _cache = None


def grant_project_trust(project_dir: str | Path, *, session_key: str = "") -> str:
    """Record consent for *project_dir* and return its canonical key.

    Raises ``ValueError`` when *project_dir* cannot name a real directory, so a
    caller cannot bank a grant against a path that will never match.

    The grant is audited with ``critical=True``: this is a one-time human
    security decision, and an audit that cannot be written must refuse it
    rather than record consent nowhere.
    """
    key = canonical_key(project_dir)
    if key is None:
        raise ValueError(f"not an existing absolute directory: {project_dir!r}")
    sel().log_governance_decision(
        session_key=session_key,
        tool_name="skill_trust",
        scope="project_skills",
        item=key,
        outcome="allowed",
        rule="operator_granted_project_skills",
        reason="operator granted project-skills trust for this directory",
        critical=True,
    )
    with _locked_store():
        entries = _read_entries_unlocked()
        for entry in entries:
            if entry.get("path") == key:
                return key
        entries.append({"path": key, "granted_at": int(time.time())})
        if len(entries) > _MAX_GRANT_ENTRIES:
            # Keep the newest grant rather than silently dropping the click the
            # operator just made; the oldest entry is the one they care least
            # about.
            entries = entries[-_MAX_GRANT_ENTRIES:]
        _write_entries_unlocked(entries)
    return key


def revoke_project_trust(project_dir: str | Path, *, session_key: str = "") -> bool:
    """Withdraw consent for *project_dir*. Returns whether a grant was removed.

    Revocation deliberately does **not** require the directory to still exist:
    an operator must be able to withdraw trust from a path they have already
    deleted or moved, so this matches on the stored string as well as on the
    canonical key.
    """
    key = canonical_key(project_dir)
    raw = str(project_dir).strip()
    candidates = {c for c in (key, raw, os.path.expanduser(raw)) if c}
    removed = False
    with _locked_store():
        entries = _read_entries_unlocked()
        kept = [e for e in entries if e.get("path") not in candidates]
        if len(kept) != len(entries):
            removed = True
            _write_entries_unlocked(kept)
    if removed:
        sel().log_governance_decision(
            session_key=session_key,
            tool_name="skill_trust",
            scope="project_skills",
            item=key or raw,
            outcome="denied",
            rule="operator_revoked_project_skills",
            reason="operator revoked project-skills trust for this directory",
            critical=True,
        )
    return removed


def list_trusted_projects() -> list[dict[str, Any]]:
    """Every stored grant, newest first, for display.

    Reports the raw stored rows rather than the enforced set so a UI can show
    a grant whose directory has since disappeared -- otherwise a stale entry
    would be invisible and un-revokable.
    """
    with _locked_store(exclusive=False):
        entries = _read_entries_unlocked()
    rows: list[dict[str, Any]] = []
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        rows.append(
            {
                "path": path,
                "granted_at": entry.get("granted_at"),
                "exists": os.path.isdir(path),
            }
        )
    rows.sort(key=lambda r: r.get("granted_at") or 0, reverse=True)
    return rows


def reset_cache_for_tests() -> None:
    """Drop the memoized enforcement read.

    The cache keys on a stat signature, and a test that writes a store twice
    within the same filesystem timestamp granularity can otherwise observe the
    first value.
    """
    global _cache
    _cache = None
