#!/usr/bin/env python3
"""Build and verify a release version-approval plan from remote tags."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urlparse


SEMVER_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
NAMESPACE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
APPROVAL_RE = re.compile(r"^Version-Approval:\s*(sha256:[0-9a-f]{64})\s*$")


class PlanError(RuntimeError):
    """A version plan cannot be proven safe."""


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = SEMVER_RE.fullmatch(value)
        if match is None:
            raise PlanError(f"invalid SemVer version: {value!r}")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], check=False, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise PlanError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def normalize_repository(remote_url: str) -> str:
    value = remote_url.strip()
    if re.match(r"^[^/@:]+@[^/:]+:", value):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https", "ssh", "git"} or not parsed.path:
            raise PlanError(f"origin URL does not identify an owner/repository: {value!r}")
        path = parsed.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        raise PlanError(f"origin URL does not identify an owner/repository: {value!r}")
    return "/".join(parts).casefold()


def parse_targets(values: list[str]) -> dict[str, Version]:
    if not values:
        raise PlanError("the version plan is incomplete: at least one --target is required")
    targets: dict[str, Version] = {}
    for value in values:
        namespace, separator, version_text = value.partition("=")
        if not separator or NAMESPACE_RE.fullmatch(namespace) is None:
            raise PlanError(f"invalid target {value!r}; expected namespace=X.Y.Z")
        if namespace in targets:
            raise PlanError(f"duplicate version namespace: {namespace!r}")
        targets[namespace] = Version.parse(version_text)
    return targets


def remote_tag_names(remote: str) -> set[str]:
    output = git("ls-remote", "--tags", "--refs", remote)
    names: set[str] = set()
    for line in output.splitlines():
        fields = line.split("\t", 1)
        if len(fields) != 2 or not fields[1].startswith("refs/tags/"):
            raise PlanError(f"unexpected git ls-remote output: {line!r}")
        names.add(fields[1].removeprefix("refs/tags/"))
    return names


def build_plan(
    repository: str,
    targets: dict[str, Version],
    tag_names: set[str],
    excluded_tag_names: set[str] | None = None,
) -> tuple[dict[str, object], list[str]]:
    excluded_tag_names = set(excluded_tag_names or set())
    excluded_tag_names.update(
        f"{namespace}{target}" for namespace, target in targets.items()
    )
    versions: list[dict[str, str]] = []
    confirmation_namespaces: list[str] = []
    for namespace, target in sorted(targets.items()):
        candidates: list[Version] = []
        for tag_name in tag_names:
            if tag_name in excluded_tag_names or not tag_name.startswith(namespace):
                continue
            suffix = tag_name[len(namespace) :]
            if SEMVER_RE.fullmatch(suffix):
                candidates.append(Version.parse(suffix))
        if not candidates:
            raise PlanError(
                f"baseline for namespace {namespace!r} is unknown after excluding the current tag"
            )
        baseline = max(candidates)
        if target < baseline:
            raise PlanError(
                f"downgrade is not allowed for {namespace!r}: {baseline} -> {target}"
            )
        automatic_patch = target == Version(baseline.major, baseline.minor, baseline.patch + 1)
        unchanged = target == baseline
        if not automatic_patch and not unchanged:
            confirmation_namespaces.append(namespace)
        versions.append(
            {"namespace": namespace, "baseline": str(baseline), "target": str(target)}
        )
    return {"schema": 1, "repository": repository, "versions": versions}, confirmation_namespaces


def canonical_json(plan: dict[str, object]) -> str:
    return json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_digest(plan: dict[str, object]) -> str:
    payload = canonical_json(plan).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load_plan(args: argparse.Namespace) -> tuple[dict[str, object], list[str]]:
    expected_repository = normalize_repository(f"https://github.com/{args.repository}")
    actual_repository = normalize_repository(
        git("config", "--get", f"remote.{args.remote}.url")
    )
    if actual_repository != expected_repository:
        raise PlanError(
            f"origin repository mismatch: expected {expected_repository!r}, found {actual_repository!r}"
        )
    targets = parse_targets(args.target)
    excluded = set()
    if args.command == "verify" and args.tag_ref:
        excluded.add(args.tag_ref.removeprefix("refs/tags/"))
    return build_plan(
        expected_repository, targets, remote_tag_names(args.remote), excluded
    )


def tag_approvals(tag_ref: str) -> list[str]:
    if git("cat-file", "-t", tag_ref).strip() != "tag":
        raise PlanError(f"release tag must be annotated: {tag_ref}")
    message = git("for-each-ref", "--format=%(contents)", tag_ref)
    approvals: list[str] = []
    for line in message.splitlines():
        if not line.startswith("Version-Approval:"):
            continue
        match = APPROVAL_RE.fullmatch(line)
        if match is None:
            raise PlanError(f"malformed Version-Approval trailer: {line!r}")
        approvals.append(match.group(1))
    return approvals


def verify_tag_target(tag_ref: str, plan: dict[str, object]) -> None:
    tag_name = tag_ref.removeprefix("refs/tags/")
    planned_tags = {
        f"{item['namespace']}{item['target']}" for item in plan["versions"]  # type: ignore[index]
    }
    if tag_name not in planned_tags:
        raise PlanError(f"tag {tag_name!r} is absent from the current version plan")


def verify_approval(
    plan: dict[str, object], confirmation_namespaces: list[str], supplied: str | None
) -> None:
    expected = plan_digest(plan)
    if supplied is not None and supplied != expected:
        raise PlanError(f"version plan changed: expected {expected}, received {supplied}")
    if confirmation_namespaces and supplied is None:
        raise PlanError(
            "minor, major, or nonconsecutive version change requires "
            f"Version-Approval: {expected}"
        )


def print_plan(plan: dict[str, object], confirmation_namespaces: list[str]) -> None:
    for item in plan["versions"]:  # type: ignore[union-attr]
        print(f"{item['namespace']}: {item['baseline']} -> {item['target']}")
    print(f"Canonical-Version-Plan: {canonical_json(plan)}")
    print(f"Confirmed-Version-Plan: {plan_digest(plan)}")
    if confirmation_namespaces:
        print("Authorization: explicit confirmation required for " + ", ".join(confirmation_namespaces))
    else:
        print("Authorization: automatic (exact next patch only)")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("plan", "verify"))
    result.add_argument("--repository", required=True, help="expected owner/repository")
    result.add_argument("--remote", default="origin")
    result.add_argument(
        "--target", action="append", default=[], metavar="NAMESPACE=X.Y.Z",
        help="complete target version set; repeat for independent namespaces",
    )
    result.add_argument("--tag-ref", help="annotated release tag ref to verify")
    result.add_argument("--confirmed-version-plan", help="user-confirmed sha256 plan digest")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        plan, confirmation_namespaces = load_plan(args)
        print_plan(plan, confirmation_namespaces)
        if args.command == "plan":
            if args.tag_ref or args.confirmed_version_plan:
                raise PlanError("plan is read-only and does not accept execution confirmation options")
            return 0

        if not args.tag_ref:
            raise PlanError("verify requires --tag-ref")
        verify_tag_target(args.tag_ref, plan)
        approvals = tag_approvals(args.tag_ref)
        if len(approvals) > 1:
            raise PlanError("release tag contains more than one Version-Approval trailer")
        tag_approval = approvals[0] if approvals else None
        if args.confirmed_version_plan and tag_approval and args.confirmed_version_plan != tag_approval:
            raise PlanError("argument and tag Version-Approval digests disagree")
        supplied = args.confirmed_version_plan or tag_approval
        verify_approval(plan, confirmation_namespaces, supplied)
        print("Version authorization verified.")
        return 0
    except PlanError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
