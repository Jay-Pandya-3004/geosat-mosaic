#!/usr/bin/env python3
"""Launches xcube's server with a bounded (LRU-evicting) dataset cache
instead of the library's own unbounded one -- see dataset_cache_patch.py
for the root cause this fixes.
Also patches the /datasets route to stop blocking the event loop -- see
async_datasets_patch.py.
"""

import sys


def main() -> None:
    """Entry point: applies the dataset-cache and async-/datasets-route
    monkeypatches, then hands off to xcube's own `serve` CLI command.

    Initializes xcube's plugin system explicitly (bypassing
    `xcube.cli.main`'s usual module-level registration, since this script
    imports `xcube.cli.serve` directly instead of going through
    `xcube.cli.main`) before invoking `serve.main()` with this process's
    own command-line arguments (`sys.argv[1:]`).
    """
    import async_datasets_patch
    import dataset_cache_patch

    dataset_cache_patch.apply()
    async_datasets_patch.apply()

    # xcube.cli.main's module-level _register_cli_commands() call is what
    # normally triggers xcube.util.plugin.init_plugins() (registers the
    # tornado/flask server-framework extensions) -- importing xcube.cli.serve
    # directly skips that, so call it explicitly first. (Same gotcha hit and
    # documented the first time a wrapper script was tried here, 2026-08-15 --
    # that earlier wrapper was reverted for a different reason, unrelated to
    # this fix.)
    from xcube.util.plugin import init_plugins

    init_plugins()

    from xcube.cli.serve import serve

    serve.main(sys.argv[1:], prog_name="xcube serve", standalone_mode=False)


if __name__ == "__main__":
    main()
