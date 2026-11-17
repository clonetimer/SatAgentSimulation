# P9-M3 BIRDS-4 Candidate Evidence Review Worksheet

Status: **Evidence locator packet — review pending**  
Source: BIRDS-4 CAD full-FM reference, pinned upstream commit `3bab47a0f53410ef79005d21321ff659726f0a1c`  
Assembly Procedure: pinned commit `d7eadd98f5327e1ddea82b640293d49bfdb09555`, Git blob SHA-1 `4236578c000b413773399d91e094b57a20a52e28`.

> Important: all coordinates below are STEP/CAD source geometry transforms converted to metres. They are not approved as-built installation coordinates, and must not be copied into the flight-unit engineering model without independent evidence.

## 1. Source document scope

The FM Assembly Procedure paragraph 21 states that the document guides assembly of the BIRDS-4 Structural Testing Model. The procedure contains an axis-definition section (paragraphs 39–42), but the current text extraction does not provide a numeric spacecraft datum origin or a complete six-degree-of-freedom transform table.

The document also records a July 2020 rework involving BIRDS-4 FM satellites and specifically mentions Maya-2 / Frame 5A (paragraph 495). That rework context must not be generalized into proof that every listed assembly step describes the final as-built configuration of Maya-2, Tsuru or Guarani-1.

## 2. Candidate review records

### A. Battery

- Engineering category: `component.battery`
- CAD occurrence: `BIRDS4_FM_Final v7/B4_battery v1`
- Parent occurrence: `BIRDS4_FM_Final v7`
- Child count: 14
- CAD local/world translation (m): `[0.0276196364030337, 0.017971248182285, 0.0140942438378067]`
- Procedure locators:
  - Paragraph 106: Assembling the battery
  - Tables 12–14: battery case, battery installation and cover/checklist
  - Table 3 parts-list item 4: Battery, quantity 1
- Current conclusion: the procedure provides structural-testing-model battery assembly evidence; flight-unit applicability and approved installation datum remain unresolved.

Reviewer disposition:
- Flight unit / configuration ID:
- Approved BOM reference and revision:
- Battery part number and hardware revision:
- Approved assembly drawing and revision:
- Coordinate datum/frame reference:
- Installation transform evidence:
- Reviewer / date / decision:

### B. Reaction wheel

- Engineering category: `component.reaction_wheel`
- CAD occurrence: `BIRDS4_FM_Final v7/B4_RW_Assembly v55`
- Parent occurrence: `BIRDS4_FM_Final v7`
- Child count: 24
- CAD local/world translation (m): `[0.0, 0.0, 0.085]`
- Procedure locators:
  - Paragraph 113: Assembling the Reaction Wheel
  - Tables 15–17: reaction-wheel assembly, fasteners and checklist
  - Table 5 parts-list item 45: Brushless motor (reaction wheel), quantity 1
  - Table 5 items 46–48: reaction-wheel holder plates and separators
- Current conclusion: the procedure supports the reaction-wheel assembly function and a structural-model assembly sequence; it does not independently identify a specific flight-unit installation frame.

Reviewer disposition:
- Flight unit / configuration ID:
- Approved BOM reference and revision:
- Reaction-wheel part number and hardware revision:
- Approved assembly drawing and revision:
- Coordinate datum/frame reference:
- Installation transform evidence:
- Reviewer / date / decision:

### C. Perovskite Solar Cells payload

- Engineering category: `component.payload`
- CAD occurrence: `BIRDS4_FM_Final v7/B4_FM_-Y_panel v3/Perovskite_Solar_Cells v3`
- Parent occurrence: `BIRDS4_FM_Final v7/B4_FM_-Y_panel v3`
- Child count: 1
- CAD local translation (m): `[0.0102179932594299, 0.0044220067405700905, 0.021963656828658]`
- CAD world translation (m): `[0.009100288827054666, -0.050000154744625804, -0.006040134082429756]`
- Procedure locators:
  - Table 9 checklist item 7 (body row 8 including header): Perovskite solar cells appears in the adhesive-applied-location checklist
  - Table 9 also lists the -Y panel as an adhesive/installation context
  - Paragraph 137 and Tables 20–37: -Y panel assembly procedure
  - Table 3 parts-list item 8: -Y panel, quantity 1
- Current conclusion: the procedure supports a Perovskite Solar Cells payload relationship with the -Y panel in the Structural Testing Model assembly documentation. It does not prove a specific flight unit's as-built placement or a numeric body-frame transform.

Reviewer disposition:
- Flight unit / payload mission configuration:
- Payload part number and revision:
- Approved payload-to-panel interface drawing:
- Approved -Y panel drawing and revision:
- Coordinate datum/frame reference:
- Installation transform evidence:
- Reviewer / date / decision:

