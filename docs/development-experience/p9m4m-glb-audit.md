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


## Distinguish a listed CAD download from acquired CAD evidence

A supplier marketplace page may list a CAD model and file size without exposing the actual file bytes or reuse terms to an automated fetch. Record the state as "CAD listed, bytes/license not verified", not "CAD acquired" and not "no CAD exists". Attempt the source acquisition and SHA-256/license check first; only fall back to image-to-3D if access, permitted reuse or target-model identity prevents use of the real CAD.
## 2026-11-19: supplier access, license scope and local inference budget

- KAIROSPACE KSST-01's 2.88 MB CAD entry on satsearch is not a direct anonymous download; the visible page routes CAD requests through a supplier inquiry. The official datasheet has an all-rights-reserved notice and the product page restricts reuse without prior written permission. Do not scrape its drawing/photo into a generation pipeline or claim the CAD was acquired.
- Public product dimensions are not the same thing as an authorized reusable geometry source. Record them as third-party product facts and identity leads only; do not silently populate the project's target device schema.
- An open-source star-tracker repository may contain software or KiCad PCB/ECAD but no mechanical solid. Check the repository tree for STEP/STL/FCStd/IGES/GLB and an explicit license before calling it a CAD alternative.
- TRELLIS.2's official 24 GB VRAM/Linux requirement is a poor default for a local RTX 5060. TripoSR is a more practical first G1 candidate: official MIT license, roughly 6 GB VRAM for one image, and native GLB output. Its output remains an appearance-only single-view reconstruction; it cannot certify hidden surfaces, boresight, mounting holes or dimensions.
- Stable Fast 3D has similar VRAM needs but gated weights and Stability AI Community License obligations; Hunyuan3D-2.1 requires more VRAM and a separate territory/distribution license review. Keep both optional rather than adding them to the core simulator.
- Do not run inference until the input image's original bytes, SHA-256, provenance locator and explicit permission for internal model generation/display are recorded. A technically feasible model is not a legally admissible input.
