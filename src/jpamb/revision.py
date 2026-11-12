"""Method-level revision diffs and call graphs from JVM2JSON class files."""

import json
import re
from pathlib import Path

import jvm


def load_methods(folder: Path, revision: int | None = None) -> dict[str, dict]:
    """Load methods, optionally treating nameRevN as name for revision N.

    Keys are full JVM method IDs, including the class and descriptor. Values
    contain access flags, bytecode and exception handlers; debug information
    and annotations do not affect the diff.
    """
    files = sorted(Path(folder).rglob("*.json"))
    if not files:
        raise ValueError(f"No decompiled JSON class files found in {folder}")

    methods = {}
    names = {}
    excluded = set()
    found_revision = False
    for file in files:
        classfile = json.loads(file.read_text(encoding="utf-8"))
        classname = jvm.ClassName.from_slashed(classfile["name"])
        for method in classfile["methods"]:
            return_type = method["returns"]["type"]
            methodid = jvm.MethodID(
                name=method["name"],
                params=jvm.Parameters.from_json(method["params"], annotated=True),
                return_type=(
                    jvm.Type.from_json(return_type) if return_type is not None else None
                ),
            )
            original_id = jvm.AbsMethodID(classname, methodid).encode()
            name = methodid.name
            match = re.fullmatch(r"(.+)Rev(\d+)", name)
            if revision is not None and match:
                if int(match[2]) != revision:
                    excluded.add(original_id)
                    continue
                found_revision = True
                name = match[1]

            logical_id = jvm.AbsMethodID(
                classname, jvm.MethodID(name, methodid.params, methodid.return_type)
            ).encode()
            if logical_id in methods:
                raise ValueError(
                    f"Duplicate method after revision selection: {logical_id}"
                )
            names[original_id] = name
            code = method["code"]
            methods[logical_id] = {
                "access": sorted(method["access"]),
                "code": (
                    {
                        "bytecode": code["bytecode"],
                        "exceptions": code["exceptions"],
                    }
                    if code is not None
                    else None
                ),
            }

    if revision is not None and not found_revision:
        raise ValueError(f"No methods ending in Rev{revision} found in {folder}")

    # Normalize call targets too, so callerRev0 -> helperRev0 and
    # callerRev1 -> helperRev1 compare as the same call to helper.
    for method in methods.values():
        if method["code"] is None:
            continue
        for instruction in method["code"]["bytecode"]:
            if instruction["opr"] == "invoke" and instruction["access"] == "static":
                target = jvm.AbsMethodID.from_json(instruction["method"]).encode()
                if target in excluded:
                    raise ValueError(f"Call to a different revision: {target}")
                if target in names:
                    instruction["method"]["name"] = names[target]
    return methods


def diff_methods(
    before: dict[str, dict], after: dict[str, dict]
) -> dict[str, list[str]]:
    """Return added, removed and modified method IDs in a stable order."""
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "modified": sorted(
            method
            for method in before.keys() & after.keys()
            if before[method] != after[method]
        ),
    }


def static_call_graph(methods: dict[str, dict]) -> dict[str, list[str]]:
    """Map each caller to its direct invokestatic targets.

    Include methods without calls and external targets as nodes with no edges.
    Virtual, interface and special calls are outside this graph's scope.
    """
    graph = {method: set() for method in methods}
    for caller, method in methods.items():
        if method["code"] is None:
            continue
        for instruction in method["code"]["bytecode"]:
            if instruction["opr"] == "invoke" and instruction["access"] == "static":
                callee = jvm.AbsMethodID.from_json(instruction["method"]).encode()
                graph[caller].add(callee)
                graph.setdefault(callee, set())
    return {method: sorted(graph[method]) for method in sorted(graph)}
