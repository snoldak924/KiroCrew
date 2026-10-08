"""An update that adds ``permissions.api`` / ``permissions.events`` entries is staged.

The app stays enabled on the grant set the owner approved; every enforcement
point (the app-token API allowlist, the WebSocket event scope, the hook
context's event bus) holds the new entries back until the owner approves them
through ``enable_app(grants_consent=<the entries shown>)``.
"""

from __future__ import annotations

import json

import pytest

from kiro_crew.apps import manager as manager_mod
from kiro_crew.apps.manager import (
    APP_MANIFEST_FILENAME,
    INSTALLED_META_FILENAME,
    app_dir,
    approved_manifest_permissions,
    enable_app,
    get_app,
    install_app,
    register_external_app,
    update_app,
)
from kiro_crew.dashboard import token_auth, ws_event_scope

APP = "test-app"
OLD_API = "/api/sessions"
NEW_API = "/api/memory"


def _source(tmp_path, sub, version="1.0.0", api=(), events=()):
    src = tmp_path / sub / APP
    src.mkdir(parents=True)
    manifest = {
        "name": APP,
        "version": version,
        "displayName": "Test App",
        "description": "staged grants",
        "author": "tester",
        "permissions": {"api": list(api), "events": list(events)},
    }
    (src / APP_MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    return src


@pytest.fixture()
def app_home(tmp_path, monkeypatch):
    home = tmp_path / "kirocrew-home"
    home.mkdir()
    monkeypatch.setenv("KIROCREW_HOME", str(home))
    (home / "config.json").write_text(
        json.dumps({"agent": {"apps_allow_third_party": True}}), encoding="utf-8"
    )
    monkeypatch.setattr(token_auth, "_app_perms_cache", {})
    return home


def _allowlist():
    token_auth._app_perms_cache.clear()
    return token_auth._app_api_allowlist(APP)


def _events():
    return ws_event_scope._read_declared_events(APP)


def _installed_v1(tmp_path):
    assert install_app(_source(tmp_path, "v1", api=[OLD_API], events=["slots:own"])).ok
    assert enable_app(APP).ok


def test_update_adding_api_and_event_entries_stays_enabled_on_old_grants(tmp_path, app_home):
    _installed_v1(tmp_path)

    result = update_app(
        _source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API], events=["slots:own", "log"])
    )

    assert result.ok, result.error
    assert result.notice == "grants_reconsent"
    info = get_app(APP)
    assert info["enabled"] is True
    assert info["version"] == "2.0.0"
    assert info["consentedGrants"] == {"api": [OLD_API], "events": ["slots:own"]}
    assert _allowlist() == (OLD_API,)
    assert _events() == (True, ws_event_scope.build_allowed_event_set(["slots:own"]))
    assert approved_manifest_permissions(info)["events"] == ["slots:own"]
    assert approved_manifest_permissions(info)["api"] == [OLD_API]


def test_grants_consent_approves_the_staged_entries(tmp_path, app_home):
    _installed_v1(tmp_path)
    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok

    # A plain enable of an enabled app is not an approval.
    assert enable_app(APP).ok
    assert get_app(APP)["consentedGrants"]
    assert _allowlist() == (OLD_API,)

    assert enable_app(APP, grants_consent={"api": [NEW_API], "events": []}).ok
    assert "consentedGrants" not in get_app(APP)
    assert _allowlist() == (OLD_API, NEW_API)


def test_staged_entries_are_held_back_while_the_new_tree_lands(tmp_path, app_home, monkeypatch):
    # Between the old tree moving aside and the new record landing, the new
    # manifest is on disk with no record beside it: that grants nothing.
    _installed_v1(tmp_path)
    real_copy = manager_mod._copy_app_tree
    seen = []

    def _copy_then_probe(source, dest):
        real_copy(source, dest)
        seen.append(_allowlist())

    monkeypatch.setattr(manager_mod, "_copy_app_tree", _copy_then_probe)
    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok
    assert seen == [()]
    assert _allowlist() == (OLD_API,)


