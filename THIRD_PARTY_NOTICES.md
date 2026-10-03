# Third-party notices

## Meta Muse Gadget SDK

- Source: <https://github.com/facebookincubator/muse-gadget-sdk>
- Commit: `b1a3822995a51c0203cd1f3d72c1c656b8c3e620`
- Location: `muse-executor/vendor/muse-gadget-sdk/`
- Copyright: Meta Platforms, Inc. and affiliates.
- License: [Apache License 2.0](muse-executor/vendor/muse-gadget-sdk/LICENSE)

This repository vendors the upstream `linux/` tree plus its root `LICENSE` and `README.md`. Those 43 files were verified against the Git blob hashes in the upstream commit and are unmodified. Other upstream platforms and repository assets are not included; some upstream documentation links refer to those omitted files. Resolve them in the source repository above.

`executor_bridge.py` adapts the SDK at runtime for macOS. Its setup uses Python dependencies rather than the SDK's Linux/systemd installer.

Python dependencies retain their respective licenses, distributed with the installed packages. The upstream Apache license applies to the vendored SDK; it does not implicitly assign a license to independently authored files in this repository.
