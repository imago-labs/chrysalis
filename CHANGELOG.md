# Chrysalis Changelog

## v0.1.0 PyPI publish, 2026-05-13

First publish of the open Chrysalis packages to the Python Package Index.
Users can now install any of the four distributions directly from PyPI
without cloning the source repo.

Published packages:

| PyPI name | Version | Source path |
| --- | --- | --- |
| chrysalis-kernel | 0.4.0 | packages/memoir-kernel |
| chrysalis-interfaces | 0.1.0 | packages/chrysalis-interfaces |
| chrysalis-sdk | 0.1.0 | packages/chrysalis-sdk |
| chrysalis-receipts | 0.1.0 | packages/chrysalis-receipts |

Repository layout changes:

- The root `pyproject.toml` (chrysalis-kernel) ships only the
  `memoir-kernel` content. The other three packages publish as standalone
  PyPI distributions from their own `pyproject.toml` files under
  `packages/<name>/`.
- Each sub-package's `pyproject.toml` now carries Crystal Tubbs as the
  author, a per-package `Homepage` URL pointing at the subdirectory on
  GitHub, and an explicit `setuptools.packages.find` include.

How to install from PyPI:

```
pip install chrysalis-kernel chrysalis-interfaces chrysalis-sdk chrysalis-receipts
```

Make it a great day.
