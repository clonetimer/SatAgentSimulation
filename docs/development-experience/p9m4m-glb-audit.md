# P9-M4-I GLB audit implementation lessons

Date: 2026-11-17

## Do not trust accessor metadata without reading the binary payload

A GLB can contain a valid JSON chunk whose POSITION accessor declares plausible min/max bounds while the embedded vertex bytes are malformed or inconsistent. A structural checker that only parses JSON and hashes the file can accept contradictory geometry metadata.

Correct pattern:
- Require the embedded BIN buffer for the synthetic geometry intake path.
- Validate bufferView byte ranges against the declared buffer length.
- Validate accessor element ranges using byteOffset, count, element size and byteStride.
- Check combined bufferView + accessor alignment, not accessor byteOffset alone.
- Decode the actual float32 POSITION records and compare their extrema to accessor min/max.
- Validate each index value against the POSITION accessor count.
- Reject sparse accessors and unsupported index layouts until their decoding semantics are implemented.
- Index accessors must not use vertex byteStride.
- Keep the GLB audit dependency-free for intake gating, but use Khronos glTF Validator as an independent standards-conformance layer when available.

## Test fixture quality matters

The original synthetic fixture described POSITION vertices but contained no actual BIN payload. Once the auditor began reading binary geometry, four tests failed before reaching their intended assertion. Do not weaken the production validator to preserve a metadata-only fixture. Make the fixture a valid GLB with a real BIN chunk and mutate one property at a time. Keep the SHA-256 receipt synchronized for structural-negative tests so the test reaches the intended validation layer.

## Scope boundary

A valid GLB proves file structure and internal consistency only. It does not prove real device identity, dimensions, mounting interfaces, optical boresight, rotor axis, or as-built installation. Those require source-backed evidence and engineering review.


## Windows CLI help must encode Chinese text explicitly

After fixing PYTHONPATH, the Windows 2022 job reached the CLI smoke check and failed because argparse printed the Chinese module docstring through a cp1252 console stream. This is a real Windows usability issue, not a test-only issue. Reconfigure stdout/stderr to UTF-8 before argparse emits help; keep the user-facing Chinese description rather than deleting it to satisfy CI.


A second Windows-only workflow failure occurred after the CLI passed: the benchmark-manifest step used a Bash heredoc but inherited the Windows runner's default PowerShell shell. Set the shell explicitly to bash on heredoc steps. Do not assume a multi-line run block selects Bash; on Windows it still defaults to PowerShell unless the shell is declared.
