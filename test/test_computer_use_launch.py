"""``computer_launch_app``: resolution trust, the dispatch path, and the fake seam.

The tests here are about ONE question — what may this verb start? — because it is
the only verb in the package that creates a process, and every other protection in
computer use assumes a window already exists.

Split by what each group can be honest about:

* :class:`TestLaunchResolutionTrust` runs on any OS against a fabricated catalog, so
  the protected-root and basename rules are asserted as LOGIC rather than against
  whatever happens to be installed on the runner;
* :class:`TestLaunchDispatch` drives the real chokepoint through the shipped fake, so
  the denylist ordering and the launch-then-snapshot shape are covered on Linux CI;
* :class:`TestWindowsHostCatalog` is Windows-only and asserts the invariant against
  the host's REAL catalog — it is the one that would catch a host where the rule does
  not hold.
"""

from __future__ import annotations

import inspect
import json
import os
import stat

import pytest

from kiro_crew.computer_use import backend as cu_backend
from kiro_crew.computer_use import index as cu_index
from kiro_crew.computer_use import policy
from kiro_crew.computer_use import service as cu_service
from kiro_crew.computer_use import tools
from kiro_crew.computer_use.types import (
    ERR_LAUNCH_ALREADY_RUNNING,
    ERROR_PREFIX,
    TOOL_GET_STATE,
    TOOL_LAUNCH_APP,
    AmbiguousLaunchTarget,
    AppRef,
    ComputerUseError,
    LaunchIdentity,
    NoSuchLaunchTarget,
)
from kiro_crew.platform_compat import IS_WINDOWS
from kiro_crew.testing.fake_computer_use import (
    FAKE_DRAW_APP,
    FAKE_FILES_APP,
    FakeComputerUseBackend,
)

# The registry these tests swap is process-wide, so they must not run beside another
# test that also swaps it.
pytestmark = pytest.mark.xdist_group("computer_use_launch")

_SESSION = "cli_chat"