## 3. Cross-cutting mandatory checks

| Claim | Evidence required | Current status |
|---|---|---|
| Flight-unit identity | Tsuru / Guarani-1 / Maya-2 approved configuration record | Unresolved |
| FM/EM/B3 applicability | Approved part/BOM and revision cross-reference | Unresolved; full CAD source contains mixed labels |
| Assembly relation | Procedure step plus approved assembly drawing | Procedure locators found; approval review pending |
| Coordinate datum | Approved datum drawing defining origin, axes and handedness | Unresolved |
| Installation transform | Approved transform chain between part, parent and spacecraft body frames | Unresolved |
| As-built verification | Unit-specific BOM/inspection or AIT evidence | Not supplied by the CAD source package |

## 4. Review rules

- Do not treat the Structural Testing Model procedure as a flight-unit as-built record.
- Do not treat a STEP `world_transform_m` as the approved installation transform.
- Do not use Newsletter Issue 49 page 158 as a numeric datum or six-DOF transform source.
- Do not infer a specific flight unit from `FM_Final` in the file name.
- A candidate remains blocked unless every required claim has a traceable, reviewed source and an explicit decision.
- No candidate may receive an instance ID, installation transform, spatial anchor or render eligibility from this worksheet alone.


## 5. Flight-unit test-report evidence — source verified, interpretation bounded

The four additional documents were downloaded from the pinned ProceduresAndReports commit and verified against their Git blob SHA-1 values. SHA-256 receipts and OOXML text/table indices are preserved in CI artifact `p9m3-birds4-flight-unit-test-reports`.

### Vibration reports

- Guarani-1 report: paragraph 9 title; paragraph 24 states the report applies to a vibration test for GuaraniSat-1; paragraph 36 names GuaraniSat-1 as the tested satellite; paragraph 245 concludes the tested GuaraniSat-1 passed its vibration requirements.
- Maya-2 report: paragraph 9 title; paragraph 24 states the report applies to Maya-2; paragraph 36 names Maya-2 as the tested satellite; paragraph 243 concludes the tested Maya-2 passed its vibration requirements.
- Tsuru report: paragraph 9 title; paragraph 24 states the report applies to Tsuru; paragraph 36 names Tsuru as the tested satellite; paragraph 244 concludes the tested Tsuru passed its vibration requirements.
- Each report's Table 3 is a common test-article inventory listing all three BIRDS-4 FM units (Tsuru, Maya-2 and GuaraniSat-1) and the 3U deployment pod. The individual report title and scope paragraphs, not the common inventory table alone, identify the test article for that report.

These reports establish that each named unit underwent a vibration test and passed the stated vibration acceptance criteria. They do not supply the detailed component BOM, six-degree-of-freedom component mounting matrices or a final as-built assembly drawing. Therefore they strengthen flight-unit provenance but do not close the installation-transform gate.

### FM thermal-vacuum report

- Paragraph 4 defines the test as BIRDS-4 Flight Model thermal-vacuum qualification.
- Paragraph 6 states the scope is the BIRDS-4 FM thermal-vacuum test.
- Paragraph 121 states the test subjects had the same hardware as the satellite EM model. This is an explicit reason to keep the test report separate from flight-unit-specific hardware configuration approval.
- Table 11 identifies temperature measurement points including battery box, reaction-wheel assembly and six external panels.
- Table 12 groups external panels and internal components, including battery box and reaction-wheel assembly.
- Table 17 gives component operating temperature ranges; Table 18 records temperature ranges by Paraguay, Philippines and Japan columns.

This is valuable evidence that the FM TVT campaign instrumented the battery box, reaction-wheel assembly and external panels. The country columns are useful for locating unit-specific test traces, but must not be interpreted as proof of different part numbers or approved installation transforms.

### Effect on the three candidate decisions

| Candidate | Additional evidence | What it supports | What remains open |
|---|---|---|---|
| Battery | Three unit-specific vibration reports; FM TVT battery-box sensors and temperature limits | Flight-unit test provenance and battery-box thermal monitoring | Approved battery part number/revision, unit BOM, datum and installed transform |
| Reaction wheel | Three unit-specific vibration reports; FM TVT reaction-wheel sensor locations and temperature limits | Flight-unit test provenance and reaction-wheel thermal monitoring | Approved wheel/holder configuration, unit BOM, datum and installed transform |
| Perovskite payload | FM Assembly Procedure Table 9 adhesive checklist and -Y panel procedure | Structural Testing Model procedure's payload-to--Y-panel assembly context | Flight-unit payload configuration, approved payload interface drawing, final unit applicability and installed transform |

**No promotion is authorized by these reports.** All three candidates remain `blocked_evidence_incomplete`, with null instance/installation transform/spatial anchor and `render_eligible=false`.