def test_failed_widening_update_restores_unstaged_record(tmp_path, app_home, monkeypatch):
    _installed_v1(tmp_path)

    def _fail(source, dest):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(manager_mod, "_copy_app_tree", _fail)
    assert not update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok
    assert "consentedGrants" not in get_app(APP)
    assert _allowlist() == (OLD_API,)


def test_update_within_the_approved_set_stages_nothing(tmp_path, app_home):
    _installed_v1(tmp_path)
    result = update_app(_source(tmp_path, "v2", "2.0.0", api=[]))
    assert result.ok
    assert result.notice == ""
    assert "consentedGrants" not in get_app(APP)


def test_second_update_keeps_the_approved_baseline(tmp_path, app_home):
    # Staged entries must not become the baseline just because a later update
    # repeats them.
    _installed_v1(tmp_path)
    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok
    assert update_app(_source(tmp_path, "v3", "3.0.0", api=[OLD_API, NEW_API])).ok
    assert get_app(APP)["consentedGrants"]["api"] == [OLD_API]
    assert _allowlist() == (OLD_API,)

    # Narrowing back inside the approved set clears the stage.
    assert update_app(_source(tmp_path, "v4", "4.0.0", api=[OLD_API])).ok
    assert "consentedGrants" not in get_app(APP)


def test_unreadable_old_manifest_fails_closed(tmp_path, app_home):
    _installed_v1(tmp_path)
    (app_dir(APP) / APP_MANIFEST_FILENAME).write_text("{ corrupt", encoding="utf-8")

    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API])).ok

    assert get_app(APP)["consentedGrants"] == {"api": [], "events": []}
    assert _allowlist() == ()


def test_malformed_staged_record_fails_closed(tmp_path, app_home):
    _installed_v1(tmp_path)
    meta_path = app_dir(APP) / INSTALLED_META_FILENAME
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    data["consentedGrants"] = "not-a-record"
    meta_path.write_text(json.dumps(data), encoding="utf-8")

    assert _allowlist() == ()
    assert _events() == (True, frozenset())


def test_unreadable_install_record_grants_nothing(tmp_path, app_home):
    _installed_v1(tmp_path)
    (app_dir(APP) / INSTALLED_META_FILENAME).write_text("{ corrupt", encoding="utf-8")
    assert _allowlist() == ()


def test_self_registration_adding_events_is_staged(app_home):
    name = "ext-keypad"

    def _manifest(events):
        return {"name": name, "version": "1.0.0", "permissions": {"events": events}}

    assert register_external_app(name, "1.0.0", "Keypad", manifest_data=_manifest(["log"])).ok
    result = register_external_app(
        name, "1.1.0", "Keypad", manifest_data=_manifest(["log", "slots:all"])
    )

    assert result.ok, result.error
    assert result.notice == "grants_reconsent"
    info = get_app(name)
    assert info["enabled"] is True
    assert info["consentedGrants"] == {"api": [], "events": ["log"]}
    assert ws_event_scope._read_declared_events(name) == (
        True,
        ws_event_scope.build_allowed_event_set(["log"]),
    )
    assert enable_app(name, grants_consent={"api": [], "events": ["slots:all"]}).ok
    assert "consentedGrants" not in get_app(name)


def test_manifest_read_is_paired_with_an_unchanged_record(tmp_path, app_home, monkeypatch):
    # A narrowing update lands between the manifest read and the record read:
    # the widened manifest must not be judged against the record the narrowing
    # wrote, which stages nothing.
    _installed_v1(tmp_path)
    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok
    real_manifest = manager_mod.get_app_manifest
    narrowed = []

    def _manifest_then_narrow(name):
        widened = real_manifest(name)
        if not narrowed:
            narrowed.append(True)
            assert update_app(_source(tmp_path, "v3", "3.0.0", api=[OLD_API])).ok
        return widened

    monkeypatch.setattr(manager_mod, "get_app_manifest", _manifest_then_narrow)
    assert _allowlist() == (OLD_API,)


