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
