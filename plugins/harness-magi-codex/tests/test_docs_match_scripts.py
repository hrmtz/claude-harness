#!/usr/bin/env python3
"""Doc-vs-code contract test for harness-magi-codex.

The deterministic half of the anti-doc-drift design (docs/designs/ANTI_DOC_DRIFT.md).
It checks PUBLIC CONTRACTS the scripts actually implement — exit codes, env vars, and the
plateau gate's assert set — against what the docs promise. A plugin whose docs claim an
exit code the script never returns is a contradiction a machine can catch.

This plugin exists because a cross-family review found the design specifying two mutually
exclusive lock protocols at once. That class of defect is what this test blocks.

Discipline (inherited from harness-formation/tests/test_docs_match_dispatch.py):
- If a contract cannot be parsed out of the source at all, RAISE. A refactor that hides
  the contract is checker-blindness, not doc-drift, and must not be reported as PASS.
- "Documented" is matched loosely enough that a doc style change does not false-fail.

Run: python3 plugins/harness-magi-codex/tests/test_docs_match_scripts.py
Exit 0 = contracts agree · 1 = drift · raises = checker cannot see the contract.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..")
ADAPTER = os.path.join(PLUGIN, "scripts", "magi_xfamily.sh")
FANOUT = os.path.join(PLUGIN, "scripts", "magi_fanout_codex.sh")
GATE = os.path.join(PLUGIN, "scripts", "magi_plateau_gate.sh")
VERIFIER = os.path.join(PLUGIN, "scripts", "magi_verify_round.py")
GUARD = os.path.join(PLUGIN, "scripts", "magi_campaign_guard.py")
PREFLIGHT = os.path.join(PLUGIN, "scripts", "magi_preflight_codex.sh")
README = os.path.join(PLUGIN, "README.md")
DESIGN = os.path.join(PLUGIN, "..", "..", "docs", "designs", "CODEX_MAGI_MIRROR.md")
DUAL_SKILL = os.path.join(PLUGIN, "skills", "dual-magi-review", "SKILL.md")
ULTRA_SKILL = os.path.join(PLUGIN, "skills", "ultramagi", "SKILL.md")
MAGI_SKILL = os.path.join(PLUGIN, "skills", "magi", "SKILL.md")


def read(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def adapter_exit_codes(src: str) -> set[str]:
    """Literal `exit N` in the adapter. Excludes 0 (success) and 64 (usage)."""
    codes = set(re.findall(r"^\s*exit (\d+)", src, re.M))
    codes |= set(re.findall(r"\bexit (\d+)\b", src))
    meaningful = {c for c in codes if c not in {"0", "64", "130", "143"}}
    if not meaningful:
        raise RuntimeError(f"cannot parse any exit codes from {ADAPTER} — checker is blind")
    return meaningful


def gate_ownership(src: str, path: str) -> set[str]:
    """Parse one stable, literal MAGI_GATE_OWNERSHIP declaration."""
    declarations = re.findall(r"^MAGI_GATE_OWNERSHIP\s*=\s*(.+)$", src, re.M)
    if len(declarations) != 1:
        raise RuntimeError(
            f"expected one MAGI_GATE_OWNERSHIP declaration in {path}, "
            f"found {len(declarations)} — checker is blind"
        )
    try:
        declared = ast.literal_eval(declarations[0])
    except (SyntaxError, ValueError) as exc:
        raise RuntimeError(
            f"cannot parse MAGI_GATE_OWNERSHIP in {path} — checker is blind"
        ) from exc
    if (
        not isinstance(declared, tuple)
        or not declared
        or any(not isinstance(tag, str) or not re.fullmatch(r"G[1-9]", tag) for tag in declared)
        or len(set(declared)) != len(declared)
    ):
        raise RuntimeError(
            f"invalid MAGI_GATE_OWNERSHIP in {path}: {declared!r} — checker is blind"
        )
    return set(declared)


def literal_gate_tag(expression: ast.expr) -> str | None:
    """Return a leading G-tag from a literal string or f-string expression."""
    prefix: str | None = None
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        prefix = expression.value
    elif (
        isinstance(expression, ast.JoinedStr)
        and expression.values
        and isinstance(expression.values[0], ast.Constant)
        and isinstance(expression.values[0].value, str)
    ):
        prefix = expression.values[0].value
    if prefix is None:
        return None
    match = re.match(r"^(G[1-9]):", prefix)
    return match.group(1) if match else None


def verifier_gate_implementation(src: str) -> set[str]:
    """AST-walk literal fail("Gx", ...) calls in the shared verifier."""
    try:
        tree = ast.parse(src, filename=VERIFIER)
    except SyntaxError as exc:
        raise RuntimeError(f"cannot parse {VERIFIER} — checker is blind") from exc
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "fail"
    ]
    tags: set[str] = set()
    for call in calls:
        if not call.args or not isinstance(call.args[0], ast.Constant):
            raise RuntimeError(
                f"{VERIFIER} has a non-literal fail() gate argument — checker is blind"
            )
        tag = call.args[0].value
        if not isinstance(tag, str) or not re.fullmatch(r"G[1-9]", tag):
            raise RuntimeError(
                f"{VERIFIER} has invalid fail() gate argument {tag!r} — checker is blind"
            )
        tags.add(tag)
    if not tags:
        raise RuntimeError(f"cannot observe verifier gate failures in {VERIFIER} — checker is blind")
    return tags


def wrapper_python(src: str) -> ast.Module:
    """Extract and parse the plateau wrapper's single embedded Python program."""
    programs = re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", src, re.S)
    if len(programs) != 1:
        raise RuntimeError(
            f"expected one parseable Python heredoc in {GATE}, found {len(programs)} "
            "— checker is blind"
        )
    try:
        return ast.parse(programs[0], filename=GATE)
    except SyntaxError as exc:
        raise RuntimeError(f"cannot parse embedded Python in {GATE} — checker is blind") from exc


