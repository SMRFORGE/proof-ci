"""M8: canonicalisation, Ed25519 keys, signing, verification (spec Section 4.7)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from typer.testing import CliRunner

from proof_ci import models as m
from proof_ci import record as rec
from proof_ci import util
from proof_ci.adapters.agent.identity import resolve_identity
from proof_ci.cli import app
from proof_ci.record import (
    KeyError_,
    SignError,
    canonical_bytes,
    check_key_protection,
    generate_keypair,
    key_id,
    verify_record,
    weak_public_key,
)
from proof_ci.util import GitError, git_preflight, tree_hash

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-project"
GIT = shutil.which("git")
cli = CliRunner()
WINDOWS = os.name == "nt"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        [GIT or "git", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


# ------------------------------------------------------------- golden vectors

GOLDEN_PAYLOAD: dict[str, Any] = {
    "z": 1,
    "a": [3, 2, {"é": 'ü\n"', "b": True, "n": None}],
    "m": 0,
    "big": 12345678901234,
}
GOLDEN_CANONICAL = (
    b'{"a":[3,2,{"b":true,"n":null,"\xc3\xa9":"\xc3\xbc\\n\\""}],"big":12345678901234,"m":0,"z":1}'
)
GOLDEN_PUBLIC = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"
GOLDEN_KEY_ID = "56475aa75463474c"
GOLDEN_SIGNATURE = (
    "0ef1b57ff3a9caccfa292da7a6337757a313073fd73b577795a2c5683d5e8d1f"
    "511ae2db736fddc885c20959dac97889040677de9a0af3507eb852fcd3f0710d"
)


def test_golden_canonicalisation() -> None:
    assert canonical_bytes(GOLDEN_PAYLOAD) == GOLDEN_CANONICAL


def test_golden_signature_and_key_id() -> None:
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert raw.hex() == GOLDEN_PUBLIC
    assert key_id(raw) == GOLDEN_KEY_ID
    assert key.sign(canonical_bytes(GOLDEN_PAYLOAD)).hex() == GOLDEN_SIGNATURE


@pytest.mark.parametrize("bad", [1.5, {"x": [1, 2.0]}, {"a": {"b": 0.1}}])
def test_floats_are_refused(bad: Any) -> None:
    with pytest.raises(SignError):
        canonical_bytes({"v": bad})


def test_weak_public_keys() -> None:
    assert weak_public_key(bytes(32))
    assert weak_public_key(bytes.fromhex("01" + "00" * 31))
    assert weak_public_key(bytes.fromhex("ed" + "ff" * 30 + "7f"))  # y = p: non-canonical
    assert not weak_public_key(bytes.fromhex(GOLDEN_PUBLIC))


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def keyfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "keys" / "ed25519.pem"
    generate_keypair(path)
    monkeypatch.setenv("PROOF_CI_SIGNING_KEY", str(path))
    return path


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    if GIT is None:
        pytest.skip("git is required")
    repo = tmp_path / "demo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns(".proof-ci", "__pycache__"))
    (repo / "proof-ci.toml").write_text(
        f"[project]\ntest_command = {json.dumps([sys.executable, '-m', 'pytest'])}\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("__pycache__/\n.proof-ci/\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def signed_run(repo: Path, *extra: str) -> tuple[dict[str, Any], Path]:
    result = cli.invoke(
        app,
        [
            "run",
            "tests/test_text.py",
            "--sign",
            "--agent-id",
            "agent-7",
            "--json",
            "--repo",
            str(repo),
            *extra,
        ],
    )
    assert result.exit_code == 0, result.output
    summary: dict[str, Any] = json.loads(result.stdout)
    assert summary["record_path"]
    return summary, repo / summary["record_path"]


def verify(*args: str) -> tuple[int, dict[str, Any]]:
    result = cli.invoke(app, ["record", "verify", *args, "--json"])
    return result.exit_code, json.loads(result.stdout)


def schema_ok(doc: dict[str, Any]) -> bool:
    from referencing import Registry, Resource

    schemas = [m.load_schema(n) for n in m.SCHEMA_FILES]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    validator = jsonschema.Draft202012Validator(m.load_schema("run_record"), registry=registry)
    return not list(validator.iter_errors(doc))


# ------------------------------------------------------------ sign and verify


def test_run_sign_then_verify(demo: Path, keyfile: Path) -> None:
    summary, record_path = signed_run(demo)
    doc = json.loads(record_path.read_text(encoding="utf-8"))
    assert schema_ok(doc)
    payload = doc["payload"]
    assert payload["commit"]["tree"] == git(demo, "rev-parse", "HEAD^{tree}")
    assert payload["commit"]["tree"] == summary["commit_tree"]
    assert payload["commit"]["dirty"] is False
    assert payload["selection"]["mode"] == "explicit"
    assert payload["selection"]["selected_count"] == 2
    assert payload["summary"]["executed"] + payload["summary"]["replayed"] == len(
        payload["results"]
    )
    assert payload["summary"]["executed"] == 2
    assert payload["agent"] == {"id": "agent-7"}
    assert payload["savings"]["headline"] == summary["savings_headline"]  # signed with the rest
    assert doc["signature"]["key_id"] == key_id(base64.b64decode(doc["signature"]["public_key"]))
    text = record_path.read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in text
    assert keyfile.read_text(encoding="utf-8").split("\n")[1] not in text

    code, out = verify(str(record_path))
    assert (code, out["valid"], out["signer"]) == (0, True, "unknown")
    code, out = verify(str(record_path), "--pubkey", str(keyfile) + ".pub")
    assert (code, out["signer"]) == (0, "known")


def test_replayed_results_are_counted(demo: Path, keyfile: Path) -> None:
    signed_run(demo)
    _, record_path = signed_run(demo)
    payload = json.loads(record_path.read_text(encoding="utf-8"))["payload"]
    assert (payload["summary"]["executed"], payload["summary"]["replayed"]) == (0, 2)


def test_trusted_pubkeys_known_and_unlisted(demo: Path, keyfile: Path, tmp_path: Path) -> None:
    _, record_path = signed_run(demo)
    other = tmp_path / "other" / "ed25519.pem"
    generate_keypair(other)
    config = demo / "proof-ci.toml"
    base = config.read_text(encoding="utf-8")

    config.write_text(
        base + f"\n[record]\ntrusted_pubkeys = [{json.dumps(str(keyfile) + '.pub')}]\n",
        encoding="utf-8",
    )
    code, out = verify(str(record_path), "--repo", str(demo))
    assert (code, out["signer"]) == (0, "known")

    config.write_text(
        base + f"\n[record]\ntrusted_pubkeys = [{json.dumps(str(other) + '.pub')}]\n",
        encoding="utf-8",
    )
    code, out = verify(str(record_path), "--repo", str(demo))
    assert (code, out) == (4, {"valid": False, "reason": "signer not trusted"})


def test_wrong_pubkey_fails(demo: Path, keyfile: Path, tmp_path: Path) -> None:
    _, record_path = signed_run(demo)
    other = tmp_path / "x" / "ed25519.pem"
    generate_keypair(other)
    code, out = verify(str(record_path), "--pubkey", str(other) + ".pub")
    assert code == 4 and out["reason"] == "signer not trusted"


def test_require_known_key_without_key_fails(demo: Path, keyfile: Path) -> None:
    _, record_path = signed_run(demo)
    code, out = verify(str(record_path), "--require-known-key")
    assert (code, out["reason"]) == (4, "no trusted key configured")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["payload"]["summary"].__setitem__("passed", 99),
        lambda d: d["payload"]["commit"].__setitem__("tree", "0" * 40),
        lambda d: d["payload"]["results"].pop(),
        lambda d: d["payload"].__setitem__("extra", 1),
        lambda d: d["signature"].__setitem__("key_id", "0" * 16),
        lambda d: d.pop("signature"),
        lambda d: d["signature"].__setitem__("alg", "RSA"),
        lambda d: d["signature"].__setitem__("public_key", base64.b64encode(bytes(32)).decode()),
    ],
)
def test_mutations_fail_verify(demo: Path, keyfile: Path, tmp_path: Path, mutate: Any) -> None:
    _, record_path = signed_run(demo)
    doc = json.loads(record_path.read_text(encoding="utf-8"))
    mutate(doc)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    code, out = verify(str(bad))
    assert code == 4 and out["valid"] is False


def test_valid_signature_over_bad_schema_is_rejected(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    payload = {"schema_version": "1.0.0", "hello": "world"}
    env = rec._envelope(payload, key)
    path = tmp_path / "r.json"
    path.write_text(json.dumps(env), encoding="utf-8")
    assert verify_record(path, []).reason == "payload does not match schema"


def test_record_sign_last_run_and_tree_change(demo: Path, keyfile: Path) -> None:
    result = cli.invoke(app, ["run", "tests/test_text.py", "--repo", str(demo)])
    assert result.exit_code == 0
    sign = cli.invoke(app, ["record", "sign", "--agent-id", "a", "--json", "--repo", str(demo)])
    assert sign.exit_code == 0, sign.output
    record_path = Path(json.loads(sign.stdout)["record_path"])
    assert verify(str(record_path))[0] == 0
    last = json.loads((demo / ".proof-ci/last-run.json").read_text(encoding="utf-8"))
    assert last["record_path"]

    (demo / "new.txt").write_text("x", encoding="utf-8")  # changes the working tree
    again = cli.invoke(app, ["record", "sign", "--repo", str(demo)])
    assert again.exit_code == 2
    assert "no longer matches" in again.stderr


def test_record_sign_refuses_zero_test_run(demo: Path, keyfile: Path) -> None:
    assert cli.invoke(app, ["run", "--repo", str(demo)]).exit_code == 0  # nothing affected
    result = cli.invoke(app, ["record", "sign", "--repo", str(demo)])
    assert result.exit_code == 2
    assert "selected no tests" in result.stderr


def test_record_sign_has_no_arbitrary_input(demo: Path) -> None:
    help_text = cli.invoke(app, ["record", "sign", "--help"]).stdout
    assert "--input" not in help_text
    public = [
        n
        for n in dir(rec)
        if not n.startswith("_")
        and "sign" in n.lower()
        and getattr(getattr(rec, n), "__module__", None) == rec.__name__
    ]
    assert public == ["SignError", "SignOutcome", "sign_run"]


def test_record_sign_errors(demo: Path, keyfile: Path) -> None:
    assert cli.invoke(app, ["record", "sign", "--repo", str(demo)]).exit_code == 2  # no last run
    bad = cli.invoke(app, ["record", "sign", "--run", "not-a-uuid", "--repo", str(demo)])
    assert bad.exit_code == 2


def test_record_out_must_stay_in_repo(demo: Path, keyfile: Path, tmp_path: Path) -> None:
    result = cli.invoke(
        app,
        [
            "run",
            "tests/test_text.py",
            "--sign",
            "--record-out",
            str(tmp_path / "out.json"),
            "--repo",
            str(demo),
        ],
    )
    assert result.exit_code == 2
    assert "outside the repository" in result.stderr


def test_missing_key_is_reported(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROOF_CI_SIGNING_KEY", str(tmp_path / "nope.pem"))
    result = cli.invoke(app, ["run", "tests/test_text.py", "--sign", "--repo", str(demo)])
    assert result.exit_code == 2
    assert "no signing key" in result.stderr


# ----------------------------------------------------------------- tree hash


def test_tree_hash_in_linked_worktree(demo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    git(demo, "worktree", "add", "-q", str(wt), "-b", "wt-branch")
    assert (wt / ".git").is_file()  # a linked worktree: .git is a file
    assert tree_hash(wt) == git(wt, "rev-parse", "HEAD^{tree}")
    (wt / "extra.txt").write_text("x", encoding="utf-8")
    assert tree_hash(wt) != git(wt, "rev-parse", "HEAD^{tree}")
    assert "extra.txt" not in git(wt, "diff", "--cached", "--name-only")  # real index untouched


def test_git_preflight_rejects_old_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Fake:
        stdout = "git version 1.9.5"
        returncode = 0

    monkeypatch.setattr(util, "run_git", lambda *a, **k: Fake())
    with pytest.raises(GitError, match=r"git >= 2.0 required; found git version 1.9.5"):
        git_preflight(tmp_path)


# ------------------------------------------------------------- key protection


@pytest.mark.skipif(WINDOWS, reason="POSIX mode bits")
def test_posix_key_mode_and_refusal(tmp_path: Path) -> None:
    key = tmp_path / "k.pem"
    generate_keypair(key)
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert check_key_protection(key) == []
    os.chmod(key, 0o644)
    with pytest.raises(KeyError_, match="chmod 600"):
        check_key_protection(key)


@pytest.mark.skipif(WINDOWS, reason="POSIX mode bits")
def test_modeless_filesystem_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = tmp_path / "k.pem"
    generate_keypair(key)
    os.chmod(key, 0o777)
    monkeypatch.setattr(rec, "_mount_fstype", lambda p: "drvfs")
    warnings = check_key_protection(key)
    assert warnings and "drvfs" in warnings[0]
    monkeypatch.setattr(rec, "_mount_fstype", lambda p: "ext4")
    with pytest.raises(KeyError_):
        check_key_protection(key)


@pytest.mark.skipif(not WINDOWS, reason="Windows ACLs")
@pytest.mark.parametrize(
    "grant", ["*S-1-5-32-545:R", "*S-1-5-32-546:R", "*S-1-5-7:R", "*S-1-1-0:R", "*S-1-5-4:R"]
)
def test_windows_owner_only_acl_and_refusal(tmp_path: Path, grant: str) -> None:
    key = tmp_path / "k.pem"
    generate_keypair(key)
    assert rec._windows_unsafe_principals(key) == []
    assert check_key_protection(key) == []
    # Users, Guests, ANONYMOUS LOGON, Everyone, INTERACTIVE: all refused (allow-list).
    subprocess.run(["icacls", str(key), "/grant", grant], capture_output=True, check=True)
    with pytest.raises(KeyError_, match="readable by"):
        check_key_protection(key)


def test_parse_sddl_allow_list() -> None:
    user = "S-1-5-21-1-2-3-1001"
    owner_only = f"D:PAI(A;;FA;;;{user})"
    assert rec._unsafe_sids(rec._parse_sddl(owner_only), user) == []
    admin_system = f"D:PAI(A;;FA;;;{user})(A;;FA;;;SY)(A;;FA;;;BA)"
    assert rec._unsafe_sids(rec._parse_sddl(admin_system), user) == []
    loose = f"D:AI(A;;FR;;;BG)(A;;FA;;;{user})(A;ID;0x1200a9;;;WD)(A;;FR;;;S-1-5-21-9-9-9-500)"
    assert rec._unsafe_sids(rec._parse_sddl(loose), user) == [
        "S-1-1-0",
        "S-1-5-21-9-9-9-500",
        "S-1-5-32-546",
    ]
    deny_only = f"D:PAI(D;;FA;;;WD)(A;;FA;;;{user})"
    assert rec._unsafe_sids(rec._parse_sddl(deny_only), user) == []
    assert rec._parse_sddl("garbage") == []


def test_unreadable_acl_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not WINDOWS:
        pytest.skip("Windows ACLs")
    key = tmp_path / "k.pem"
    generate_keypair(key)
    monkeypatch.setattr(rec, "_windows_unsafe_principals", lambda p: None)
    warnings = check_key_protection(key)
    assert warnings and "could not inspect" in warnings[0]


def test_keygen_failure_leaves_no_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise KeyError_("cannot restrict")

    if WINDOWS:
        monkeypatch.setattr(rec, "_protect_windows", boom)
    else:
        monkeypatch.setattr(rec.os, "chmod", boom)
    key = tmp_path / "k.pem"
    with pytest.raises(KeyError_):
        generate_keypair(key)
    assert not key.exists()
    assert not key.with_name("k.pem.pub").exists()


def test_windows_user_prefers_username(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> str:
        raise OSError("no console")

    monkeypatch.setattr(os, "getlogin", boom)
    monkeypatch.setenv("USERNAME", "alice")
    assert rec._windows_user() == "alice"
    monkeypatch.delenv("USERNAME")
    with pytest.raises(KeyError_):
        rec._windows_user()


def test_keygen_cli_refuses_overwrite(tmp_path: Path) -> None:
    key = tmp_path / "k.pem"
    assert cli.invoke(app, ["record", "keygen", "--key", str(key)]).exit_code == 0
    assert key.with_name("k.pem.pub").is_file()
    assert cli.invoke(app, ["record", "keygen", "--key", str(key)]).exit_code == 2
    assert cli.invoke(app, ["record", "keygen", "--key", str(key), "--force"]).exit_code == 0


# ------------------------------------------------------------------ identity


def test_identity_resolution_is_generic() -> None:
    env = {
        "PROOF_CI_AGENT_ID": "env-id",
        "PROOF_CI_AGENT_NAME": "cursor",
        "PROOF_CI_AGENT_VERSION": "1.0",
        "PROOF_CI_AGENT_MODEL": "some-model",
        "PROOF_CI_AGENT_SESSION": "s1",
        "ANTHROPIC_API_KEY": "must-not-be-read",
    }
    ident = resolve_identity(None, "cfg-id", env)
    assert ident.as_payload() == {
        "id": "env-id",
        "agent_name": "cursor",
        "agent_version": "1.0",
        "model": "some-model",
        "session": "s1",
    }
    assert resolve_identity("cli-id", "cfg-id", env).id == "cli-id"
    assert resolve_identity(None, "cfg-id", {}).id == "cfg-id"
    fallback = resolve_identity(None, "", {})
    assert (fallback.id, fallback.defaulted) == ("local", True)


# ------------------------------------------------- review regression tests


def run_dir_of(repo: Path, summary: dict[str, Any]) -> Path:
    return repo / Path(summary["results_path"]).parent


def plain_run(repo: Path, *tests: str) -> dict[str, Any]:
    result = cli.invoke(app, ["run", *tests, "--json", "--repo", str(repo)])
    assert result.exit_code in (0, 1), result.output
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def test_record_sign_refuses_edited_counts(demo: Path, keyfile: Path) -> None:
    summary = plain_run(demo, "tests/test_math.py")  # 2 passed, 1 failed
    run_dir = run_dir_of(demo, summary)
    doc = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    doc["outcome"] = "passed"
    doc["counts"]["passed"] = 999
    doc["counts"]["failed"] = 0
    (run_dir / "summary.json").write_text(json.dumps(doc), encoding="utf-8")
    result = cli.invoke(app, ["record", "sign", "--run", summary["run_id"], "--repo", str(demo)])
    assert result.exit_code == 2
    assert "inconsistent" in result.stderr


def test_record_sign_refuses_results_outside_run_dir(
    demo: Path, keyfile: Path, tmp_path: Path
) -> None:
    summary = plain_run(demo, "tests/test_text.py")
    run_dir = run_dir_of(demo, summary)
    outside = tmp_path / "ext_results.json"
    shutil.copy(demo / summary["results_path"], outside)
    doc = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    doc["results_path"] = str(outside)
    (run_dir / "summary.json").write_text(json.dumps(doc), encoding="utf-8")
    result = cli.invoke(app, ["record", "sign", "--run", summary["run_id"], "--repo", str(demo)])
    assert result.exit_code == 2
    assert "outside the run directory" in result.stderr


def test_record_sign_malformed_summary_is_runtime_error(demo: Path, keyfile: Path) -> None:
    summary = plain_run(demo, "tests/test_text.py")
    (run_dir_of(demo, summary) / "summary.json").write_text("[]", encoding="utf-8")
    result = cli.invoke(app, ["record", "sign", "--run", summary["run_id"], "--repo", str(demo)])
    assert result.exit_code == 3
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_verify_rejects_internally_inconsistent_payload(
    demo: Path, keyfile: Path, tmp_path: Path
) -> None:
    _, record_path = signed_run(demo)
    doc = json.loads(record_path.read_text(encoding="utf-8"))
    payload = doc["payload"]
    payload["summary"]["passed"] = 999
    key, _ = rec.load_private_key(keyfile)
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(rec._envelope(payload, key)), encoding="utf-8")
    code, out = verify(str(forged))
    assert code == 4
    assert out["reason"].startswith("payload is internally inconsistent")


@pytest.mark.parametrize(
    "make",
    [
        lambda d: d["payload"]["summary"].__setitem__("duration_ms", 2**53),
        lambda d: d["payload"].__setitem__("timestamp", "\ud800"),
    ],
)
def test_verify_garbage_payload_is_a_verdict(
    demo: Path, keyfile: Path, tmp_path: Path, make: Any
) -> None:
    _, record_path = signed_run(demo)
    doc = json.loads(record_path.read_text(encoding="utf-8"))
    make(doc)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    code, out = verify(str(bad))
    assert code == 4 and out["valid"] is False


def test_verify_deep_nesting_and_duplicates_are_verdicts(tmp_path: Path) -> None:
    deep = tmp_path / "deep.json"
    deep.write_text('{"payload": ' + "[" * 100_000 + "]" * 100_000 + "}", encoding="utf-8")
    code, out = verify(str(deep))
    assert code == 4 and out["valid"] is False
    dup = tmp_path / "dup.json"
    dup.write_text('{"payload": {"a": 1, "a": 2}, "signature": {}}', encoding="utf-8")
    code, out = verify(str(dup))
    assert code == 4 and "duplicate key" in out["reason"]


def test_locally_excluded_file_changes_tree_and_dirty(demo: Path) -> None:
    head_tree = git(demo, "rev-parse", "HEAD^{tree}")
    (demo / "tests/test_hidden.py").write_text("def test_hidden():\n    pass\n", encoding="utf-8")
    exclude = demo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as fh:
        fh.write("tests/test_hidden.py\n")
    info = util.git_info(demo)
    assert info.tree != head_tree
    assert info.dirty is True
    # In-tree .gitignore rules still exclude ignored files.
    (demo / "ignored.log").write_text("x", encoding="utf-8")
    with (demo / ".gitignore").open("a", encoding="utf-8") as fh:
        fh.write("*.log\n")
    git(demo, "add", ".gitignore")
    git(demo, "commit", "-q", "-m", "ignore logs")
    (demo / "tests/test_hidden.py").unlink()
    assert util.git_info(demo).tree == git(demo, "rev-parse", "HEAD^{tree}")


def test_run_sign_refuses_when_tests_modify_the_tree(demo: Path, keyfile: Path) -> None:
    (demo / "tests/test_writer.py").write_text(
        "from pathlib import Path\n\n"
        "def test_writes():\n"
        "    p = Path(__file__).parent.parent / 'README.md'\n"
        "    p.write_text(p.read_text() + 'x', encoding='utf-8')\n",
        encoding="utf-8",
    )
    git(demo, "add", "-A")
    git(demo, "commit", "-q", "-m", "writer")
    result = cli.invoke(app, ["run", "tests/test_writer.py", "--sign", "--repo", str(demo)])
    assert result.exit_code == 2
    assert "no longer matches" in result.stderr
    assert not list((demo / ".proof-ci" / "runs").glob("*/record.json"))


@pytest.mark.parametrize("target", ["proof-ci.toml", ".git/config"])
def test_record_out_refuses_tracked_and_git_dir(demo: Path, keyfile: Path, target: str) -> None:
    before = (demo / target).read_bytes()
    result = cli.invoke(
        app,
        [
            "run",
            "tests/test_text.py",
            "--sign",
            "--record-out",
            str(demo / target),
            "--repo",
            str(demo),
        ],
    )
    assert result.exit_code == 2
    assert (demo / target).read_bytes() == before


def test_failing_run_keeps_exit_1_when_signing_fails(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROOF_CI_SIGNING_KEY", str(tmp_path / "missing.pem"))
    result = cli.invoke(app, ["run", "tests/test_math.py", "--sign", "--repo", str(demo)])
    assert result.exit_code == 1
    assert "no signing key" in result.stderr


def test_verify_reports_trust_source(demo: Path, keyfile: Path) -> None:
    _, record_path = signed_run(demo)
    code, out = verify(str(record_path), "--pubkey", str(keyfile) + ".pub")
    assert code == 0
    assert out["trust_source"] == "--pubkey"
    config = demo / "proof-ci.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"\n[record]\ntrusted_pubkeys = [{json.dumps(str(keyfile) + '.pub')}]\n",
        encoding="utf-8",
    )
    result = cli.invoke(app, ["record", "verify", str(record_path), "--json", "--repo", str(demo)])
    assert json.loads(result.stdout)["trust_source"].startswith("config:")
    assert "out of band" in result.stderr
