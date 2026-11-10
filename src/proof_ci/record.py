"""Signed run records: canonicalisation, Ed25519 keys, sign, verify (spec Section 4.7).

A valid signature proves exactly two things: the payload bytes have not
changed since signing, and they were signed by the holder of the private key
whose public half is in the envelope. It does NOT prove that pytest executed,
that the right tests ran, or that the machine was honest. A Phase 1 record is
a tamper-evident statement of what this proof-ci installation reported.

To keep that statement meaningful, proof-ci signs only payloads it builds
itself from a run's own artifacts; there is deliberately no function here that
signs caller-supplied JSON.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import ValidationError

from proof_ci import __version__
from proof_ci.adapters.agent.identity import Identity
from proof_ci.config import Config, user_config_dir
from proof_ci.models import RunRecord, RunSummary, TestResult
from proof_ci.util import GitError, git_preflight, tree_hash

__all__ = [
    "KEY_ENV",
    "KeyError_",
    "SignError",
    "VerifyResult",
    "canonical_bytes",
    "default_key_path",
    "generate_keypair",
    "key_id",
    "load_private_key",
    "load_public_key",
    "resolve_key_path",
    "sign_run",
    "verify_record",
    "weak_public_key",
]

KEY_ENV = "PROOF_CI_SIGNING_KEY"
KEY_FILENAME = "ed25519.pem"
MODELESS_FS = ("drvfs", "9p", "cifs", "smbfs", "fuse", "nfs")


class KeyError_(Exception):
    """The signing key is missing, unreadable, or not protected. Exit 2 or 3 by caller."""


class SignError(Exception):
    """Signing refused (exit 2) or impossible (``runtime=True``: exit 3)."""

    def __init__(self, message: str, *, runtime: bool = False) -> None:
        super().__init__(message)
        self.runtime = runtime


# ------------------------------------------------------------- canonicalisation


def _reject_floats(value: Any, path: str = "payload") -> None:
    if isinstance(value, float):
        raise SignError(f"refusing to sign a floating-point number at {path}")
    if isinstance(value, dict):
        for k, v in value.items():
            _reject_floats(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _reject_floats(v, f"{path}[{i}]")


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """RFC 8785 serialisation of ``payload``; floats are refused."""
    _reject_floats(payload)
    return rfc8785.dumps(payload)


def key_id(raw_public_key: bytes) -> str:
    """First 16 hex characters of SHA-256 over the raw 32-byte public key."""
    return hashlib.sha256(raw_public_key).hexdigest()[:16]


def _raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


# ------------------------------------------------------------------ key paths


def default_key_path() -> Path:
    return user_config_dir() / KEY_FILENAME


def resolve_key_path(cfg: Config | None, key_arg: Path | None) -> Path:
    """``PROOF_CI_SIGNING_KEY`` > ``--key`` > ``[record].key_path`` > platformdirs default."""
    env = os.environ.get(KEY_ENV)
    if env:
        return Path(env)
    if key_arg is not None:
        return key_arg
    if cfg is not None and cfg.record.key_path:
        return cfg.resolve(cfg.record.key_path)
    return default_key_path()


# ------------------------------------------------------------- key protection


def _windows_user() -> str:
    user = os.environ.get("USERNAME")
    if user:
        return user
    try:
        return os.getlogin()
    except OSError as exc:
        raise KeyError_("cannot determine the current Windows user for the key ACL") from exc


def _system32_tool(name: str) -> str | None:
    """A Windows system tool by absolute path.

    PATH lookup is not used: Git Bash and MSYS put a Unix ``whoami`` ahead of the
    Windows one, which would silently disable the ACL check.
    """
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    candidate = Path(root) / "System32" / f"{name}.exe"
    return str(candidate) if candidate.is_file() else None


def _current_user_sid() -> str | None:
    """The current user's SID from ``whoami /user`` (locale-independent)."""
    exe = _system32_tool("whoami")
    if exe is None:
        return None
    proc = subprocess.run(
        [exe, "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    match = re.search(r"\"(S-1-[0-9-]+)\"", proc.stdout)
    return match.group(1) if match else None


def _icacls() -> str | None:
    return _system32_tool("icacls")


def _protect_windows(path: Path) -> None:
    exe = _icacls()
    if exe is None:
        sys.stderr.write(
            f"warning: icacls not found; {path} is not restricted to the current user\n"
        )
        return
    sid = _current_user_sid()
    # Grant by SID when known: a bare name can resolve to a same-named local account.
    target = f"*{sid}" if sid else _windows_user()
    proc = subprocess.run(
        [exe, str(path), "/inheritance:r", "/grant:r", f"{target}:F"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise KeyError_(f"icacls could not restrict {path}: {(proc.stderr or proc.stdout).strip()}")


# SDDL aliases icacls may print instead of SIDs.
_SDDL_ALIASES = {
    "SY": "S-1-5-18",
    "BA": "S-1-5-32-544",
    "CO": "S-1-3-0",
    "OW": "S-1-3-4",
    "WD": "S-1-1-0",
    "BU": "S-1-5-32-545",
    "AU": "S-1-5-11",
    "AN": "S-1-5-7",
    "BG": "S-1-5-32-546",
    "PU": "S-1-5-32-547",
    "IU": "S-1-5-4",
    "NU": "S-1-5-2",
    "LS": "S-1-5-19",
    "NS": "S-1-5-20",
}
# Allowed besides the current user: SYSTEM, Administrators, CREATOR OWNER, OWNER RIGHTS.
_ALLOWED_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-0", "S-1-3-4"})
_ACE_RE = re.compile(r"\(([A-Z]+);[^;()]*;[^;()]*;[^;()]*;[^;()]*;([^;()]+)(?:;[^()]*)?\)")


def _parse_sddl(sddl: str) -> list[tuple[str, str]]:
    """(ace_type, sid) pairs from the DACL part of an SDDL string."""
    dacl = sddl.split("D:", 1)[1] if "D:" in sddl else ""
    dacl = dacl.split("S:", 1)[0]
    return [(typ, _SDDL_ALIASES.get(sid, sid)) for typ, sid in _ACE_RE.findall(dacl)]


def _unsafe_sids(aces: list[tuple[str, str]], user_sid: str) -> list[str]:
    """Allow-list: any granting ACE for a SID other than the user/SYSTEM/Administrators."""
    allowed = _ALLOWED_SIDS | {user_sid}
    return sorted({sid for typ, sid in aces if typ in ("A", "OA") and sid not in allowed})


def _windows_unsafe_principals(path: Path) -> list[str] | None:
    """SIDs other than the owner/SYSTEM/Administrators with access; None if it cannot be read."""
    exe = _icacls()
    user_sid = _current_user_sid()
    if exe is None or user_sid is None:
        return None
    fd, tmp_name = tempfile.mkstemp(prefix="proof-ci-acl-")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.unlink()  # icacls /save refuses to overwrite in some versions
        proc = subprocess.run(
            [exe, str(path), "/save", str(tmp), "/q"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0 or not tmp.exists():
            return None
        aces = _parse_sddl(tmp.read_bytes().decode("utf-16", errors="replace"))
    finally:
        tmp.unlink(missing_ok=True)
    if not aces:
        return None
    return _unsafe_sids(aces, user_sid)


def _mount_fstype(path: Path) -> str | None:
    """Filesystem type of the mount holding ``path`` (Linux /proc/mounts, macOS mount)."""
    target = str(path.resolve())
    entries: list[tuple[str, str]] = []
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/mounts", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 3:
                        entries.append((parts[1].replace("\\040", " "), parts[2]))
        elif sys.platform == "darwin":
            out = subprocess.run(["mount"], capture_output=True, text=True, check=False).stdout
            for line in out.splitlines():
                m = re.match(r"^.+? on (.+?) \(([^,)]+)", line)
                if m:
                    entries.append((m.group(1), m.group(2)))
    except OSError:
        return None
    best: tuple[str, str] | None = None
    for mount_point, fstype in entries:
        prefix = mount_point.rstrip("/") + "/"
        if (target == mount_point or target.startswith(prefix)) and (
            best is None or len(mount_point) > len(best[0])
        ):
            best = (mount_point, fstype)
    return best[1] if best else None


def check_key_protection(path: Path) -> list[str]:
    """Raise :class:`KeyError_` if the private key is readable by others; return warnings."""
    warnings: list[str] = []
    if os.name == "nt":
        bad = _windows_unsafe_principals(path)
        if bad is None:
            warnings.append(f"could not inspect the ACL of {path} (icacls unavailable)")
        elif bad:
            raise KeyError_(
                f"signing key {path} is readable by {', '.join(bad)}; restrict it with "
                f'icacls "{path}" /inheritance:r /grant:r "%USERNAME%":F'
            )
        return warnings
    st = path.stat()
    if stat.S_IMODE(st.st_mode) & 0o077 == 0:
        return warnings
    fstype = _mount_fstype(path) or ""
    getuid = getattr(os, "getuid", None)
    same_owner = getuid is not None and st.st_uid == getuid()
    if same_owner and fstype.startswith(MODELESS_FS):
        warnings.append(
            f"{path} is on a {fstype} mount that does not keep file modes; keep the signing key "
            "on a native filesystem (for WSL: ~/.config/proof-ci, not /mnt/c)"
        )
        return warnings
    raise KeyError_(f"signing key {path} is readable by group or others; run: chmod 600 {path}")


# ------------------------------------------------------------------- key I/O


def generate_keypair(path: Path, *, force: bool = False) -> Path:
    """Create ``path`` (PKCS#8 PEM, owner-only) and ``path.pub`` (SPKI PEM).

    The file is created empty, restricted, and only then filled, so a failure to
    restrict it never leaves a readable private key behind.
    """
    pub = path.with_name(path.name + ".pub")
    if path.exists() and not force:
        raise KeyError_(f"{path} already exists; use --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    os.close(fd)
    try:
        if os.name == "nt":
            _protect_windows(path)
        else:
            os.chmod(path, 0o600)
        with path.open("wb") as fh:
            fh.write(pem)
        pub.write_bytes(
            key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
    except BaseException:
        path.unlink(missing_ok=True)
        pub.unlink(missing_ok=True)
        raise
    return pub


def load_private_key(path: Path) -> tuple[Ed25519PrivateKey, list[str]]:
    if not path.is_file():
        raise KeyError_(f"no signing key at {path}; run `proof-ci record keygen` or set {KEY_ENV}")
    warnings = check_key_protection(path)
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise KeyError_(f"{path} is not a PKCS#8 PEM private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyError_(f"{path} is not an Ed25519 key")
    return key, warnings


def load_public_key(path: Path) -> bytes:
    """Raw 32-byte public key from an SPKI PEM file."""
    try:
        key = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise KeyError_(f"cannot read public key {path}: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise KeyError_(f"{path} is not an Ed25519 public key")
    return _raw_public(key)


# ------------------------------------------------------------------- signing


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _load_json(text: str) -> Any:
    """Strict JSON: duplicate keys are an error (RFC 8785 input must be duplicate-free)."""
    return json.loads(text, object_pairs_hook=_no_duplicates)


def _read_json(path: Path) -> Any:
    try:
        return _load_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise SignError(f"cannot read {path}: {exc}", runtime=True) from exc


_OUTCOME_KEYS = ("passed", "failed", "skipped", "error")


def _tally(results: list[dict[str, Any]]) -> dict[str, int]:
    tally = dict.fromkeys(_OUTCOME_KEYS, 0)
    for r in results:
        outcome = r.get("outcome")
        if outcome in tally:
            tally[outcome] += 1
    return tally


def _consistency_problem(payload: dict[str, Any]) -> str | None:
    """Why a payload's summary disagrees with its own results, or None."""
    results = payload.get("results")
    summary = payload.get("summary")
    if not isinstance(results, list) or not isinstance(summary, dict):
        return "summary or results missing"
    tally = _tally(results)
    for key in _OUTCOME_KEYS:
        if summary.get(key) != tally[key]:
            return f"summary.{key} is {summary.get(key)} but results contain {tally[key]}"
    replayed = sum(1 for r in results if isinstance(r, dict) and r.get("cache_hit"))
    if summary.get("replayed") != replayed or summary.get("executed") != len(results) - replayed:
        return "summary.executed/replayed do not match the results"
    selection = payload.get("selection")
    if isinstance(selection, dict) and selection.get("selected_count") != len(results):
        return "selection.selected_count does not match the number of results"
    blocking = [
        r
        for r in results
        if r.get("outcome") in ("failed", "error")
        and (r.get("flaky") or {}).get("status") not in ("confirmed", "ignored")
    ]
    outcome = summary.get("outcome")
    if outcome == "passed" and blocking:
        return "summary.outcome is passed but results contain failures that were not known flakes"
    if outcome == "failed" and not any(r.get("outcome") == "failed" for r in results):
        return "summary.outcome is failed but no result failed"
    if outcome == "error" and not any(r.get("outcome") == "error" for r in results):
        return "summary.outcome is error but no result errored"
    return None


def build_payload(cfg: Config, run_dir: Path, identity: Identity, *, tree: str) -> dict[str, Any]:
    """Build the payload from a run's own artifacts. Never from caller-supplied JSON.

    The artifacts live in the user's working copy, so they are validated and
    cross-checked rather than trusted: the summary must parse as a RunSummary,
    results must parse as TestResults, live inside the run directory, and agree
    with the summary's counts and outcome.
    """
    raw_summary = _read_json(run_dir / "summary.json")
    try:
        summary = RunSummary.model_validate(raw_summary).to_json()
    except ValidationError as exc:
        raise SignError(
            f"{run_dir / 'summary.json'} is not a valid RunSummary", runtime=True
        ) from exc
    results_path = cfg.resolve(summary["results_path"]).resolve()
    try:
        results_path.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise SignError("results_path points outside the run directory; refusing to sign") from exc
    raw_results = _read_json(results_path)
    if not isinstance(raw_results, list):
        raise SignError(f"{results_path} is not a list of results", runtime=True)
    try:
        results = [TestResult.model_validate(r).to_json() for r in raw_results]
    except ValidationError as exc:
        raise SignError(f"{results_path} contains an invalid TestResult", runtime=True) from exc
    environment = _read_json(run_dir / "environment.json")
    if not isinstance(environment, dict) or not {"python", "platform"} <= set(environment):
        raise SignError(f"{run_dir / 'environment.json'} is malformed", runtime=True)
    sel = dict(summary["selection"])
    if sel.get("selected_count", 0) == 0:
        raise SignError("cannot sign a run that selected no tests")
    if not summary.get("commit_sha"):
        raise SignError("the run has no git commit; a record needs a tree hash", runtime=True)
    sel.setdefault("base_ref", summary["commit_sha"])
    sel.setdefault("changed_files", [])
    sel.setdefault("fallback_full_run", False)
    sel.setdefault("fallback_reason", None)
    counts = summary["counts"]
    replayed = sum(1 for r in results if r.get("cache_hit"))
    command = " ".join([*cfg.project.test_command, *cfg.project.pytest_args])
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "run_id": summary["run_id"],
        "timestamp": summary["finished_at"],
        "commit": {
            "sha": summary["commit_sha"],
            "tree": tree,
            "dirty": bool(summary.get("working_tree_dirty")),
        },
        "selection": sel,
        "agent": identity.as_payload(),
        "environment": {
            "python": str(environment["python"]),
            "platform": str(environment["platform"]),
            "proof_ci_version": __version__,
            "os_release": platform.release(),
        },
        "checks": [{"name": "pytest", "command": command, "selector": sel["mode"]}],
        "summary": {
            "outcome": summary["outcome"],
            "passed": counts["passed"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "error": counts["error"],
            "executed": len(results) - replayed,
            "replayed": replayed,
            "duration_ms": summary["duration_ms"],
        },
        "results": results,
    }
    if summary.get("savings"):
        payload["savings"] = summary["savings"]
    problem = _consistency_problem(payload)
    if problem is not None:
        raise SignError(f"the run's artifacts are inconsistent ({problem}); refusing to sign")
    return payload


def _envelope(payload: dict[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    try:
        canonical = canonical_bytes(payload)
    except ValueError as exc:  # rfc8785 domain errors (e.g. integers beyond 2**53)
        raise SignError(f"payload cannot be canonicalised: {exc}", runtime=True) from exc
    raw = _raw_public(key.public_key())
    return {
        "payload": payload,
        "signature": {
            "alg": "Ed25519",
            "key_id": key_id(raw),
            "public_key": base64.b64encode(raw).decode("ascii"),
            "value": base64.b64encode(key.sign(canonical)).decode("ascii"),
        },
    }


@dataclass(frozen=True)
class SignOutcome:
    path: Path
    record: dict[str, Any]
    warnings: list[str]


def sign_run(
    cfg: Config,
    run_dir: Path,
    identity: Identity,
    key_path: Path,
    output: Path,
) -> SignOutcome:
    """Sign the run in ``run_dir`` (a directory ``proof-ci run`` wrote).

    The working tree is re-hashed at signing time and must equal the tree the
    run recorded, both for ``run --sign`` (tests may have modified files) and
    for ``record sign`` (the user may have edited files since). ``record sign``
    re-signs artifacts stored in the working copy, which the same user can edit;
    it is therefore weaker than ``run --sign``.
    """
    if not (run_dir / "summary.json").is_file():
        raise SignError(f"no proof-ci run at {run_dir}")
    raw = _read_json(run_dir / "summary.json")
    selection = raw.get("selection") if isinstance(raw, dict) else None
    if not isinstance(selection, dict):
        raise SignError(f"{run_dir / 'summary.json'} is not a valid RunSummary", runtime=True)
    if selection.get("selected_count", 0) == 0:
        raise SignError("cannot sign a run that selected no tests")
    recorded = raw.get("commit_tree")
    if not recorded:
        raise SignError("the run recorded no tree hash (git was unavailable)", runtime=True)
    try:
        git_preflight(cfg.repo_root)
        current = tree_hash(cfg.repo_root)
    except GitError as exc:
        raise SignError(str(exc), runtime=True) from exc
    if current != recorded:
        raise SignError(
            "the working tree no longer matches the tree this run tested (tree "
            f"{str(recorded)[:12]} -> {current[:12]}; tests or edits changed files); "
            "rerun instead of signing a stale result"
        )
    key, warnings = load_private_key(key_path)
    payload = build_payload(cfg, run_dir, identity, tree=str(recorded))
    envelope = _envelope(payload, key)
    try:
        RunRecord.model_validate(envelope)
    except ValidationError as exc:
        raise SignError(
            f"built record does not match the RunRecord schema: {exc}", runtime=True
        ) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(envelope, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return SignOutcome(output, envelope, warnings)


# ------------------------------------------------------------------ verifying


@dataclass(frozen=True)
class VerifyResult:
    valid: bool
    reason: str | None = None
    key_id: str | None = None
    run_id: str | None = None
    signer: str | None = None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"valid": self.valid}
        if self.reason:
            out["reason"] = self.reason
        if self.valid:
            out.update(key_id=self.key_id, run_id=self.run_id, signer=self.signer)
        return out


# Ed25519 public keys of small order (and the x = 0 points with the sign bit set).
# With such a key a "signature" can verify for many messages, so an embedded
# key like this would let `verify` report integrity for content nobody signed.
_SMALL_ORDER_KEYS = frozenset(
    bytes.fromhex(h)
    for h in (
        "0100000000000000000000000000000000000000000000000000000000000000",
        "0100000000000000000000000000000000000000000000000000000000000080",
        "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
        "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        "0000000000000000000000000000000000000000000000000000000000000000",
        "0000000000000000000000000000000000000000000000000000000000000080",
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa",
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85",
    )
)
_ED25519_P = 2**255 - 19


def weak_public_key(raw: bytes) -> bool:
    """True for small-order points and non-canonical encodings (y >= p)."""
    if raw in _SMALL_ORDER_KEYS:
        return True
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    return y >= _ED25519_P


def _b64(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


def verify_record(
    path: Path, trusted: list[bytes], *, require_known_key: bool = False
) -> VerifyResult:
    """Verify a record file (read-only). ``trusted`` holds raw 32-byte public keys.

    Always returns a verdict; malformed input is an invalid record, never a crash.
    """
    try:
        doc = _load_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return VerifyResult(False, f"cannot read record: {exc}")
    except RecursionError:
        return VerifyResult(False, "record is nested too deeply")
    except ValueError as exc:
        return VerifyResult(False, f"record is not valid JSON: {exc}")
    if not isinstance(doc, dict) or "payload" not in doc:
        return VerifyResult(False, "missing payload")
    sig = doc.get("signature")
    if not isinstance(sig, dict):
        return VerifyResult(False, "missing signature")
    if sig.get("alg") != "Ed25519":
        return VerifyResult(False, "unsupported signature algorithm")
    raw = _b64(sig.get("public_key"))
    value = _b64(sig.get("value"))
    if raw is None or len(raw) != 32:
        return VerifyResult(False, "public_key is not 32 bytes of base64")
    if value is None or len(value) != 64:
        return VerifyResult(False, "signature value is not 64 bytes of base64")
    if weak_public_key(raw):
        return VerifyResult(False, "public key is a small-order or non-canonical point")
    known = False
    if trusted:
        if raw not in trusted:
            return VerifyResult(False, "signer not trusted")
        known = True
    elif require_known_key:
        return VerifyResult(False, "no trusted key configured")
    if sig.get("key_id") != key_id(raw):
        return VerifyResult(False, "key_id does not match public_key")
    payload = doc["payload"]
    if not isinstance(payload, dict):
        return VerifyResult(False, "payload is not an object")
    try:
        canonical = canonical_bytes(payload)
    except SignError:
        return VerifyResult(False, "payload contains a floating-point number")
    except (ValueError, RecursionError):
        return VerifyResult(False, "payload cannot be canonicalised")
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(value, canonical)
    except (InvalidSignature, ValueError):
        return VerifyResult(False, "bad signature")
    try:
        RunRecord.model_validate(doc)
    except ValidationError:
        return VerifyResult(False, "payload does not match schema")
    problem = _consistency_problem(payload)
    if problem is not None:
        return VerifyResult(False, f"payload is internally inconsistent: {problem}")
    note = (
        None
        if known
        else "verified against the embedded public key: proves integrity, not identity"
    )
    return VerifyResult(
        True,
        reason=note,
        key_id=key_id(raw),
        run_id=str(payload.get("run_id")),
        signer="known" if known else "unknown",
    )
