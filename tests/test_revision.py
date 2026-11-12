import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli import cli
from jpamb.revision import diff_methods, load_methods, static_call_graph


def method(name, bytecode=None, params=(), returns=None):
    """A small JVM2JSON method, with the same shape as target/decompiled."""
    return {
        "name": name,
        "access": ["public", "static"],
        "params": [{"type": param, "annotations": []} for param in params],
        "returns": {"type": returns, "annotations": []},
        "annotations": [],
        "code": {
            "bytecode": bytecode if bytecode is not None else [],
            "exceptions": [],
            "lines": [{"line": 1, "offset": 0}],
            "max_locals": len(params),
            "max_stack": 1,
            "stack_map": None,
        },
    }


def invoke(name, access="static", classname="example/Methods", args=(), returns=None):
    return {
        "opr": "invoke",
        "offset": 0,
        "access": access,
        "method": {
            "ref": {"kind": "class", "name": classname},
            "name": name,
            "args": list(args),
            "returns": returns,
            "is_interface": False,
        },
    }


def write_class(folder, methods, classname="example/Methods"):
    file = folder / f"{classname}.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps({"name": classname, "methods": methods}), encoding="utf-8"
    )
    return folder


def test_diff_added_removed_modified_and_unchanged(tmp_path):
    before = write_class(
        tmp_path / "before",
        [
            method("unchanged"),
            method("modified"),
            method("removed"),
        ],
    )
    after = write_class(
        tmp_path / "after",
        [
            method("unchanged"),
            method("modified", [{"offset": 0, "opr": "return", "type": None}]),
            method("added"),
        ],
    )

    assert diff_methods(load_methods(before), load_methods(after)) == {
        "added": ["example.Methods.added:()V"],
        "removed": ["example.Methods.removed:()V"],
        "modified": ["example.Methods.modified:()V"],
    }


def test_debug_information_and_annotations_do_not_change_methods(tmp_path):
    original = method("same", params=("int",), returns="int")
    edited = copy.deepcopy(original)
    edited["access"].reverse()
    edited["annotations"] = [{"type": "jpamb/utils/Case"}]
    edited["params"][0]["annotations"] = [{"type": "example/Annotation"}]
    edited["code"]["lines"] = [{"line": 100, "offset": 0}]
    edited["code"]["stack_map"] = [{"offset": 0}]
    before = write_class(tmp_path / "before", [original])
    after = write_class(tmp_path / "after", [edited])

    assert diff_methods(load_methods(before), load_methods(after)) == {
        "added": [],
        "removed": [],
        "modified": [],
    }


@pytest.mark.parametrize("change", ["access", "handler"])
def test_access_and_exception_handler_changes_are_detected(tmp_path, change):
    original = method("edited")
    edited = copy.deepcopy(original)
    if change == "access":
        edited["access"].append("synchronized")
    else:
        edited["code"]["exceptions"] = [
            {"start": 0, "end": 1, "handler": 2, "type": "java/lang/Exception"},
        ]
    before = write_class(tmp_path / "before", [original])
    after = write_class(tmp_path / "after", [edited])
    assert diff_methods(load_methods(before), load_methods(after))["modified"] == [
        "example.Methods.edited:()V",
    ]


def test_full_ids_distinguish_overloads_classes_and_return_types(tmp_path):
    write_class(
        tmp_path,
        [
            method("helper"),
            method("helper", params=("int",), returns="int"),
            method("helper", params=("int",), returns={"kind": "array", "type": "int"}),
        ],
    )
    write_class(tmp_path, [method("helper")], "example/Other")

    assert set(load_methods(tmp_path)) == {
        "example.Methods.helper:()V",
        "example.Methods.helper:(I)I",
        "example.Methods.helper:(I)[I",
        "example.Other.helper:()V",
    }


def test_call_graph_direction_recursion_duplicates_and_external_targets(tmp_path):
    folder = write_class(
        tmp_path,
        [
            method(
                "caller",
                [
                    invoke("helper"),
                    invoke("helper"),
                    invoke("library", classname="external/Lib"),
                ],
            ),
            method("helper", [invoke("helper")]),
            method("alone"),
        ],
    )

    assert static_call_graph(load_methods(folder)) == {
        "example.Methods.alone:()V": [],
        "example.Methods.caller:()V": [
            "example.Methods.helper:()V",
            "external.Lib.library:()V",
        ],
        "example.Methods.helper:()V": ["example.Methods.helper:()V"],
        "external.Lib.library:()V": [],
    }


