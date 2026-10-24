# Analyzer version-selection use case

Primary source read on 2026-10-24:
[clangd issue 1088](https://github.com/clangd/clangd/issues/1088), opened
2022-03-30, including all four comments. The reporter describes diagnostics
after a multi-file rename. Maintainers explain disk-versus-dirty-header content
selection and note that source/header edit notifications can arrive in an
order that temporarily combines versions. No upstream code or logs were run.

This is a version-selection use case, not evidence of durable-store corruption
or the exact dependency-closed SQL counterexample. The paper's inference is
that an audit must specify the intended content endpoint; the experiment in
`experiments/closure_scenario.py` is a separate owned SQL construction. Tests
compare complete endpoint values rather than asserting closure alone. Selecting
one transaction/root and defining disk-versus-buffer provenance are distinct
obligations; this store does not implement clangd's content policy.

Only source attribution and a short paraphrase are retained. No upstream
implementation or attachment is redistributed.