@pytest.fixture
def fake_computer_backend(tmp_path, monkeypatch):
    """The shipped fake, registered process-wide, with the keystone enable on.

    ``KIROCREW_HOME`` is redirected first: the dispatcher refuses everything before
    reaching a driver unless the keystone says enabled, and a developer's real
    ``~/.kiro/crew`` must never decide a test's outcome.
    """
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    (tmp_path / "computer_use.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    fake = FakeComputerUseBackend()
    cu_backend.register_computer_use_backend(lambda: fake)
    cu_backend.reset_shared_backend()
    cu_service.reset_shared_service()
    cu_index.reset_shared_index()
    try:
        yield fake
    finally:
        cu_backend.register_computer_use_backend(None)
        cu_backend.reset_shared_backend()
        cu_service.reset_shared_service()
        cu_index.reset_shared_index()


def _launch(app: str) -> str:
    return tools.dispatch_tool(TOOL_LAUNCH_APP, {"app": app}, session_key=_SESSION)


@pytest.mark.skipif(not IS_WINDOWS, reason="launch_windows is the Windows resolver")
class TestLaunchResolutionTrust:
    """The protected-root and basename rules, against a FABRICATED catalog.

    Fabricated on purpose. Asserting these against the host's real registry would
    make the test's strength depend on what is installed, and the rules are what has
    to hold for a catalog entry the agent WROTE — which no real host provides.
    """

    @staticmethod
    def _catalog(monkeypatch, entries):
        from kiro_crew.computer_use import launch_windows

        monkeypatch.setattr(launch_windows, "installed_apps", lambda: tuple(entries))
        return launch_windows

    @staticmethod
    def _entry(key: str, executable: str):
        from kiro_crew.computer_use.launch_windows import InstalledApp

        stem = key[:-4] if key.lower().endswith(".exe") else key
        return InstalledApp(key=key, name=stem, executable=executable, source="test")

    @staticmethod
    def _system32_is_unwritable(monkeypatch):
        """Pin the writability half of ``_under_protected`` to "not writable".

        Required for any test that expects a real ``System32`` binary to RESOLVE, because
        the answer depends on the privilege of whoever runs the suite: an elevated process
        genuinely can write ``System32``, so the probe correctly reports it and the launch
        is correctly refused. CI's ``windows-latest`` runner is elevated and a developer's
        shell usually is not, so the unpinned assertion measures the runner rather than
        the rule.

        Pinned rather than skipped: the protected-root and basename rules are what these
        tests exist for, and they hold at every privilege level. What varies is only this
        one input.
        """
        from kiro_crew.computer_use import launch_windows

        monkeypatch.setattr(launch_windows, "_directory_is_writable", lambda _d: False)

    def test_a_target_outside_the_protected_roots_is_refused(self, monkeypatch, tmp_path):
        # THE central rule. An agent can write HKCU's App Paths (measured), so a
        # catalog entry naming a binary it dropped in its own directory is the exact
        # attack this verb has to refuse — and tmp_path is that directory.
        planted = tmp_path / "mspaint.exe"
        planted.write_bytes(b"MZ")
        launch = self._catalog(monkeypatch, [self._entry("mspaint.exe", str(planted))])
        with pytest.raises(ComputerUseError) as caught:
            launch.resolve_target("mspaint")
        assert "protected install directories" in str(caught.value)

    def test_a_target_whose_basename_disagrees_with_its_key_is_refused(self, monkeypatch):
        # The second half of the rule, and the one that stops a REDIRECT: an agent
        # that rewrites a writable catalog value can only aim it at a file already
        # present under a protected root, so the remaining move is aiming
        # "mspaint.exe" at some other installed binary. Verified against a real
        # System32 file so only the basename differs from the passing case.
        real = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "calc.exe")
        if not os.path.isfile(real):
            pytest.skip("no System32 calc.exe on this host")
        self._system32_is_unwritable(monkeypatch)
        launch = self._catalog(monkeypatch, [self._entry("mspaint.exe", real)])
        with pytest.raises(ComputerUseError) as caught:
            launch.resolve_target("mspaint")
        assert "calc.exe" in str(caught.value)

    def test_a_protected_target_named_after_its_key_resolves(self, monkeypatch):
        # The positive control. Without it the two refusals above would also pass on
        # an implementation that refused everything.
        real = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
        if not os.path.isfile(real):
            pytest.skip("no System32 notepad.exe on this host")
        self._system32_is_unwritable(monkeypatch)
        launch = self._catalog(monkeypatch, [self._entry("notepad.exe", real)])
        assert launch.resolve_target("notepad") == (real, "notepad")

    def test_a_junction_cannot_borrow_a_protected_prefix(self, monkeypatch, tmp_path):
        # ``_under_protected`` realpaths BEFORE comparing, which is what stops a link
        # under a writable directory presenting a protected-looking path. Asserted
        # against the resolver directly because a junction needs no elevation to
        # create, so this is a move the agent really has
        # available — unlike the reverse (a junction UNDER Program Files), which the
        # OS refuses outright.
        launch = self._catalog(monkeypatch, [])
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "Program Files"
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            pytest.skip("this host does not permit creating a directory link")
        planted = target / "notepad.exe"
        planted.write_bytes(b"MZ")
        assert launch._under_protected(str(link / "notepad.exe")) is False

    def test_the_protected_roots_do_NOT_come_from_the_environment(self, monkeypatch, tmp_path):
        """THE attack that defeats every other check in one line.

        ``%ProgramFiles%`` and ``%SystemRoot%`` are ordinary environment variables, and
        ``HKCU\\Environment`` is writable without elevation — so anyone who can set one
        can nominate a directory they WRITE as a "protected" root, after which the
        protected-root verification accepts a binary planted there. Both halves were
        live bypasses during development:

        * reading the install roots from ``os.environ`` (fixed by
          :func:`~kiro_crew.computer_use.launch_windows._install_roots_from_registry`);
        * ``platform_compat._windows_system_dirs`` appending
          ``%SystemRoot%\\System32`` *unconditionally* rather than as a fallback, so it
          was added even while ``GetSystemDirectoryW`` answered normally.

        **Every variable is planted at BOTH depths**, and that is the point of the
        parametrization rather than thoroughness for its own sake: the first version of
        this test planted only ``<tmp>/mspaint.exe`` and therefore could not see the
        ``SystemRoot`` bypass at all, because that one injects ``<tmp>/System32`` — one
        level deeper than the file it was checking. A guard test that inspects the wrong
        path is worse than no guard test, since it reads as coverage.
        """
        from kiro_crew import platform_compat
        from kiro_crew.computer_use import launch_windows

        # Both the root itself and the System32 child an env-derived root expands to.
        for relative in ("", "System32", os.path.join("System32", "WindowsPowerShell", "v1.0")):
            (tmp_path / relative).mkdir(parents=True, exist_ok=True)
            (tmp_path / relative / "evil.exe").write_bytes(b"MZ")

        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemRoot"):
            monkeypatch.setenv(var, str(tmp_path))

        roots = launch_windows._protected_roots()
        assert roots, "no protected root resolved at all"
        planted_root = str(tmp_path).casefold()
        for root in roots:
            assert not root.casefold().startswith(planted_root), root
        for relative in ("", "System32", os.path.join("System32", "WindowsPowerShell", "v1.0")):
            candidate = str(tmp_path / relative / "evil.exe")
            assert launch_windows._under_protected(candidate) is False, candidate
        # The shared helper is where the SystemRoot half lived, so it is asserted
        # directly too: a caller-side filter in launch_windows would leave
        # ``trusted_system_bin`` — which has the same distrust goal — still exposed.
        for directory in platform_compat._windows_system_dirs():
            assert not directory.casefold().startswith(planted_root), directory

    def test_a_WRITABLE_DESCENDANT_of_a_protected_root_is_refused(self, monkeypatch):
        """An unwritable root can contain writable children, so a prefix test is not it.

        Measured on this host: ``C:\\Windows\\Temp``, ``C:\\Windows\\Tasks`` and
        ``C:\\Windows\\System32\\spool\\drivers\\color`` are all writable by an
        unprivileged user while their parents are not. A prefix test against
        ``C:\\Windows`` therefore accepted a planted ``C:\\Windows\\Temp\\Evil.exe``, and
        an ``App Paths`` entry in the writable ``HKCU`` hive can name exactly that.

        Two changes close it: ``C:\\Windows`` is no longer a root at all (``System32``
        comes from ``platform_compat`` instead), and the file's own directory is probed
        for writability. This asserts the second, because the first alone would leave the
        two writable directories still under ``System32``.
        """
        from kiro_crew.computer_use import launch_windows

        # ``C:\Windows`` must not be a root at all — that is half the fix, and it is
        # assertable without touching anything: the paths under it are rejected at the
        # prefix stage, before any probe.
        windows_root = os.environ.get("SystemRoot", r"C:\Windows")
        assert launch_windows._under_protected(os.path.join(windows_root, "Temp", "Evil.exe")) is (
            False
        )
        assert launch_windows._under_protected(os.path.join(windows_root, "Tasks", "Evil.exe")) is (
            False
        )

        # The two holes UNDER ``System32`` reach the writability probe, and probing them
        # for real would create files in the operator's system directories — which a test
        # must not do. So the probe is scripted to report what this host actually
        # measured (both writable) and the REFUSAL is what is asserted.
        measured_writable = {
            os.path.join(windows_root, "System32", "spool", "drivers", "color"),
            os.path.join(windows_root, "System32", "Microsoft", "Crypto", "RSA", "MachineKeys"),
        }
        # Compared CASE-FOLDED after ``realpath``: ``_under_protected`` resolves the path
        # first, and ``GetSystemDirectoryW`` reports the on-disk casing (``C:\WINDOWS``)
        # while ``%SystemRoot%`` here yields ``C:\Windows``. A case-sensitive membership
        # test silently matches nothing, which would leave this test passing while
        # asserting the wrong branch.
        folded_writable = {os.path.realpath(d).casefold() for d in measured_writable}
        monkeypatch.setattr(
            launch_windows,
            "_directory_is_writable",
            lambda directory: os.path.realpath(directory).casefold() in folded_writable,
        )
        for writable in measured_writable:
            assert launch_windows._under_protected(os.path.join(writable, "Evil.exe")) is False

    def test_a_writable_directory_under_a_protected_root_is_rejected(self, monkeypatch, tmp_path):
        """The general rule, with the writability answer INJECTED rather than measured.

        The honest version of this test walked every protected root and probed each
        directory for real — which meant creating files across the operator's own
        ``System32`` and ``Program Files`` trees. A test must not touch the host
        (AGENTS.md), and "the probe cleans up after itself" is not the same guarantee:
        an interrupted run leaves litter in a system directory.

        So the shape is asserted instead of the host's ACLs: ``tmp_path`` stands in for
        the root, and ``_directory_is_writable`` is scripted. That covers the branch that
        matters — a directory inside a protected root, writable, must be REFUSED — for
        any directory, not only the ones this host happens to have. The specific
        real-world holes are still named by the sibling test above, which calls only
        ``_under_protected`` and creates nothing.
        """
        from kiro_crew.computer_use import launch_windows

        nested = tmp_path / "Program Files" / "App"
        nested.mkdir(parents=True)
        planted = nested / "app.exe"
        planted.write_bytes(b"MZ")
        monkeypatch.setattr(
            launch_windows, "_protected_roots", lambda: (str(tmp_path / "Program Files"),)
        )

        # The FILE answer is injected as well, so this asserts the directory condition
        # alone. A file under ``tmp_path`` is genuinely rewritable by this user, and the
        # separate file probe correctly refuses on it — which would otherwise mask whichever
        # branch this test is about. Its own coverage is
        # ``test_a_WRITABLE_executable_under_a_protected_root_is_refused``.
        monkeypatch.setattr(launch_windows, "_file_is_writable", lambda _p: False)

        monkeypatch.setattr(launch_windows, "_directory_is_writable", lambda _d: True)
        assert launch_windows._under_protected(str(planted)) is False
        # And the same path is accepted once its directory is not writable, so the
        # refusal above is attributable to writability and nothing else.
        monkeypatch.setattr(launch_windows, "_directory_is_writable", lambda _d: False)
        assert launch_windows._under_protected(str(planted)) is True

    def test_a_legitimate_system_binary_still_resolves(self, monkeypatch):
        """The positive control: the probe must not have turned every target into a
        refusal.

        ``_directory_is_writable`` is scripted to ``False`` for the real ``System32``
        rather than measured, because measuring it means creating a file in the
        operator's system directory. What this pins is that a real path under a real
        protected root, in a directory reported unwritable, is ACCEPTED — the branch the
        writability change could have broken.
        """
        from kiro_crew.computer_use import launch_windows

        real = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
        if not os.path.isfile(real):
            pytest.skip("no System32 notepad.exe on this host")
        monkeypatch.setattr(launch_windows, "_directory_is_writable", lambda _d: False)
        assert launch_windows._under_protected(real) is True

    def test_a_WRITABLE_executable_under_a_protected_root_is_refused(self, monkeypatch, tmp_path):
        """Create-permission and replace-permission are different questions on Windows too.

        The directory probe asks only *can I create a new file here*; rewriting an existing
        file's bytes needs write on the file itself. So a parent that refuses creates can
        still hold an executable this user rewrites with ``open(path, "r+b")`` — measured —
        and the protected-root test alone accepted it. The macOS sibling makes the same
        distinction, and a target must fail BOTH questions to be trusted.
        """
        from kiro_crew.computer_use import launch_windows

        planted = tmp_path / "app.exe"
        planted.write_bytes(b"MZ")
        monkeypatch.setattr(launch_windows, "_protected_roots", lambda: (str(tmp_path),))
        monkeypatch.setattr(launch_windows, "_directory_is_writable", lambda _d: False)
        assert launch_windows._under_protected(str(planted)) is False

    def test_a_real_system_binary_is_still_trusted(self, monkeypatch):
        """The control, and the reason ``os.access`` is not the probe.

        On Windows ``os.access(.., W_OK)`` reports the read-only ATTRIBUTE and never consults
        the ACL, so it answers True for every ``System32`` binary — using it would refuse the
        whole catalog. Measured from an unelevated shell: ``os.access`` says writable for
        ``System32\\notepad.exe`` while opening it ``O_RDWR`` is denied. This asserts the real
        binary stays trusted, which an ``os.access``-based check could not.
        """
        from kiro_crew.computer_use import launch_windows

        real = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
        if not os.path.isfile(real):
            pytest.skip("no System32 notepad.exe on this host")
        if launch_windows._file_is_writable(real):
            # An elevated process really can rewrite it, and refusing is then correct.
            pytest.skip("this process can write System32, so the refusal is the right answer")
        monkeypatch.setattr(launch_windows, "_directory_is_writable", lambda _d: False)
        assert launch_windows._under_protected(real) is True

    def test_the_file_write_probe_modifies_nothing(self, tmp_path):
        # It runs on a binary about to be ALLOWED, so it must not damage a real installed
        # application: ``O_RDWR`` with no ``O_TRUNC`` and no write.
        from kiro_crew.computer_use import launch_windows

        target = tmp_path / "app.exe"
        target.write_bytes(b"MZ-ORIGINAL-BYTES")
        assert launch_windows._file_is_writable(str(target)) is True
        assert target.read_bytes() == b"MZ-ORIGINAL-BYTES"

    def test_an_unexaminable_file_fails_CLOSED(self, monkeypatch, tmp_path):
        # "Assume writable" is the safe answer: the caller refuses. A file we cannot open at
        # all is not evidence that it is trustworthy.
        from kiro_crew.computer_use import launch_windows

        def boom(*_args, **_kwargs):
            raise OSError("device not ready")

        monkeypatch.setattr(launch_windows.os, "open", boom)
        assert launch_windows._file_is_writable(str(tmp_path / "app.exe")) is True

    def test_the_write_probe_is_removed(self, tmp_path):
        # The probe is only ever created where the launch then refuses, but a leftover
        # file in a system directory would still be litter with our name on it.
        from kiro_crew.computer_use import launch_windows

        assert launch_windows._directory_is_writable(str(tmp_path)) is True
        assert list(tmp_path.iterdir()) == []

    def test_an_unwritable_directory_answers_False(self, monkeypatch, tmp_path):
        # Driven through a denial rather than by finding a real unwritable directory, so
        # the fail-closed branches are reachable on any host.
        from kiro_crew.computer_use import launch_windows

        def denied(*_args, **_kwargs):
            raise PermissionError("Access is denied")

        monkeypatch.setattr("builtins.open", denied)
        assert launch_windows._directory_is_writable(str(tmp_path)) is False

    @pytest.mark.parametrize("error", [OSError("io"), FileExistsError("collision")])
    def test_an_unexpected_probe_error_fails_CLOSED(self, monkeypatch, tmp_path, error):
        # "Assume writable" is the safe answer: the caller refuses the launch. An
        # unreadable directory is not evidence that a binary inside it is trustworthy.
        from kiro_crew.computer_use import launch_windows

        def boom(*_args, **_kwargs):
            raise error

        monkeypatch.setattr("builtins.open", boom)
        assert launch_windows._directory_is_writable(str(tmp_path)) is True

    def test_a_command_interpreter_is_refused_even_under_a_protected_root(self, monkeypatch):
        # A shell passes the protected-root rule (cmd.exe IS in System32) and the
        # basename rule, so it is refused by name. The no-arguments bound that makes
        # every other target safe buys nothing against a process that takes its work
        # from a subsequent keystroke.
        real = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        if not os.path.isfile(real):
            pytest.skip("no System32 cmd.exe on this host")
        launch = self._catalog(monkeypatch, [self._entry("cmd.exe", real)])
        with pytest.raises(ComputerUseError) as caught:
            launch.resolve_target("cmd")
        assert "command interpreter" in str(caught.value)

    def test_a_shell_QUERY_is_refused_even_when_it_resolves_elsewhere(self, monkeypatch):
        """THE defect a live run found, and the reason there are two shell checks.

        Asking for ``cmd`` on the measured host matched ``IEDIAGCMD.EXE`` — an
        unrelated Internet Explorer diagnostic — under the old substring tier, and the
        resolved-basename check never saw a shell, so it **launched**. Two independent
        failures met: a 3-character fragment matching inside a 9-character name, and a
        guard that only inspected what the name resolved TO.

        Both are fixed, and this pins the first half: the QUERY is refused before
        resolution, so it does not matter what the catalog would have matched.
        """
        launch = self._catalog(
            monkeypatch, [self._entry("IEDIAGCMD.EXE", r"C:\Windows\System32\IEDIAGCMD.EXE")]
        )
        with pytest.raises(ComputerUseError) as caught:
            launch.resolve_target("cmd")
        assert "command interpreter" in str(caught.value)

    def test_the_fuzzy_tier_is_a_PREFIX_not_a_substring(self, monkeypatch):
        """The second half of the same defect.

        A short fragment matching inside a long name is a coincidence rather than an
        intent, and the ambiguity guard cannot catch it because a coincidence usually
        hits exactly ONE entry — which is precisely how ``cmd`` resolved to a single
        unrelated application and was launched. Asserted with a non-shell name so this
        is about the matching rule rather than about the shell list.
        """
        launch = self._catalog(
            monkeypatch, [self._entry("IEDIAGXYZ.EXE", r"C:\Windows\System32\IEDIAGXYZ.EXE")]
        )
        with pytest.raises(NoSuchLaunchTarget):
            launch.resolve_target("xyz")

    def test_a_near_miss_SUGGESTS_the_real_name(self, monkeypatch):
        # Prefix-only matching would otherwise be a dead end for a model that typed a
        # fragment of a real name, and the retry it would reach for is a path — the one
        # shape that can never be served. Suggestions are never launch TARGETS.
        real = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
        if not os.path.isfile(real):
            pytest.skip("no System32 notepad.exe on this host")
        launch = self._catalog(monkeypatch, [self._entry("notepad.exe", real)])
        with pytest.raises(NoSuchLaunchTarget) as caught:
            launch.resolve_target("tepad")
        assert "notepad" in caught.value.near

    def test_an_ambiguous_substring_refuses_rather_than_picking(self, monkeypatch):
        # Launching the wrong application is not undoable, so a substring hitting
        # several apps must not resolve to whichever came first. The ambiguity check
        # runs BEFORE any target verification, so the entries need not name real
        # files — and deliberately do not, since two same-prefixed real applications
        # are not something a runner can be relied on to have.
        #
        # The query is a strict PREFIX of both stems, never equal to either: an exact
        # match is resolved first by design (see the sibling test), so a query equal to
        # one of the names would take that branch and never reach the ambiguity rule.
        launch = self._catalog(
            monkeypatch,
            [
                self._entry("noteappone.exe", r"C:\Windows\System32\noteappone.exe"),
                self._entry("noteapptwo.exe", r"C:\Windows\System32\noteapptwo.exe"),
            ],
        )
        with pytest.raises(AmbiguousLaunchTarget) as caught:
            launch.resolve_target("noteapp")
        assert caught.value.count == 2

    def test_an_exact_name_beats_an_ambiguous_prefix(self, monkeypatch):
        # The positive control for the rule above: without it, "refuse when several
        # match" would also refuse the case where one of them is what was asked for.
        real = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
        if not os.path.isfile(real):
            pytest.skip("no System32 notepad.exe on this host")
        self._system32_is_unwritable(monkeypatch)
        launch = self._catalog(
            monkeypatch,
            [
                self._entry("notepad.exe", real),
                self._entry("notepadplus.exe", r"C:\Windows\System32\notepadplus.exe"),
            ],
        )
        assert launch.resolve_target("notepad") == (real, "notepad")

    def test_the_launch_argv_is_exactly_the_executable(self, monkeypatch, tmp_path):
        # No document, no flag, no URL. A launch that accepted an argument would be a
        # way to hand attacker-chosen input to an arbitrary installed application,
        # which is a different capability from "open the drawing app".
        from kiro_crew.computer_use import launch_windows

        seen: list[list[str]] = []

        class _Popen:
            def __init__(self, argv, **kwargs):
                seen.append(list(argv))

        monkeypatch.setattr(launch_windows.subprocess, "Popen", _Popen)
        launch_windows.spawn_detached(str(tmp_path / "app.exe"))
        assert seen == [[str(tmp_path / "app.exe")]]


