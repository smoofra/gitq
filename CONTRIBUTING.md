# Contributing to gitq

## Development setup

gitq is managed with [uv](https://docs.astral.sh/uv/). Python 3.12 or newer is
required.

```sh
uv sync --all-groups
```

Run the tools through `uv run` (the `Makefile` already does this), or activate
`.venv` if you prefer.

## Checks

| command         | what it does                                        |
| --------------- | --------------------------------------------------- |
| `make check`    | everything below: flake8, black --check, mypy, tests |
| `make lint`     | flake8, black --check, mypy                          |
| `make test`     | `pytest -n auto`                                     |
| `make black`    | reformat `src` and `test` in place                   |
| `make coverage` | run tests under coverage and open the HTML report    |

Line length is 99 everywhere.

The tests shell out to real `git`, so a `user.name` / `user.email` must be
configured in your git config.

CI (`.github/workflows/ci.yml`) runs `make lint` and `make test` on every push
to `master` and on every pull request. Don't push a release that CI would
reject.

## Releasing

Releases are published to [PyPI](https://pypi.org/project/gitq/) by hand from a
maintainer's machine — there is no release workflow in CI. The released commit
is marked afterwards with a lightweight `vX.Y.Z` tag.

### One-time setup: PyPI credentials

`pyproject.toml` sets

```toml
[tool.uv]
check-url = "https://pypi.org/simple/"
keyring-provider = "subprocess"
```

`keyring-provider = "subprocess"` means uv shells out to the `keyring` command
line tool for the upload password, so that tool has to be on `PATH` and has to
hold a PyPI API token:

```sh
uv tool install keyring          # once, puts `keyring` on PATH via ~/.local/bin
keyring set https://upload.pypi.org/legacy/ __token__
# paste a PyPI API token (starts with "pypi-") at the prompt
```

On macOS keyring stores this in the login Keychain.

`check-url` makes `uv publish` ask PyPI which files already exist and skip
them, so a re-run after a partial upload is safe.

If you'd rather not use keyring, pass the token directly instead — `uv publish
--token pypi-...` or `UV_PUBLISH_TOKEN=pypi-... uv publish` — and skip the
`--username` flag in step 5 below.

### Release checklist

1. Be on `master`, up to date with `origin/master`, with a clean working tree.

2. Make sure everything passes:

   ```sh
   make check
   ```

3. Bump `version` in `pyproject.toml` and commit it on its own:

   ```sh
   git commit -am 'bump version to 0.0.11'
   git push origin master
   ```

   Versions are plain `MAJOR.MINOR.PATCH`; the project has been walking the
   patch number. There is no other place the version is written down — the
   console scripts and the package read nothing from a `__version__`.

4. Build the artifacts. Clear `dist/` first, or stale wheels from earlier
   versions get picked up by the publish step:

   ```sh
   rm -rf dist
   uv build
   ```

   This produces `dist/gitq-X.Y.Z.tar.gz` and
   `dist/gitq-X.Y.Z-py3-none-any.whl`. `dist/` is gitignored.

5. Upload:

   ```sh
   uv publish --dry-run --username __token__   # optional: checks the files
   uv publish --username __token__
   ```

   (Drop `--username __token__` if you are passing `--token` / setting
   `UV_PUBLISH_TOKEN`.)

6. Tag the exact commit you built and published, and push the tag:

   ```sh
   git tag v0.0.11
   git push origin v0.0.11
   ```

   Tags are lightweight and always named `v` + the version in
   `pyproject.toml` at that commit. Keep the tag on the commit that was
   actually built: if commits landed on `master` between the version bump and
   the upload, build and tag the same commit rather than tagging the tip.

7. Sanity check the published package from somewhere outside the source tree:

   ```sh
   uvx --from gitq==0.0.11 git-queue --help
   ```

   and confirm the new version shows up at
   <https://pypi.org/project/gitq/>.