def test_record_that_keeps_changing_grants_nothing(tmp_path, app_home, monkeypatch):
    _installed_v1(tmp_path)
    real_read = manager_mod._read_installed
    calls = []

    def _always_new(name):
        meta = real_read(name)
        calls.append(1)
        return None if meta is None else manager_mod.replace(meta, updatedAt=str(len(calls)))

    monkeypatch.setattr(manager_mod, "_read_installed", _always_new)
    assert _allowlist() == ()


def test_detail_read_does_not_rewrite_a_freshly_staged_record(app_home, monkeypatch):
    # get_app read the record, then a registration staged it and wrote the new
    # manifest, then get_app read that manifest: writing its stale record back
    # would erase the stage.
    name = "ext-keypad"

    def _manifest(version, events):
        return {"name": name, "version": version, "permissions": {"events": events}}

    assert register_external_app(
        name, "1.0.0", "Keypad", manifest_data=_manifest("1.0.0", ["log"])
    ).ok
    real_read = manager_mod._read_installed
    raced = []

    def _read_then_register(app):
        meta = real_read(app)
        if not raced:
            raced.append(True)
            assert register_external_app(
                name, "1.1.0", "Keypad", manifest_data=_manifest("1.1.0", ["log", "slots:all"])
            ).ok
        return meta

    monkeypatch.setattr(manager_mod, "_read_installed", _read_then_register)
    row = get_app(name)
    monkeypatch.setattr(manager_mod, "_read_installed", real_read)
    assert row["version"] == "1.1.0"
    assert get_app(name)["consentedGrants"] == {"api": [], "events": ["log"]}


def test_approving_staged_events_changes_the_hook_signature(tmp_path, app_home):
    # The hook context's EventBus is built once per load; the reconciler reloads
    # on a signature change, so approval must change it.
    from kiro_crew.apps.hooks_integration import hook_signature

    _installed_v1(tmp_path)
    assert update_app(
        _source(tmp_path, "v2", "2.0.0", api=[OLD_API], events=["slots:own", "log"])
    ).ok
    staged = hook_signature(get_app(APP))
    assert enable_app(APP, grants_consent={"api": [], "events": ["log"]}).ok
    approved = hook_signature(get_app(APP))
    assert staged != approved
    assert approved_manifest_permissions(get_app(APP))["events"] == ["slots:own", "log"]


def test_record_restored_by_a_later_write_is_still_a_change(tmp_path, app_home, monkeypatch):
    # widen -> narrow inside the manifest read can put every record field back
    # (same second, same version); the write itself must still read as a change.
    _installed_v1(tmp_path)
    real_manifest = manager_mod.get_app_manifest
    real_write = manager_mod._write_installed
    restored = []

    def _widen_then_restore(name):
        if not restored:
            restored.append(True)
            original = manager_mod._read_installed(name)
            assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok
            widened = real_manifest(name)
            # Narrow back: the v1 manifest and a record with exactly its
            # original fields, as a same-second same-version refresh would.
            manifest_path = app_dir(name) / APP_MANIFEST_FILENAME
            manifest_path.write_text(
                (tmp_path / "v1" / APP / APP_MANIFEST_FILENAME).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            real_write(name, original)
            return widened
        return real_manifest(name)

    monkeypatch.setattr(manager_mod, "get_app_manifest", _widen_then_restore)
    assert _allowlist() == (OLD_API,)


@pytest.mark.parametrize("value", [[], False, 0, "", None, {}])
def test_present_but_falsy_record_fails_closed(tmp_path, app_home, value):
    _installed_v1(tmp_path)
    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok
    meta_path = app_dir(APP) / INSTALLED_META_FILENAME
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    data["consentedGrants"] = value
    meta_path.write_text(json.dumps(data), encoding="utf-8")

    assert _allowlist() == ()


def test_approval_covers_only_the_entries_the_owner_was_shown(tmp_path, app_home):
    # The owner saw /api/memory; an update then added /api/projects before the
    # click. Approving what was shown must leave the newer entry staged.
    later = "/api/projects"
    _installed_v1(tmp_path)
    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok
    assert update_app(_source(tmp_path, "v3", "3.0.0", api=[OLD_API, NEW_API, later])).ok

    result = enable_app(APP, grants_consent={"api": [NEW_API], "events": []})

    assert result.ok
    assert result.notice == "grants_reconsent"
    assert get_app(APP)["consentedGrants"]["api"] == [OLD_API, NEW_API]
    assert _allowlist() == (OLD_API, NEW_API)


def test_approval_of_an_entry_no_longer_declared_adds_nothing(tmp_path, app_home):
    # An entry shown then dropped from the manifest must not join the baseline,
    # or a later update re-adding it would be granted unseen.
    _installed_v1(tmp_path)
    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API, "/api/x"])).ok
    assert update_app(_source(tmp_path, "v3", "3.0.0", api=[OLD_API, "/api/x"])).ok

    assert enable_app(APP, grants_consent={"api": [NEW_API], "events": []}).ok

    assert get_app(APP)["consentedGrants"]["api"] == [OLD_API]