def test_call_graph_includes_only_static_invocations(tmp_path):
    folder = write_class(
        tmp_path,
        [
            method(
                "caller",
                [
                    invoke("staticTarget", args=("int",), returns="int"),
                    invoke("virtualTarget", "virtual"),
                    invoke("interfaceTarget", "interface"),
                    invoke("<init>", "special"),
                ],
            )
        ],
    )

    assert static_call_graph(load_methods(folder)) == {
        "example.Methods.caller:()V": ["example.Methods.staticTarget:(I)I"],
        "example.Methods.staticTarget:(I)I": [],
    }


def test_methods_without_code_are_supported(tmp_path):
    native = method("nativeMethod")
    native["access"].append("native")
    native["code"] = None
    methods = load_methods(write_class(tmp_path, [native]))
    assert static_call_graph(methods) == {"example.Methods.nativeMethod:()V": []}


def test_revision_names_and_call_targets_are_normalized(tmp_path):
    folder = write_class(
        tmp_path,
        [
            method("helperRev0"),
            method("helperRev1", [{"opr": "return", "offset": 0, "type": None}]),
            method("callerRev0", [invoke("helperRev0"), invoke("shared")]),
            method("callerRev1", [invoke("helperRev1"), invoke("shared")]),
            method("shared"),
        ],
    )
    before = load_methods(folder, revision=0)
    after = load_methods(folder, revision=1)

    assert diff_methods(before, after) == {
        "added": [],
        "removed": [],
        "modified": ["example.Methods.helper:()V"],
    }
    assert static_call_graph(after) == {
        "example.Methods.caller:()V": [
            "example.Methods.helper:()V",
            "example.Methods.shared:()V",
        ],
        "example.Methods.helper:()V": [],
        "example.Methods.shared:()V": [],
    }


def test_cross_class_revision_call_targets_are_normalized(tmp_path):
    write_class(
        tmp_path,
        [method("callerRev1", [invoke("helperRev1", classname="example/Other")])],
    )
    write_class(tmp_path, [method("helperRev1")], "example/Other")
    graph = static_call_graph(load_methods(tmp_path, revision=1))
    assert graph["example.Methods.caller:()V"] == ["example.Other.helper:()V"]


def test_wrong_revision_call_is_rejected(tmp_path):
    folder = write_class(
        tmp_path,
        [
            method("helperRev0"),
            method("helperRev1"),
            method("callerRev1", [invoke("helperRev0")]),
        ],
    )
    with pytest.raises(ValueError, match="Call to a different revision"):
        load_methods(folder, revision=1)


def test_missing_revision_and_name_collisions_are_rejected(tmp_path):
    folder = write_class(tmp_path, [method("helperRev0"), method("helper")])
    with pytest.raises(ValueError, match="No methods ending in Rev1"):
        load_methods(folder, revision=1)
    with pytest.raises(ValueError, match="Duplicate method"):
        load_methods(folder, revision=0)


def test_cli_diff_and_saved_graph(tmp_path):
    folder = write_class(
        tmp_path / "input",
        [
            method("helperRev0"),
            method("helperRev1", [invoke("external", classname="example/Lib")]),
        ],
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "diff",
            str(folder),
            str(folder),
            "--before-revision",
            "0",
            "--after-revision",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "added": [],
        "removed": [],
        "modified": ["example.Methods.helper:()V"],
    }

    output = tmp_path / "graph.json"
    result = runner.invoke(
        cli,
        [
            "call-graph",
            str(folder),
            "--revision",
            "1",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output == ""
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "example.Lib.external:()V": [],
        "example.Methods.helper:()V": ["example.Lib.external:()V"],
    }


@pytest.mark.parametrize("command", ["diff", "call-graph"])
def test_cli_rejects_empty_input(tmp_path, command):
    args = [command, str(tmp_path)]
    if command == "diff":
        args.append(str(tmp_path))
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1
    assert "No decompiled JSON class files" in result.output


def test_existing_jpamb_call_graph():
    folder = Path(__file__).resolve().parents[1] / "target" / "decompiled"
    graph = static_call_graph(load_methods(folder))
    assert graph["jpamb.cases.Calls.callsAssertIf:(Z)V"] == [
        "jpamb.cases.Calls.assertIf:(Z)V",
    ]
    assert graph["jpamb.cases.Calls.fib:(I)I"] == ["jpamb.cases.Calls.fib:(I)I"]