def wrapper_gate_implementation(src: str) -> set[str]:
    """Observe literal G-tagged failures appended by the plateau-only wrapper."""
    tags: set[str] = set()
    for node in ast.walk(wrapper_python(src)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "fails"
            and node.args
        ):
            tag = literal_gate_tag(node.args[0])
            if tag is not None:
                tags.add(tag)
    if not tags:
        raise RuntimeError(f"cannot observe wrapper gate failures in {GATE} — checker is blind")
    return tags


def gate_asserts(wrapper_src: str, verifier_src: str) -> set[str]:
    """Bind declared ownership to implementation, exact split, and G1..G9 union."""
    shared = gate_ownership(verifier_src, VERIFIER)
    wrapper = gate_ownership(wrapper_src, GATE)
    implemented_shared = verifier_gate_implementation(verifier_src)
    implemented_wrapper = wrapper_gate_implementation(wrapper_src)
    expected_shared = {"G1", "G2", "G3", "G4", "G5", "G6", "G9"}
    expected_wrapper = {"G7", "G8"}
    expected_union = {f"G{number}" for number in range(1, 10)}
    if shared != implemented_shared:
        raise RuntimeError(
            f"{VERIFIER} declares {sorted(shared)} but implements "
            f"{sorted(implemented_shared)} — checker is blind"
        )
    if wrapper != implemented_wrapper:
        raise RuntimeError(
            f"{GATE} declares {sorted(wrapper)} but implements "
            f"{sorted(implemented_wrapper)} — checker is blind"
        )
    if shared != expected_shared:
        raise RuntimeError(
            f"{VERIFIER} owns {sorted(shared)}, expected {sorted(expected_shared)} "
            "— checker is blind"
        )
    if wrapper != expected_wrapper:
        raise RuntimeError(
            f"{GATE} owns {sorted(wrapper)}, expected {sorted(expected_wrapper)} "
            "— checker is blind"
        )
    implemented = shared | wrapper
    if shared & wrapper or implemented != expected_union:
        raise RuntimeError(
            f"plateau ownership overlap/union invalid: shared={sorted(shared)}, "
            f"wrapper={sorted(wrapper)} — checker is blind"
        )
    return implemented


def gate_ownership_mutation_probes(wrapper_src: str, verifier_src: str) -> None:
    """Prove declarations alone cannot conceal removed or undeclared gate code."""
    tree = ast.parse(verifier_src, filename=VERIFIER)

    class RemoveImplementedG3(ast.NodeTransformer):
        removed = 0

        def visit_Call(self, node: ast.Call) -> ast.AST:
            self.generic_visit(node)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "fail"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "G3"
            ):
                node.func = ast.Name(id="removed_gate", ctx=ast.Load())
                self.removed += 1
            return node

    remover = RemoveImplementedG3()
    missing_tree = remover.visit(tree)
    ast.fix_missing_locations(missing_tree)
    if remover.removed != 1:
        raise RuntimeError(
            f"cannot construct missing-gate mutation for {VERIFIER} — checker is blind"
        )
    missing_implementation = ast.unparse(missing_tree)

    added_tree = ast.parse(verifier_src, filename=VERIFIER)
    added_tree.body.append(
        ast.Expr(
            value=ast.Call(
                func=ast.Name(id="fail", ctx=ast.Load()),
                args=[
                    ast.Constant(value="G8"),
                    ast.Constant(value="ownership mutation probe"),
                ],
                keywords=[],
            )
        )
    )
    ast.fix_missing_locations(added_tree)
    mutations = {
        "implemented G3 removed with declaration retained": missing_implementation,
        "undeclared implemented G8 added": ast.unparse(added_tree),
    }
    for label, mutated_verifier in mutations.items():
        try:
            gate_asserts(wrapper_src, mutated_verifier)
        except RuntimeError:
            continue
        raise RuntimeError(f"ownership mutation passed unexpectedly: {label}")