def test_grants_read_during_a_failed_update_rollback_grants_nothing(
    tmp_path, app_home, monkeypatch
):
    # The rollback removes the replacement tree before restoring the old one,
    # so for a moment neither the record nor the manifest exists. A read of the
    # replacement manifest in that window must not pass as "nothing staged".
    _installed_v1(tmp_path)
    real_replace = manager_mod.os.replace
    seen = []

    def _probe_rollback(src, dst):
        if "-update-old-" in str(src):
            # Rollback: dest is gone, the retired tree is about to come back.
            seen.append(manager_mod.staged_app_grants(APP, "api", lambda: [OLD_API, NEW_API]))
        return real_replace(src, dst)

    def _fail(source, dest):
        dest.mkdir(parents=True)
        (dest / APP_MANIFEST_FILENAME).write_text(
            (source / APP_MANIFEST_FILENAME).read_text(encoding="utf-8"), encoding="utf-8"
        )
        raise OSError("simulated copy failure")

    monkeypatch.setattr(manager_mod, "_copy_app_tree", _fail)
    monkeypatch.setattr(manager_mod.os, "replace", _probe_rollback)
    assert not update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok

    assert seen == [[]]
    assert not manager_mod._update_marker(APP).exists()
    assert _allowlist() == (OLD_API,)


def test_failed_rollback_keeps_the_marker_and_grants_nothing(tmp_path, app_home, monkeypatch):
    _installed_v1(tmp_path)
    real_write = manager_mod._write_installed
    calls = []

    def _fail_writes(name, meta):
        calls.append(1)
        raise OSError("disk full")

    monkeypatch.setattr(manager_mod, "_write_installed", _fail_writes)
    result = update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API]))
    monkeypatch.setattr(manager_mod, "_write_installed", real_write)

    assert not result.ok
    assert "rollback failed" in result.error
    assert manager_mod._update_marker(APP).exists()
    assert _allowlist() == ()


@pytest.mark.parametrize("break_it", ["corrupt", "updating"])
def test_approval_against_an_unreadable_manifest_changes_nothing(tmp_path, app_home, break_it):
    # Read as "declares nothing", an unreadable manifest would clear the record
    # and grant everything it declares once readable again.
    _installed_v1(tmp_path)
    assert update_app(_source(tmp_path, "v2", "2.0.0", api=[OLD_API, NEW_API])).ok
    manifest_path = app_dir(APP) / APP_MANIFEST_FILENAME
    good = manifest_path.read_text(encoding="utf-8")
    if break_it == "corrupt":
        manifest_path.write_text("{ corrupt", encoding="utf-8")
    else:
        manager_mod._update_marker(APP).write_text("", encoding="utf-8")

    result = enable_app(APP, grants_consent={"api": [], "events": []})

    assert not result.ok
    assert result.error_code == "grants_manifest_unreadable"
    manifest_path.write_text(good, encoding="utf-8")
    manager_mod._update_marker(APP).unlink(missing_ok=True)
    assert get_app(APP)["consentedGrants"]["api"] == [OLD_API]
    assert _allowlist() == (OLD_API,)