class TestMacOSBundleVerification:
    """What a ``.app`` must satisfy before ``open -a`` is allowed to run it.

    Runs on every platform: ``launch_macos`` imports anywhere (the whole point of
    ``_OPEN_BIN``'s composed form), and the writability answers are INJECTED rather than
    measured — which is also what lets the interesting case be asserted at all, since no
    CI runner ships a bundle with a writable executable directory.
    """

    _APP = "/Applications/Foo.app"

    @staticmethod
    def _app():
        from kiro_crew.computer_use.launch_macos import InstalledApp

        return InstalledApp(name="Foo", path="/Applications/Foo.app", source="/Applications")

    @staticmethod
    def _no_writable_executable(monkeypatch):
        """Answer the REPLACE half so a test can assert the CREATE half on its own.

        The two halves are independent permissions and are tested independently. These
        directory tests use a synthetic ``/Applications/Foo.app`` — deliberately, since a
        real bundle's parent is not assertable — and the executable probe fails CLOSED on a
        path that does not exist, which is correct behaviour and would mask every
        directory answer. Its own coverage is the ``_REWRITABLE_executable`` tests, which
        use real files with real modes.
        """
        from kiro_crew.computer_use import launch_macos

        monkeypatch.setattr(launch_macos, "_any_executable_is_writable", lambda _d: False)

    def test_a_writable_bundle_EXECUTABLE_directory_is_refused(self, monkeypatch):
        """The hole a parent-only probe left, and it is the COMMON case.

        ``/Applications`` is root-owned, so probing only the bundle's parent says nothing
        about who can rewrite the Mach-O inside an existing bundle. A bundle installed
        there by any user-space installer — every drag-install, every Homebrew cask — is
        owned by the installing user, so ``Foo.app/Contents/MacOS/Foo`` can be replaced
        without ``/Applications`` ever being writable. That bundle passed every check and
        launched agent-authored native code.
        """
        from kiro_crew.computer_use import launch_macos

        probed: list[str] = []

        def writable(directory: str) -> bool:
            probed.append(directory)
            return directory.endswith(os.path.join("Contents", "MacOS"))

        monkeypatch.setattr(launch_macos, "_directory_is_writable", writable)
        self._no_writable_executable(monkeypatch)
        assert launch_macos._writable_component(self._app()) is True
        # The parent WAS probed too — the fix adds layers, it does not move the check.
        assert "/Applications" in probed

    def test_an_unwritable_bundle_still_resolves(self, monkeypatch):
        # The positive control: without it the refusal above would also pass on an
        # implementation that refused every bundle.
        from kiro_crew.computer_use import launch_macos

        monkeypatch.setattr(launch_macos, "_directory_is_writable", lambda _d: False)
        self._no_writable_executable(monkeypatch)
        assert launch_macos._writable_component(self._app()) is False

    def test_the_probed_directories_are_FIXED_not_read_from_the_bundle(self, monkeypatch):
        """The bundle must not get to say which directory is judged.

        ``CFBundleExecutable`` lives inside the very directory whose trustworthiness is in
        question, so honouring it would let a crafted value aim the probe outside the
        bundle — ``/tmp/x`` probes ``/tmp``, ``../../..`` walks up out of it — and the
        target would choose its own examiner. The three locations are therefore fixed, and
        every one of them is under the bundle or its parent.
        """
        from kiro_crew.computer_use import launch_macos

        probed: list[str] = []
        monkeypatch.setattr(
            launch_macos, "_directory_is_writable", lambda d: probed.append(d) or False
        )
        self._no_writable_executable(monkeypatch)
        # A hostile plist, which must change nothing: the reader is not consulted here.
        monkeypatch.setattr(
            "kiro_crew.computer_use.apps_macos.read_bundle_plist",
            lambda _b: {"CFBundleExecutable": "/tmp/evil"},
        )
        launch_macos._writable_component(self._app())
        assert probed == [
            "/Applications",
            self._APP,
            os.path.join(self._APP, "Contents"),
            os.path.join(self._APP, "Contents", "MacOS"),
        ]

    @pytest.mark.parametrize(
        "writable",
        ["/Applications", _APP, "Contents", os.path.join("Contents", "MacOS")],
    )
    def test_a_writable_directory_at_ANY_level_refuses(self, monkeypatch, writable):
        """Write access to a SINGLE directory on the path is enough to control what runs.

        So each one has to refuse on its own. ``Contents`` is the one an endpoints-only
        check missed, and it is not "deeper nesting": owning it is enough to
        ``mv Contents/MacOS Contents/MacOS.bak && mkdir Contents/MacOS`` and end up with an
        agent-owned executable directory that every other probe then calls unwritable. It
        also holds the ``Info.plist``, so the same access rewrites ``CFBundleIdentifier``
        and defeats the pre-spawn identity deny as well.
        """
        from kiro_crew.computer_use import launch_macos

        monkeypatch.setattr(launch_macos, "_directory_is_writable", lambda d: d.endswith(writable))
        self._no_writable_executable(monkeypatch)
        assert launch_macos._writable_component(self._app()) is True

    def test_a_REWRITABLE_executable_is_refused_though_no_directory_permits_creates(
        self, monkeypatch, tmp_path
    ):
        """Create-permission and replace-permission are different, so both are tested.

        Directory writability governs create, unlink and rename; rewriting an existing
        file's bytes needs write on the file inode and no directory permission at all. So a
        create-probe answers "unwritable" for the ordinary drag-install and Homebrew-cask
        shape — root-owned directories that deny creates, holding an executable owned by
        the installing user — while ``open(exe, "r+b")`` replaces the binary in place.
        Verified against that revision: all four directory probes answered False and the
        launch was allowed.

        Real files with real modes rather than an injected answer, because the whole defect
        was that the injected question was the wrong one.
        """
        from kiro_crew.computer_use import launch_macos

        bundle = tmp_path / "Foo.app"
        macos = bundle / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        (macos / "Foo").write_bytes(b"ORIGINAL-SIGNED-MACHO")
        app = launch_macos.InstalledApp(name="Foo", path=str(bundle), source=str(tmp_path))
        # Every directory denies creates; only the executable's own mode permits writing.
        monkeypatch.setattr(launch_macos, "_directory_is_writable", lambda _d: False)
        assert os.access(str(macos / "Foo"), os.W_OK), "fixture precondition"
        assert launch_macos._writable_component(app) is True

    def test_an_unwritable_executable_still_resolves(self, monkeypatch, tmp_path):
        # The positive control for the probe above: a bundle whose binary this user cannot
        # rewrite must still launch, or the refusal would be unconditional.
        from kiro_crew.computer_use import launch_macos

        bundle = tmp_path / "Foo.app"
        macos = bundle / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        exe = macos / "Foo"
        exe.write_bytes(b"ORIGINAL")
        os.chmod(exe, stat.S_IRUSR | stat.S_IXUSR)
        app = launch_macos.InstalledApp(name="Foo", path=str(bundle), source=str(tmp_path))
        monkeypatch.setattr(launch_macos, "_directory_is_writable", lambda _d: False)
        if os.access(str(exe), os.W_OK):
            # An elevated process (CI's windows-latest runner is one) bypasses the mode.
            pytest.skip("this user can write a read-only file, so the mode proves nothing")
        assert launch_macos._writable_component(app) is False

    def test_a_READ_ONLY_mode_on_a_file_this_user_OWNS_is_not_trust(self, monkeypatch, tmp_path):
        """Mode is not durable; ownership is.

        The owner of a file may ``chmod`` it at will, with no privilege, so a read-only mode
        on a file this user owns is a fact the same user can undo between the check and the
        ``exec``: replace the binary, ``chmod a-w``, launch. ``st_uid`` cannot be changed that
        way — ``chown`` to another user requires privilege — so the durable question is "could
        this user have written it", not "can they right now".

        Asserted through an injected ``st_uid`` rather than a real chmod, because the answer
        must not depend on the suite's own privilege level (CI's runner is elevated and
        bypasses the mode entirely).
        """
        from kiro_crew.computer_use import launch_macos

        bundle = tmp_path / "Foo.app"
        macos = bundle / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        exe = macos / "Foo"
        exe.write_bytes(b"AGENT-AUTHORED")
        os.chmod(exe, stat.S_IRUSR | stat.S_IXUSR)
        app = launch_macos.InstalledApp(name="Foo", path=str(bundle), source=str(tmp_path))
        monkeypatch.setattr(launch_macos, "_directory_is_writable", lambda _d: False)
        monkeypatch.setattr(launch_macos.os, "geteuid", lambda: 501, raising=False)
        monkeypatch.setattr(launch_macos.os, "access", lambda *_a, **_k: False)
        real_stat = os.stat

        class _Owned:
            st_uid = 501

        monkeypatch.setattr(
            launch_macos.os,
            "stat",
            lambda p, *a, **k: _Owned() if str(p).endswith("Foo") else real_stat(p, *a, **k),
        )
        assert launch_macos._writable_component(app) is True

    def test_a_file_owned_by_SOMEONE_ELSE_and_unwritable_resolves(self, monkeypatch, tmp_path):
        # The positive control for the ownership rule: a bundle whose executable belongs to
        # root and denies writing is exactly what a real system install looks like, and it
        # must still launch.
        from kiro_crew.computer_use import launch_macos

        bundle = tmp_path / "Foo.app"
        macos = bundle / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        (macos / "Foo").write_bytes(b"SIGNED")
        app = launch_macos.InstalledApp(name="Foo", path=str(bundle), source=str(tmp_path))
        monkeypatch.setattr(launch_macos, "_directory_is_writable", lambda _d: False)
        monkeypatch.setattr(launch_macos.os, "geteuid", lambda: 501, raising=False)
        monkeypatch.setattr(launch_macos.os, "access", lambda *_a, **_k: False)
        real_stat = os.stat

        class _Root:
            st_uid = 0

        monkeypatch.setattr(
            launch_macos.os,
            "stat",
            lambda p, *a, **k: _Root() if str(p).endswith("Foo") else real_stat(p, *a, **k),
        )
        assert launch_macos._writable_component(app) is False

    def test_a_MISSING_executable_directory_fails_CLOSED(self, monkeypatch, tmp_path):
        # An unverifiable bundle is refused, never admitted. A bundle with no
        # ``Contents/MacOS`` is not one ``open -a`` could run either, so refusing costs
        # nothing and the alternative is a branch that silently skips the check.
        from kiro_crew.computer_use import launch_macos

        bundle = tmp_path / "Foo.app"
        bundle.mkdir()
        app = launch_macos.InstalledApp(name="Foo", path=str(bundle), source=str(tmp_path))
        monkeypatch.setattr(launch_macos, "_directory_is_writable", lambda _d: False)
        assert launch_macos._writable_component(app) is True

    def test_the_bundle_id_is_read_through_the_HARDENED_reader(self):
        # ``target_identity`` DOES read the plist, for ``CFBundleIdentifier``. Not with a
        # bare ``open``: the bundle is agent-choosable, so the read must keep
        # ``apps_macos``' realpath + sensitive-path re-check + ``O_NOFOLLOW`` path. A
        # second reader here would be a second chance to lose those three.
        from kiro_crew.computer_use import launch_macos

        source = inspect.getsource(launch_macos)
        assert "bundle_identity_at" in source
        assert "plistlib" not in source, "launch_macos must not parse a plist itself"