def env_vars(src: str) -> set[str]:
    names = set(re.findall(r"\$\{(MAGI_[A-Z_]+):-", src))
    if not names:
        raise RuntimeError(f"cannot parse any MAGI_* env vars from {ADAPTER} — checker is blind")
    return names


def guard_exit_codes(src: str) -> set[str]:
    codes = set(re.findall(r"^\s*return (\d+)$", src, re.M))
    meaningful = {code for code in codes if code != "0"}
    if not meaningful:
        raise RuntimeError(f"cannot parse campaign guard exit codes from {GUARD} — checker is blind")
    return meaningful


def guard_env_vars(src: str) -> set[str]:
    names = set(re.findall(r'os\.environ\.get\(\s*"(MAGI_[A-Z_]+)"', src))
    if not names:
        raise RuntimeError(f"cannot parse campaign guard env vars from {GUARD} — checker is blind")
    return names


def preflight_exit_codes(src: str) -> set[str]:
    codes = set(re.findall(r"\bexit (\d+)\b", src))
    if not {"1", "2", "3", "5", "64", "130", "143"}.issubset(codes):
        raise RuntimeError(f"cannot observe the preflight exit contract in {PREFLIGHT}")
    return codes


def preflight_env_vars(src: str) -> set[str]:
    names = set(re.findall(r"\$\{(MAGI_PREFLIGHT_[A-Z_]+):-", src))
    if not names:
        raise RuntimeError(f"cannot parse preflight env vars from {PREFLIGHT}")
    return names


def main() -> int:
    adapter, fanout, gate, verifier, guard, preflight = (
        read(ADAPTER),
        read(FANOUT),
        read(GATE),
        read(VERIFIER),
        read(GUARD),
        read(PREFLIGHT),
    )
    skill_paths = (DUAL_SKILL, ULTRA_SKILL, MAGI_SKILL)
    if not all(os.path.isfile(path) for path in skill_paths):
        raise RuntimeError("cannot read every shipped SKILL.md — checker is blind")
    docs = read(README) + read(DESIGN) + "".join(read(path) for path in skill_paths)
    preflight_docs = read(README) + read(MAGI_SKILL)
    drift = []

    for code in sorted(adapter_exit_codes(adapter)):
        # The doc must explain what this exit code means, e.g. "exit 3" / "3 = lock" / "exit 2".
        if not re.search(rf"\b{code}\s*=|\bexit(?:s)? {code}\b|`{code}`", docs):
            drift.append(f"adapter can `exit {code}` but no doc explains it")

    for code in sorted(guard_exit_codes(guard)):
        if not re.search(rf"\b{code}\s*=|\bexit(?:s)? {code}\b|`{code}`", docs):
            drift.append(f"campaign guard can exit {code} but no doc explains it")

    implemented = gate_asserts(gate, verifier)
    gate_ownership_mutation_probes(gate, verifier)
    for tag in sorted(implemented):
        if tag not in docs:
            drift.append(f"plateau gate asserts {tag} but no doc mentions it")

    # Conversely: a doc promising a G-assert the gate never makes is equally a contradiction.
    documented = set(re.findall(r"\bG[1-9]\b", docs))
    for tag in sorted(documented - implemented):
        drift.append(f"docs promise assert {tag} but the gate never checks it")

    for var in sorted(env_vars(adapter) | env_vars(fanout) | guard_env_vars(guard)):
        if var not in docs:
            drift.append(f"adapter reads ${var} but no doc mentions it")

    for code in sorted(preflight_exit_codes(preflight)):
        if not re.search(rf"\b{code}\s*=|\bexit(?:s)? `{code}`|\bexit(?:s)? {code}\b|`{code}`", preflight_docs):
            drift.append(f"preflight can `exit {code}` but no preflight doc explains it")

    for var in sorted(preflight_env_vars(preflight)):
        if var not in preflight_docs:
            drift.append(f"preflight reads ${var} but no preflight doc mentions it")

    for prompt_name, prompt in (("fanout", fanout), ("cross-family", adapter)):
        for field in ("root_cause_id", "subsystem"):
            if field not in prompt:
                drift.append(f"{prompt_name} prompt omits blocking convergence field {field}")

    # The design's central honesty claim must not be silently dropped from the README.
    if "forgery" in read(README).lower() and "not" not in read(README).lower():
        drift.append("README uses 'forgery' without disclaiming it")

    if drift:
        print("DRIFT:", *drift, sep="\n  - ", file=sys.stderr)
        return 1
    print(f"PASS: adapter/guard exit codes, {len(implemented)} G-asserts, and env vars documented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
