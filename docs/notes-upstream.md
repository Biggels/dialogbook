# Upstream notes

A running record of how these libraries actually behave, versus what the docs or the
design handoff assumed. Verified by running code on **Windows 11 + Python 3.12.1**.

## Resolved at M0

### Kernel cwd follows the server process, not `root_dir`

The handoff's workspace decision says "Kernel cwd = that directory", assuming
`ServerApp.root_dir` sets it. **It does not.** With `root_dir` alone, kernels start in
whatever directory the app process was launched from.

Fix: spawn the `jupyter server` subprocess with `cwd=<workspace>`. Covered by
`tests/test_m0_kernel.py::test_kernel_cwd_is_the_workspace`.

Separately, the kernels API *does* honour a per-kernel `path` (relative to `root_dir`),
which gives a per-dialog cwd if nested workspace folders ever land. Verified in
`test_per_kernel_path_sets_cwd`.

### Spawn `python -m jupyter_server`, not `python -m jupyter server`

The `jupyter` dispatcher shells out through `jupyter-server.exe`, leaving an
eight-process chain between our `Popen` handle and the actual server. `python -m
jupyter_server` starts it directly. `taskkill /F /T` cleaned up either shape without
orphans, but the short chain makes the pid we hold mean something.

### `ipymini` is Apache-2.0

Its PyPI metadata declares no license, but `AnswerDotAI/ipymini` carries an Apache-2.0
`LICENSE` file. The risk noted in the handoff is closed. We still default to `ipykernel`;
the option is simply no longer blocked.

### `fasttransport` uses `httpx2`, not `httpx`

`jupyasyncclient` talks HTTP through `fasttransport.AsyncTransport`, which imports
`httpx2`. Plain `httpx` is not installed. Relevant if we ever need to share a client.

## Open

### `lisette` 0.1.36 is broken

Imports `toolslm.funccall`, which moved to `fastcore.funccall` in `toolslm` 0.3.48.
Avoided: the OpenAI SDK will be used directly. Not yet re-checked against a newer
`lisette`.