class TestResolvedIdentityReachesThePolicy:
    """The platform half of the pre-spawn check: what ``permit`` is actually handed.

    Split from :class:`TestLaunchDispatch` because the fake supplies its own identity, so
    every test that goes through it passes even when a real driver forwards none. These
    drive ``backend.run_launch`` and each ``target_identity`` directly, which is the only
    place the wiring is observable.
    """

    @staticmethod
    def _run(identity, *, denied="", found=None):
        """``run_launch`` with everything injected. Returns ``(result, spawned, seen)``.

        *found* is what ``find`` answers AFTER the spawn (it always answers ``None`` before,
        so the already-running branch is not taken) — the hook for asserting the check on
        the identity the OS publishes once a window exists.
        """
        from kiro_crew.computer_use import backend as be
        from kiro_crew.computer_use.policy import PolicyConfig, check_app

        spawned: list[str] = []
        seen: list[LaunchIdentity] = []
        cfg = PolicyConfig(extra_denied_apps=(denied,) if denied else ())

        def permit(who: LaunchIdentity) -> "str | None":
            seen.append(who)
            return check_app(who.as_app_ref(), cfg)

        def refuse_launched(ref: AppRef) -> "str | None":
            seen.append(LaunchIdentity(display=ref.name, key=ref.bundle_id))
            return check_app(ref, cfg)

        result = be.run_launch(
            "foo",
            resolve=lambda _q: ("/opt/foo/foo.bin", "Foo"),
            find=lambda _n: found if spawned else None,
            spawn=spawned.append,
            permit=permit,
            identity=identity,
            refuse_launched=refuse_launched,
        )
        return result, spawned, seen

    def test_the_PUBLISHED_identity_is_checked_once_a_window_exists(self):
        """The check the pre-spawn one structurally cannot make.

        A packaged app's window is fronted by ``ApplicationFrameHost``, so
        ``apps_windows`` publishes the WINDOW TITLE as both name and bundle id — the
        broker's image name identifies no application. That title is the only spelling an
        operator can write a rule against, and it does not exist until a window does: before
        the spawn the catalog offers ``store.exe`` and nothing else. Measured on a real host,
        ``extra_denied_apps: ["Microsoft Store"]`` matched neither pre-spawn identity and the
        launch reported success.

        This cannot stop the process starting, and does not claim to. What it stops is the
        launch REPORTING success, so nothing downstream snapshots or drives the window.
        """
        # A HOSTED window, which is the shape this covers: ``_app_ref`` puts the title into
        # ``name`` and ``bundle_id``, and those are the two fields an operator pattern is
        # matched against. An app that reports its OWN executable keeps the title in
        # ``window_title`` alone, which no operator pattern reads — a separate, pre-existing
        # limitation of ``policy._matches_operator_pattern`` and not something this check can
        # reach.
        published = AppRef(
            name="Microsoft Store",
            pid=4242,
            bundle_id="Microsoft Store",
            window_title="Microsoft Store",
        )
        result, spawned, seen = self._run(
            lambda target, display: LaunchIdentity(display=display, key="store.exe"),
            denied="Microsoft Store",
            found=published,
        )
        assert result.ok is False, "a denied packaged app was reported as launched"
        assert result.app is None, "the refusal still handed back a drivable window"
        # The process DID start — that is the stated residual, not a silent one.
        assert spawned == ["/opt/foo/foo.bin"]
        # And the pre-spawn identity really did MISS, which is the whole reason this check
        # exists. Asserted rather than assumed: if a future resolver learned the title, the
        # test would otherwise keep passing while covering nothing.
        pre = seen[0].as_app_ref()
        assert (pre.name, pre.bundle_id) == ("Foo", "store.exe")
        assert (
            policy.check_app(pre, policy.PolicyConfig(extra_denied_apps=("Microsoft Store",)))
            is None
        ), "the pre-spawn identity already matched, so this test proves nothing"

    def test_an_allowed_app_is_NOT_refused_after_its_window_appears(self):
        # The positive control for the check above: without it, the third check would
        # refuse every launch and the two assertions above would still pass.
        published = AppRef(name="Foo", pid=7, bundle_id="foo.bin", window_title="Foo — Untitled")
        result, spawned, _seen = self._run(
            lambda target, display: LaunchIdentity(display=display, key="foo.bin"),
            found=published,
        )
        assert result.ok is True
        assert result.app is published
        assert spawned == ["/opt/foo/foo.bin"]

    def test_the_identity_supplier_is_what_the_policy_SEES(self):
        # ``run_launch``'s half of the contract: whatever the platform supplies is what
        # reaches ``permit``. The DRIVERS' half is pinned separately below — this one
        # injects the supplier, so it cannot see a driver that forgets to pass one.
        _result, _spawned, seen = self._run(
            lambda target, display: LaunchIdentity(display=display, key="foo.bin")
        )
        assert [(w.display, w.key) for w in seen] == [("Foo", "foo.bin")]

    @pytest.mark.parametrize("driver_mod", ["windows_driver", "macos_driver"])
    def test_EVERY_driver_forwards_its_platform_identity_supplier(self, driver_mod):
        """A driver that omits ``identity=`` restores the vulnerability, silently.

        ``run_launch``'s ``identity`` parameter defaults to ``None`` and that default
        degrades to the pre-fix display-name-only check, so the omission is invisible: with
        the argument deleted from BOTH drivers the entire launch suite stayed green, because
        every dispatch-level test runs through the fake, which supplies its own identity.

        Asserted against the source rather than by calling the driver, because constructing
        a real one needs the platform's native UI-Automation/AX stack — which is exactly the
        reason the gap existed. Both drivers are checked on every OS for the same reason.
        """
        import importlib

        module = importlib.import_module(f"kiro_crew.computer_use.{driver_mod}")
        source = inspect.getsource(module)
        launcher = "launch_windows" if driver_mod == "windows_driver" else "launch_macos"
        assert f"identity={launcher}.target_identity," in source, (
            f"{driver_mod} does not forward its identity supplier to run_launch, so the "
            "pre-spawn policy check sees only the display name"
        )

    def test_a_deny_on_the_OS_IDENTITY_stops_the_SPAWN(self):
        # The point of the whole mechanism: refused, and no process created.
        result, spawned, _seen = self._run(
            lambda target, display: LaunchIdentity(display=display, key="foo.bin"),
            denied="foo.bin",
        )
        assert result.ok is False
        assert spawned == [], "the denied target was spawned anyway"

    def test_NO_supplier_degrades_to_the_display_name_only(self):
        # The default is the pre-fix behaviour, which is exactly why a driver that forgets
        # to pass ``identity=`` must be caught by the test above rather than by this one.
        _result, _spawned, seen = self._run(None)
        assert [(w.display, w.key) for w in seen] == [("Foo", "")]
        assert seen[0].as_app_ref().bundle_id == "Foo"

    @pytest.mark.skipif(not IS_WINDOWS, reason="launch_windows is the Windows resolver")
    def test_the_windows_key_is_the_RESOLVED_basename(self, tmp_path):
        """An 8.3 alias must not be able to rename the target out of a deny rule.

        Short names exist by default on the system volume, so an ``App Paths`` value (the
        hive is agent-writable) can name ``…\\SOMEVE~1.EXE`` for a file whose real name is
        ``SomeVeryLongName.exe``. ``resolve_target`` accepts it — it compares
        ``basename(realpath(...))`` against the catalog key — so reporting the RAW basename
        handed the policy a string the operator's ``someverylongname.exe`` rule cannot
        match, and the denied application spawned. Verified against that revision.
        """
        from kiro_crew.computer_use import launch_windows

        real = tmp_path / "SomeVeryLongName.exe"
        real.write_bytes(b"MZ")
        short = tmp_path / "SOMEVE~1.EXE"
        if not short.is_file():
            pytest.skip("8.3 short names are disabled on this volume")
        who = launch_windows.target_identity(str(short), "SomeVeryLongName")
        assert who.key == "SomeVeryLongName.exe"

    def test_the_macos_key_is_the_BUNDLE_ID(self, monkeypatch):
        # The macOS spelling that matters: the built-in denylist is bundle PREFIXES and
        # the operator's rules are written the way refusals print them.
        from kiro_crew.computer_use import launch_macos

        monkeypatch.setattr(
            "kiro_crew.computer_use.apps_macos.bundle_identity_at",
            lambda _b: ("com.example.Foo", "Foo"),
        )
        who = launch_macos.target_identity("/Applications/Foo.app", "Foo")
        assert (who.display, who.key) == ("Foo", "com.example.Foo")


