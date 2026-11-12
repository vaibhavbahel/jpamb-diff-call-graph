# Method diffs and static call graphs

These commands read the JVM2JSON class files produced by `jpamb build` in
`target/decompiled`. They do not execute Java or run the analyser or fuzzer.
JSON output can be passed to the incremental analysis or saved with `--output`.

## Compare two compiled folders

Save a copy of `target/decompiled` before editing the Java methods. Rebuild the
edited code, then compare the saved folder with the new output:

```sh
jpamb diff path/to/before/decompiled target/decompiled
jpamb diff path/to/before/decompiled target/decompiled --output changes.json
```

The result separates added, removed and modified methods:

```json
{
  "added": [],
  "removed": [],
  "modified": ["example.Methods.helper:(I)I"]
}
```

Method IDs include the class, name, parameter types and return type, so overloads
remain distinct. A signature change appears as a removal and an addition.
The comparison checks bytecode, exception handlers and method access flags.
It ignores annotations (including `@Case`), line numbers and other debug or
compiler bookkeeping. It detects syntactic changes, not semantic equivalence.

## Compare revision methods in one class

For the project's `helperRev0`, `helperRev1`, `callerRev0`, `callerRev1` convention:

```sh
jpamb diff target/decompiled target/decompiled --before-revision 0 --after-revision 1
```

With a revision selected, only that revision's `nameRevN` methods and shared
methods without a revision suffix are loaded. The selected methods and calls to
them use the logical name without `RevN`. This means changing `helperRev0` to
`helperRev1` at an otherwise identical call site does not mark the caller as
modified. A call to a different revision, a missing revision, or colliding logical
method IDs is reported as an error. Without revision options, names stay literal.

## Build the call graph

```sh
jpamb call-graph target/decompiled --output call-graph.json
jpamb call-graph target/decompiled --revision 1 --output call-graph-rev1.json
```

The graph maps each method ID to its direct static callees:

```json
{
  "example.Methods.caller:(I)I": ["example.Methods.helper:(I)I"],
  "example.Methods.helper:(I)I": []
}
```

Edges come only from `invokestatic` instructions. Duplicate calls produce one
edge; recursion produces a self-edge. Methods without static calls remain in
the graph. Targets outside the loaded folders are retained as leaf nodes, whose
implementations are unknown. Virtual, interface and special calls, inherited
method resolution, and implicit class initialization are outside its scope.
The project assumes functional-style static methods without object-field edits.

The diff reports directly changed methods. Propagating changes to callers,
comparing analysis summaries and deciding which fuzzing results to reuse belong
to the incremental analysis layer. Keep both revisions' graphs when calls change
or methods are removed; the old graph still records dependencies lost in the new
version.

## Use from Python

```python
from pathlib import Path
from jpamb.revision import diff_methods, load_methods, static_call_graph

before = load_methods(Path("target/decompiled"), revision=0)
after = load_methods(Path("target/decompiled"), revision=1)
changes = diff_methods(before, after)
old_graph = static_call_graph(before)
new_graph = static_call_graph(after)
```

The functions use dictionaries and lists, and the command output uses sorted
method IDs and callee lists for repeatable JSON. No extra dependencies are needed.
