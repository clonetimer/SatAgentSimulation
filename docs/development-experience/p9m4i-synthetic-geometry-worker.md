# P9-M4-I synthetic geometry worker — development experience

Date: 2026-11-19

## CI failure analysis and correction

The first rebased PR #161 run on head `e80cda9ee40c5b71b0efe8f48bfe905fcd2f07c4` failed identically on Ubuntu 24.04 and Windows 2022. The failure was in the test fixture and cross-platform CLI invocation, not in an executed TripoSR inference:

1. The fake worker emitted a planar triangle with a zero Z extent. The production GLB auditor correctly rejects any zero/non-finite axis extent. Weakening the auditor would have removed a useful fail-closed geometry gate.
2. The schema test created a temporary Git checkout and monkeypatched the TripoSR revision/license pins, then reused those temporary values against the committed production JSON Schema. The schema correctly required the pinned production revision, so the fixture and schema were testing different identities.
3. The Windows runner used a legacy code page for bilingual argparse help output, causing `UnicodeEncodeError` after the Python tests.

Corrections:

- The normal GLB fixture and fake worker now emit two non-coplanar triangles (six vertices) with positive X/Y/Z bounds. The production zero-extent check is unchanged.
- Added an explicit regression test proving a planar GLB is still rejected with `P9M4I_GLB_ZERO_OR_INVALID_EXTENT`.
- The schema test snapshots the production TripoSR revision and LICENSE blob SHA before the fixture monkeypatch, then asserts the JSON Schema constants match those pins. The schema is not weakened or dynamically rewritten.
- The workflow sets `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` for the bilingual CLI help check on both operating systems.

## Engineering lessons

- A GLB can be syntactically valid and still be unsuitable for a 3D engineering preview because one axis has zero extent. Keep the geometric extent gate and repair fixtures to model actual three-dimensional output.
- Test fixtures that patch immutable model pins must not leak those temporary values into checks of committed production schemas. Snapshot the production pins before patching.
- Cross-platform CLI tests should explicitly set UTF-8 when help/diagnostic text contains non-ASCII characters; otherwise Windows may fail after all functional tests have passed.
- Do not describe a worker test fixture as a real model-generation experiment. The fake worker validates orchestration and provenance only; actual TripoSR inference still requires a source image with explicit generation/preview permission, pinned local weights, and a qualifying GPU.
- No inference was run in this phase. KSST-01 imagery/datasheet reuse remains blocked by the vendor's stated rights restriction; no image or CAD bytes were used as generation input.

## Acceptance state

The two fixes and this record are committed to the P9-M4-I feature branch. Final acceptance requires the latest same-head Ubuntu and Windows jobs to pass. The pilot benchmark remains `pilot_specification_not_yet_executed` until a permitted source image and isolated GPU inference are available.
## Same-head CI closure (2026-11-19)

- Final code head for this correction: `aaef2e03417bcff3de90de7117698a511286e429`.
- Official workflow `44475016651` passed on both Ubuntu 24.04 and Windows 2022: 32 passed on each OS; benchmark manifest gate passed on both.
- The first zero-extent regression fixture itself initially failed earlier in GLB parsing because a byte-string escape was nested differently in the ordinary Python helper and the triple-quoted fake-worker source. The ordinary helper now writes the four-byte BIN chunk marker using a true NUL byte; the embedded fake-worker source keeps the extra source-level escaping required by its outer triple-quoted string.
- This preserved the auditor's original zero-extent rejection and added a deterministic regression for it. Windows UTF-8 CLI help also passed.
- CI success validates schemas, the GLB auditor, worker orchestration with a fake model checkout, and benchmark metadata. It does **not** mean TripoSR inference has run or that a star-tracker model exists.
## Benchmark 状态同步经验（2026-11-19）

- 原始图下载与 artifact receipt 已在同一 head 通过，但跨平台 benchmark manifest gate 因 workflow 仍期待旧 `license_assessment_status`（`...pending_project_owner_signoff_and_source_hash`）而失败；benchmark 已将字节获取与权利签核拆成两个独立状态。
- 修复为 workflow 只校验当前状态 `policy_basis_found_pending_project_owner_signoff`，并单独校验 `source_bytes_obtained=true`、固定 SHA-256、bytes、dimensions 和 `eligible_for_inference=false`。这避免把“已拿到源文件”错误解释成“用途已批准”。
- 同类经验：当 manifest 字段拆分状态后，CI gate、source receipt 和文档必须按新状态机一起更新；只更新数据文件会让跨平台 gate 继续验证过期语义。

## 公开仓库的原始来源制品边界（2026-11-19）

- GitHub Actions artifact 对拥有仓库读取权限的人开放；本仓库为 public，因此不能把“CI artifact”误认为内部存储。
- 旧 source-intake job 将 NASA/JPL-Caltech 原始 JPEG 与 receipt 一起上传，但来源记录明确为 `external_distribution_allowed=false`、`project_owner_signoff=pending`。这是来源策略与工程实现之间的冲突，不能因为图片可从 NASA 页面公开访问就忽略项目自己的分发限制。
- 修复：采集 CLI 增加 `--receipt-only`，CI 在内存中下载、校验 JPEG、计算 SHA-256 和尺寸后只写 `source-intake.json`；公开 artifact 精确限定为该 JSON，7 天保留。原始图不落盘、不进入 artifact。
- 历史公开原图 artifact `14509376634`（run `44482953922`）与 `14510716115`（run `44485479649`）已完成删除；按 artifact ID 查询均返回 HTTP 404，两个原 workflow run 的 artifact 列表也均为空。PR 内新增的 GitHub Actions cleanup step 会幂等删除并复核这两个固定 ID，避免历史制品重新暴露。
- 推理门禁继续保持关闭：来源用途签核、人工 crop/alpha-mask 审查、原始/处理后哈希、固定权重和 CUDA 预检均完成前，`inference_eligible=false`。


## 历史公开原图 artifact 清理完成（2026-11-19）

- Artifact `14509376634`（run `44482953922`）和 `14510716115`（run `44485479649`）均已不可访问：按 artifact ID 查询返回 HTTP 404，两个原 workflow run 的 artifact 列表也均为空。
- 新 CI 在上传只含 `source-intake.json` 的 7 天 receipt artifact 前，执行幂等清理和二次查询；若历史 ID 仍可访问，CI 必须失败。
- 清理只处理上述两个明确的历史 ID，不删除其他构建/审查证据。