class TestLaunchDispatch:
    """The chokepoint, through the shipped fake — so this runs on every platform."""

    def test_launching_returns_the_new_window_tree(self, fake_computer_backend):
        # The launch's own snapshot is what turns "it opened" into "here is what you
        # can click": a fresh window has no cached indices, so without it the model's
        # only possible next call is get_state on the app it just launched.
        out = _launch("Fake Draw")
        assert not out.startswith(ERROR_PREFIX)
        assert "launched Fake Draw" in out
        assert "Refreshed state:" in out
        assert [name for name, _kw in fake_computer_backend.calls] == [
            "launch_app",
            "snapshot",
        ]

    def test_an_already_running_app_is_refused_rather_than_relaunched(self, fake_computer_backend):
        # A second copy of an editor is a second unsaved document, and the model's
        # actual goal (a window to drive) is already met.
        out = _launch(FAKE_FILES_APP.name)
        assert out.startswith(ERROR_PREFIX)
        assert TOOL_GET_STATE in out

    def test_a_process_with_no_window_yet_is_a_SUCCESS(self, fake_computer_backend):
        # The branch that stops a model launching twice. Reporting failure for "the
        # process started but is still loading" is what makes the second attempt
        # happen, and the second attempt is what produces two copies.
        fake_computer_backend.launched_with_window = False
        out = _launch("Fake Draw")
        assert not out.startswith(ERROR_PREFIX)
        assert "do NOT launch it again" in out
        # No snapshot: there is no window to walk.
        assert [name for name, _kw in fake_computer_backend.calls] == ["launch_app"]

    def test_an_uninstalled_app_names_the_rule_not_a_path(self, fake_computer_backend):
        # The refusal has to teach the rule, because "try a path" is the one retry
        # that can never work.
        out = _launch("Nothing Installed")
        assert out.startswith(ERROR_PREFIX)
        assert "filesystem path" in out

    def test_the_self_target_denylist_blocks_a_launch_BEFORE_the_driver(
        self, fake_computer_backend
    ):
        # THE launch-specific security assertion. Every other verb resolves an
        # ``AppRef`` from the window list first, so the denylist sees a real identity;
        # a launch has only the name typed. Kiro Crew's own rule matches on name
        # substrings, so it fires — and it must fire before a process exists, which
        # is what the empty journal proves.
        out = _launch("Kiro Crew")
        assert out.startswith(ERROR_PREFIX)
        assert fake_computer_backend.calls == []

    def test_a_denied_BUNDLE_ID_is_refused_before_the_process_exists(self, fake_computer_backend):
        """A rule that only the resolved identity can match must still gate the spawn.

        The dispatcher's own pre-check sees only the name the caller typed, so an app
        whose OS-reported identity is denied while its display name is innocuous passes
        it. That identity is knowable BEFORE the spawn — the resolver produced it — so
        the refusal belongs there rather than after the fact: a detached spawn cannot be
        undone, and refusing afterwards only stops Kiro Crew driving a process it has
        already started.

        ``launch_app`` appearing alone in the journal, with no ``snapshot`` after it, is
        what distinguishes the two: the driver was reached (that is where the resolved
        identity exists) and nothing was driven.
        """
        fake_computer_backend.launchable = (
            AppRef(
                name="Innocuous",
                pid=4109,
                bundle_id="dev.kiro.crew.dashboard",
                window_id=8809,
                window_title="Dashboard",
            ),
        )
        out = _launch("Innocuous")
        assert out.startswith(ERROR_PREFIX)
        assert "blocked target" in out
        assert [name for name, _kw in fake_computer_backend.calls] == ["launch_app"]
        assert fake_computer_backend.launchable[0] not in fake_computer_backend.apps

    def test_the_operators_deny_rule_matches_the_OS_IDENTITY_not_just_the_name(
        self, fake_computer_backend, tmp_path
    ):
        """An operator's deny entry is written the way the OS names the app.

        Every other computer-use refusal prints the OS identity — ``notepad.exe``,
        ``com.apple.TextEdit`` — so that is the spelling an operator copies into
        ``extra_denied_apps``. A pre-spawn check that knew only the DISPLAY name
        (``notepad``) matched neither that nor a bundle-id rule, so the denied app
        started and was refused only once it was running. Verified against that
        revision: with ``extra_denied_apps: ["dev.kirocrew.fake.draw"]`` the launch
        succeeded.

        The empty ``apps`` list is the assertion that matters: the fake moves a
        successfully launched app into ``apps``, so its absence proves the refusal
        preceded the spawn rather than following it.
        """
        (tmp_path / "computer_use.json").write_text(
            json.dumps({"enabled": True, "extra_denied_apps": ["dev.kirocrew.fake.draw"]}),
            encoding="utf-8",
        )
        out = _launch("Fake Draw")
        assert out.startswith(ERROR_PREFIX)
        assert "blocked list by the operator" in out
        assert FAKE_DRAW_APP not in fake_computer_backend.apps

    def test_the_resolved_check_does_not_turn_an_ALLOW_list_into_a_refusal(self):
        """The union must not make an allow-list refuse the app it names.

        The other direction of the same fix, and the reason the two identities are
        checked as ONE ``AppRef`` rather than once per name: ``check_app`` refuses a name
        absent from a non-empty ``allowed_apps``, so refusing on any individual miss
        would mean an operator who allow-listed the display name was defeated by the
        bundle id failing that very list — and vice versa. Asserted here rather than
        through ``_launch`` because the dispatcher's earlier name-only check has its own
        (pre-existing, fail-closed) behaviour on an allow-list written in a spelling the
        caller did not type; this pins the resolved check alone.
        """
        who = LaunchIdentity(display="Fake Draw", key="dev.kirocrew.fake.draw")
        for spelling in ("fake draw", "dev.kirocrew.fake.draw"):
            cfg = policy.PolicyConfig(allowed_apps=(spelling,))
            assert tools._launch_refusal(who, cfg) is None, f"allowed_apps={spelling!r} refused"
        # The positive control: an allow-list naming a DIFFERENT app still refuses, so
        # the two assertions above cannot pass on an implementation that allows anything.
        narrow = policy.PolicyConfig(allowed_apps=("something else",))
        assert tools._launch_refusal(who, narrow) is not None

    def test_the_operators_DENY_list_gates_the_launch(self, fake_computer_backend, tmp_path):
        """The operator's own lists must gate the one verb that starts a process.

        An earlier draft called ``policy.denied_rule_for`` here — the built-in floor
        ALONE — which silently exempted ``extra_denied_apps`` and ``allowed_apps`` from
        `computer_launch_app`. Verified against that draft: with
        ``extra_denied_apps: ["fake draw"]`` the floor answered ``None`` and the app
        launched. The post-launch re-check cannot help, because the spawn is detached:
        refusing afterwards does not un-launch a process.

        The empty driver journal is the assertion that matters — it proves the refusal
        happened BEFORE anything ran, not after.
        """
        (tmp_path / "computer_use.json").write_text(
            json.dumps({"enabled": True, "extra_denied_apps": ["fake draw"]}), encoding="utf-8"
        )
        out = _launch("Fake Draw")
        assert out.startswith(ERROR_PREFIX)
        assert "blocked list by the operator" in out
        assert fake_computer_backend.calls == [], "the app was launched before being refused"

    def test_the_operators_ALLOW_list_gates_the_launch(self, fake_computer_backend, tmp_path):
        # The other half: an allow-list is a narrowing, and a verb that ignored it would
        # let the agent start anything while every other verb stayed bounded.
        (tmp_path / "computer_use.json").write_text(
            json.dumps({"enabled": True, "allowed_apps": ["something else"]}), encoding="utf-8"
        )
        out = _launch("Fake Draw")
        assert out.startswith(ERROR_PREFIX)
        assert "allowed-apps list" in out
        assert fake_computer_backend.calls == []

    def test_an_allowed_app_still_launches(self, fake_computer_backend, tmp_path):
        # The positive control: without it the two refusals above would also pass on an
        # implementation that refused every launch.
        (tmp_path / "computer_use.json").write_text(
            json.dumps({"enabled": True, "allowed_apps": ["fake draw"]}), encoding="utf-8"
        )
        out = _launch("Fake Draw")
        assert not out.startswith(ERROR_PREFIX), out
        assert "launched Fake Draw" in out

    def test_the_launch_is_audited_with_the_RESOLVED_identity(
        self, fake_computer_backend, monkeypatch
    ):
        """The SEL row for the one process-creating verb must name what was started.

        The upstream ``_audit_allowed`` runs before the launch, when there is no target
        yet, so it records an empty ``resources`` field. "A launch happened, of
        something" is not the record an operator needs, so the branch re-audits with the
        identity the OS reported.
        """
        rows: list[dict] = []

        class _Sel:
            def log_tool_invocation(self, **kwargs):
                rows.append(kwargs)

        monkeypatch.setattr(tools, "sel", lambda: _Sel())
        out = _launch("Fake Draw")
        assert not out.startswith(ERROR_PREFIX), out
        launches = [r for r in rows if r.get("tool_name") == TOOL_LAUNCH_APP]
        assert launches, rows
        # ``AppRef.label`` — the bundle id plus the pid, which is what every other
        # verb's audit row carries, so the launch row is readable beside them.
        assert any(
            FAKE_DRAW_APP.label == str(row.get("resources") or "") for row in launches
        ), launches

    def test_the_tool_is_in_the_mutating_set_not_the_read_only_one(self):
        # A verb that starts a process is the largest change this tool set can make,
        # so classifying it as read-only would put it on the same footing as reading
        # a tree.
        from kiro_crew.computer_use.types import MUTATING_TOOLS, READ_ONLY_TOOLS

        assert TOOL_LAUNCH_APP in MUTATING_TOOLS
        assert TOOL_LAUNCH_APP not in READ_ONLY_TOOLS

    def test_the_schema_accepts_no_path_or_argument_field(self):
        # Enforced as a SHAPE rather than trusted from the prose: a later edit adding
        # an ``args`` or ``path`` field would silently widen the verb from "open an
        # application" to "run a program with input", and nothing else in the suite
        # would notice.
        from kiro_crew.validation import MCP_COMPUTER_SCHEMAS

        fields = {spec.name for spec in MCP_COMPUTER_SCHEMAS[TOOL_LAUNCH_APP].fields}
        assert fields == {"app"}

    def test_the_advertised_schema_matches_the_validator(self):
        # A tool whose advertised ``required`` list is looser than the validator's
        # teaches the model a call shape that is always refused.
        from kiro_crew.mcp_computer import _tool_definitions

        entry = next(d for d in _tool_definitions() if d["name"] == TOOL_LAUNCH_APP)
        assert entry["inputSchema"]["required"] == ["app"]
        assert set(entry["inputSchema"]["properties"]) == {"app"}


