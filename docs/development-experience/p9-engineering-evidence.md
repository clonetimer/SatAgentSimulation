# P9 Engineering Evidence Review — Development Rules

Date: 2026-11-17

## CAD geometry is not as-built installation evidence

A STEP assembly tree may provide:
- source node path and occurrence identity;
- parent-child structure;
- local and composed world transforms;
- geometry counts and source-unit conversion.

These facts describe the CAD document. They do not independently prove:
- which flight unit (Tsuru, Guarani-1, Maya-2) the occurrence applies to;
- whether the part is FM, EM, B3, or a later approved revision;
- whether the CAD assembly is the approved as-built configuration;
- whether the CAD root datum and axes match the engineering spacecraft body frame;
- whether a CAD placement is the installed transform of a specific physical unit.

Never copy `world_transform_m` into an as-built `installation_transform` field.

## Separate the evidence gates

Review the following as distinct claims:
1. Flight-unit applicability.
2. Hardware part number and revision.
3. Parent-child assembly relation.
4. Datum, origin, axes, handedness and units.
5. Approved six-degree-of-freedom installation transform.

Each claim needs a traceable source location, revision/hash, reviewer and explicit disposition. A file URL or a source document being downloaded is not a reviewed claim.

## Validate transform mathematics without overclaiming

For each CAD source transform:
- validate finite 4x4 homogeneous matrix;
- validate last row [0, 0, 0, 1];
- validate rotation orthogonality and determinant +1;
- validate parent-world × child-local = child-world;
- retain original matrices and source node path.

A passing transform test only establishes internal mathematical consistency. It does not establish datum identity or physical installation correctness.

## DOCX evidence extraction limits

Extract paragraphs and tables in document order and preserve stable paragraph/table/cell indices. Do not invent page numbers from OOXML body order. Keep raw extracted text separate from human interpretation. Extraction success and hash verification do not imply review or approval.

## Fail-closed promotion

Until all required claims have reviewed evidence and explicit approval:
- review status remains blocked;
- `instance_id`, `installation_transform`, and `spatial_anchor` remain null;
- `render_eligible` remains false;
- no CAD candidate enters the as-built satellite view or telemetry binding.

A useful M3 deliverable is an honest evidence-gap packet, not a fabricated complete digital satellite.
