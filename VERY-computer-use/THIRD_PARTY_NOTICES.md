# Third-party components and license status

This document is an inventory, not a license grant for this project's original code. No license has yet been designated for the original portions of this project. Do not infer a project-wide MIT license from an MIT-licensed dependency. Confirm authorship and rights for original/contributed code before publication.

## Included material

[`src/semantic_cu/upstream_contract.py`](src/semantic_cu/upstream_contract.py) contains a tools/list contract from [`@agent-sh/computer-use-linux`](https://github.com/agent-sh/computer-use-linux) 0.7.49 (source reference: commit `6b288a604283a500a2254dcbe1433491746a76f5`). Its existing full MIT notice and copyright (c) 2026 Avi Fenesh are preserved in that source. The same notice is also provided in [LICENSES/computer-use-linux-MIT.txt](LICENSES/computer-use-linux-MIT.txt). Keep the copyright and permission notice with redistributed copies or substantial portions of the covered material. The imported material is the advertised tool-schema contract; this attribution does not claim that all validation logic or all project code was copied from upstream.

## Runtime/build dependencies (not vendored here)

The following were inspected in the development environment. Versions are evidence of inspection, not a lockfile or guarantee about all versions allowed by pyproject.toml.

| Component | Inspected version | Observed license |
|---|---|---|
| @agent-sh/computer-use-linux | 0.7.49 | MIT |
| Pillow | 13.0.0 | MIT-CMU |
| NumPy | 2.4.6 | Metadata expression: BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| opencv-python-headless (optional) | 5.0.0.93 | Apache-2.0; additional third-party notices shipped with wheel |
| dbus-next (optional) | 0.2.3 | MIT |
| setuptools (build) | 84.2.1 | MIT |

Wheel distributions may bundle native libraries under additional licenses. If distributing an environment, binary, wheel bundle or container, inspect the actual artifacts and preserve their notices; this table is not sufficient for that distribution.

System components such as Xorg/X11 libraries, GTK/GI, GStreamer, AT-SPI, TigerVNC, Fcitx5 and DBus have their own licenses, including GPL/LGPL terms for some components. They are not bundled by this source tree. A branded VS Code executable is an optional local dependency, not a redistributable artifact supplied by this project. Do not package it merely because Code-OSS has a different license.

No comprehensive provenance audit has established that every source file is original. Public release requires review of authorship, project-license selection, and applicable third-party obligations.

## Attribution scope

The dependency inventory does not assert that dependency source is vendored. Public protocol documentation and a software dependency are not, by themselves, evidence that a specific algorithm or source file was copied. No specific research-paper inspiration is asserted without a verifiable source. Existing notices for included material are preserved; future imported code must carry its own source/version and applicable notices.