@pytest.mark.skipif(not IS_WINDOWS, reason="asserts the real Windows host catalog")
class TestWindowsHostCatalog:
    """The invariant against the host's REAL catalog.

    The fabricated-catalog tests above prove the rules are implemented; this proves
    they are SATISFIABLE here — that a real installed application actually resolves.
    A resolver that refused everything would pass every test above.
    """

    def test_at_least_one_real_installed_app_resolves(self):
        from kiro_crew.computer_use import launch_windows

        catalog = launch_windows.installed_apps()
        assert catalog, "the host reported no installed applications at all"
        resolved = []
        for app in catalog:
            try:
                resolved.append(launch_windows.resolve_target(app.name))
            except ComputerUseError:
                continue
        assert resolved, "no entry in the host's own catalog survived resolution"

    def test_every_resolvable_entry_lives_under_a_protected_root(self):
        # The rule restated over real data: whatever resolves must be somewhere this
        # user cannot write. A failure here means the protected-root list is missing
        # a root that real applications use, which would be a genuine finding rather
        # than a test to relax.
        from kiro_crew.computer_use import launch_windows

        for app in launch_windows.installed_apps():
            try:
                executable, _name = launch_windows.resolve_target(app.name)
            except ComputerUseError:
                continue
            assert launch_windows._under_protected(executable), executable

    def test_the_local_windowsapps_alias_dir_is_never_a_launch_source(self):
        # Measured: %LOCALAPPDATA%\Microsoft\WindowsApps is ON PATH and writable, and
        # it is what shutil.which("mspaint") returns. Nothing that resolves may come
        # from there — that is the specific hole the protected-root rule closes.
        from kiro_crew.computer_use import launch_windows

        alias_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps")
        if not alias_dir or not os.path.isdir(alias_dir):
            pytest.skip("no execution-alias directory on this host")
        folded = os.path.realpath(alias_dir).casefold()
        for app in launch_windows.installed_apps():
            try:
                executable, _name = launch_windows.resolve_target(app.name)
            except ComputerUseError:
                continue
            assert not os.path.realpath(executable).casefold().startswith(folded)


def test_the_already_running_refusal_names_get_state():
    # A refusal a model cannot act on costs it a turn and teaches it nothing, and the
    # useful move here is specifically get_state rather than a retry.
    assert TOOL_GET_STATE in ERR_LAUNCH_ALREADY_RUNNING.format(
        app="A", title="T", tool=TOOL_GET_STATE
    )


def test_the_denylist_probe_shape_reaches_the_self_target_rule():
    # ``tools`` synthesizes an ``AppRef`` from the requested NAME because no window
    # exists yet. That is only sound if the self-target rule can actually fire on a
    # name-only ref — pinned here so a future denylist change that dropped
    # ``name_substrings`` would fail loudly rather than silently open the launch path
    # to Kiro Crew's own dashboard.
    probe = AppRef(name="Kiro Crew", pid=0, bundle_id="Kiro Crew", window_title="Kiro Crew")
    assert policy.denied_rule_for(probe) is not None


def test_launch_windows_imports_no_platform_module_at_MODULE_SCOPE():
    """``winreg`` does not exist off Windows, so importing it at module scope would
    break EVERY test that transitively touches ``kiro_crew`` on the Linux CI fleet.

    Asserted by AST rather than by importing, because that is the only form that
    fails on a Windows dev box: an ``import winreg`` at module scope succeeds here and
    would only go red on the shard nobody runs locally. The same reasoning
    ``test_computer_use_unsupported.py::test_no_module_scope_native_library_load``
    gives for a module-scope ``CDLL``.
    """
    import ast
    import pathlib

    from kiro_crew.computer_use import launch_windows

    source = pathlib.Path(launch_windows.__file__).read_text(encoding="utf-8")
    module_scope: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            module_scope.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_scope.add(node.module.split(".")[0])
    # The stdlib modules that exist everywhere, plus this package.
    assert "winreg" not in module_scope
    assert module_scope <= {
        "__future__",
        "dataclasses",
        "logging",
        "os",
        "subprocess",
        "typing",
        "kiro_crew",
    }


def test_the_fake_launch_catalog_is_disjoint_from_the_running_list():
    # The fake's three launch outcomes are only distinguishable while these two lists
    # disagree; a fixture edit that put the draw app in both would make the
    # successful-launch test unable to fail.
    from kiro_crew.testing.fake_computer_use import FAKE_APPS, FAKE_LAUNCHABLE

    assert FAKE_DRAW_APP in FAKE_LAUNCHABLE
    assert FAKE_DRAW_APP not in FAKE_APPS
