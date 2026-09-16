#!/usr/bin/env node
/** Build the two user-facing synthetic demonstration workbooks.
 *
 * XLSX authoring uses @oai/artifact-tool. The Python finalisation script passes
 * the live formula-catalogue inventory, recalculates the exported files through
 * LibreOffice, and writes the evidence contracts consumed by the tests.
 */

import fs from "node:fs/promises";
import path from "node:path";

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

const outputDir = args.get("--output-dir");
const supportedFunctions = JSON.parse(args.get("--supported-functions") || "[]");
if (!outputDir || supportedFunctions.length === 0) {
  throw new Error("--output-dir and --supported-functions are required");
}

let artifactModule;
try {
  artifactModule = await import("@oai/artifact-tool");
} catch (error) {
  const fallback = process.env.ARTIFACT_TOOL_MODULE;
  if (!fallback) {
    throw new Error(
      "@oai/artifact-tool is unavailable. Set ARTIFACT_TOOL_MODULE to its artifact_tool.mjs path."
    );
  }
  artifactModule = await import(fallback);
}

const { SpreadsheetFile, Workbook } = artifactModule;

const FONT = "Arial";
const NAVY = "#17365D";
const BLUE = "#D9EAF7";
const LIGHT_BLUE = "#EAF2F8";
const INPUT_YELLOW = "#FFF2CC";
const GREEN = "#E2F0D9";
const RED = "#FCE4D6";
const WHITE = "#FFFFFF";
const DARK = "#1F2937";
const FORMULA_GREEN = "#008000";
const INPUT_BLUE = "#0000FF";
const BORDER = "#B7C9D6";

const currencyFormat = "#,##0.00;[Red](#,##0.00);-";
const percentageFormat = "0.0%";
const precisePercentageFormat = "0.00%";

function excelRound(value, digits = 0) {
  const factor = 10 ** digits;
  return Math.sign(value) * Math.floor(Math.abs(value) * factor + 0.5 + 1e-10) / factor;
}

function excelRoundUp(value, digits = 0) {
  const factor = 10 ** digits;
  return Math.sign(value) * Math.ceil(Math.abs(value) * factor - 1e-10) / factor;
}

function excelRoundDown(value, digits = 0) {
  const factor = 10 ** digits;
  return Math.sign(value) * Math.floor(Math.abs(value) * factor + 1e-10) / factor;
}

function excelCeiling(value, significance) {
  return Math.ceil(value / significance - 1e-12) * significance;
}

function excelFloor(value, significance) {
  return Math.floor(value / significance + 1e-12) * significance;
}

function applyBaseStyle(workbook) {
  for (const sheet of workbook.worksheets.items) {
    sheet.showGridLines = false;
    const used = sheet.getUsedRange();
    if (used) {
      used.format.font = { name: FONT, size: 10, color: DARK };
      used.format.verticalAlignment = "center";
    }
  }
}

function styleTitle(sheet, range) {
  const title = sheet.getRange(range);
  title.format.font = { name: FONT, size: 14, bold: true, color: NAVY };
  title.format.rowHeight = 24;
}

function styleHeader(sheet, range) {
  sheet.getRange(range).format = {
    fill: NAVY,
    font: { name: FONT, size: 10, bold: true, color: WHITE },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: BORDER },
  };
}

function styleSection(sheet, range) {
  sheet.getRange(range).format = {
    fill: LIGHT_BLUE,
    font: { name: FONT, size: 10, bold: true, color: NAVY },
    borders: { preset: "outside", style: "thin", color: BORDER },
  };
}

function setWidths(sheet, widths) {
  for (const [range, width] of Object.entries(widths)) {
    sheet.getRange(range).format.columnWidth = width;
  }
}

async function renderPreviews(workbook, folder, ranges) {
  await fs.mkdir(folder, { recursive: true });
  for (const [sheetName, range] of Object.entries(ranges)) {
    const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
    const safeName = sheetName.replaceAll(" ", "_");
    await fs.writeFile(
      path.join(folder, `${safeName}.png`),
      new Uint8Array(await preview.arrayBuffer())
    );
  }
}

async function exportWorkbook(workbook, outputPath) {
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
}

function signedLine(label, account, value, evidence) {
  return {
    account_number: account,
    label,
    // Reference figures and the human-entered control total use the same
    // six-decimal currency precision. Without this, a long NPV decimal can
    // make an otherwise complete control total fail on binary representation.
    amount: excelRound(Math.abs(value), 6),
    debit_credit: value < 0 ? "credit" : "debit",
    ledger_source: "Synthetic formula control extract",
    evidence_reference: evidence,
  };
}

async function buildCase5() {
  const workbook = Workbook.create();
  const outputs = workbook.worksheets.add("Outputs");
  const calculations = workbook.worksheets.add("Calculations");
  const inputs = workbook.worksheets.add("Inputs");
  const assumptions = workbook.worksheets.add("Assumptions");
  const lookups = workbook.worksheets.add("Lookups");
  const boundary = workbook.worksheets.add("Scope Boundary");
  const guide = workbook.worksheets.add("Demo Guide");

  const inputRows = [
    ["SYN-F001", "Motor", "North", "Active", 120000, 80000, -2500, null],
    ["SYN-F002", "Property", "South", "Active", 200000, 110000, 0, 5000],
    ["SYN-F003", "Liability", "North", "Inactive", 150000, 90000, 1800, null],
    ["SYN-F004", "Motor", "North", "Active", 130000, 70000, -1200, 0],
    ["SYN-F005", "Property", "North", "Active", 175000, 95000, 900, 0],
    ["SYN-F006", "Liability", "South", "Active", 220000, 125000, -3000, null],
    ["SYN-F007", "Motor", "South", "Inactive", 100000, 65000, 0, 2500],
    ["SYN-F008", "Property", "North", "Active", 190000, 105000, 1100, null],
  ];
  const claims = inputRows.map((row) => row[5]);
  const premiums = inputRows.map((row) => row[4]);
  const adjustments = inputRows.map((row) => row[6]);
  const selectedProduct = "Property";
  const selectedRegion = "North";
  const positiveBoundary = 2500.5;
  const negativeBoundary = -2500.51;
  const roundDigits = 0;
  const decimalDigits = 1;
  const significance = 100;
  const exposureUnit = 100000;
  const factorByProduct = { Motor: 1.12, Property: 1.08, Liability: 1.18 };

  const portfolioWeights = [0.90, 1.05, 1.10, 0.95, 1.00, 1.15, 0.85, 1.08];

  inputs.getRange("A1:I1").values = [["Synthetic portfolio inputs"]];
  inputs.getRange("A2:I2").values = [[
    "All records and amounts are fictional. Blank optional reserves and explicit zeros are intentional.",
  ]];
  inputs.getRange("A3:I3").values = [[
    "Cohort ID", "Product", "Region", "Active", "Premium (EUR)", "Claim estimate (EUR)",
    "Adjustment (EUR)", "Optional reserve (EUR)", "Portfolio weight",
  ]];
  inputs.getRange("A4:H11").values = inputRows;
  inputs.getRange("I4:I11").values = portfolioWeights.map((value) => [value]);

  assumptions.getRange("A1:C1").values = [["Formula demonstration assumptions"]];
  assumptions.getRange("A3:C3").values = [["Input", "Value", "Use"]];
  assumptions.getRange("A4:C14").values = [
    ["Selected product", selectedProduct, "Criteria and lookup tests"],
    ["Selected region", selectedRegion, "Multiple-criteria tests"],
    ["Whole-number precision", roundDigits, "ROUND control"],
    ["Decimal precision", decimalDigits, "ROUNDUP and ROUNDDOWN controls"],
    ["Rounding significance", significance, "CEILING and FLOOR controls"],
    ["Positive half-unit boundary", positiveBoundary, "Positive Excel rounding boundary"],
    ["Negative rounding input", negativeBoundary, "Negative rounding behaviour"],
    ["Fallback zero", 0, "Explicit zero branch"],
    ["Lookup product", selectedProduct, "Exact lookup key"],
    ["Exposure unit", exposureUnit, "Whole premium exposure units"],
    ["Active status", "Active", "Text criterion used by population controls"],
  ];
  assumptions.getRange("A15:A16").values = [["Boolean TRUE"], ["Boolean FALSE"]];
  assumptions.getRange("B15:B16").formulas = [["=TRUE()"], ["=FALSE()"]];
  assumptions.getRange("C15:C16").values = [
    ["Boolean function used in the nested IF control"],
    ["Boolean function used in the nested IF control"],
  ];

  lookups.getRange("A1:B1").values = [["Synthetic product lookup"]];
  lookups.getRange("A3:B3").values = [["Product", "Risk factor"]];
  lookups.getRange("A4:B6").values = [
    ["Motor", factorByProduct.Motor],
    ["Property", factorByProduct.Property],
    ["Liability", factorByProduct.Liability],
  ];
  lookups.getRange("D1:E1").values = [["Synthetic periodic cash flows", null]];
  lookups.getRange("D3:E3").values = [["Period", "Net cash flow (EUR)"]];
  lookups.getRange("D4:E7").values = [[1, -1000], [2, 300], [3, 300], [4, 300]];
  assumptions.getRange("A17:C20").values = [
    ["Periodic discount rate", 0.10, "NPV control"],
    ["Scenario 1 factor", 0.95, "CHOOSE control"],
    ["Scenario 2 factor", 1.08, "CHOOSE control"],
    ["Scenario 3 factor", 1.15, "CHOOSE control"],
  ];

  const cases = [
    {
      id: "FML-001", label: "Portfolio claim estimate total", primary: "SUM",
      formula: "=SUM(Inputs!$F$4:$F$11)",
      expected: claims.reduce((a, b) => a + b, 0),
      meaning: "Adds the claim estimates across the synthetic portfolio.",
    },
    {
      id: "FML-002", label: "Absolute net portfolio adjustment", primary: "ABS",
      formula: "=ABS(SUM(Inputs!$G$4:$G$11))",
      expected: Math.abs(adjustments.reduce((a, b) => a + b, 0)),
      meaning: "Shows the magnitude of the signed adjustment total.",
    },
    {
      id: "FML-003", label: "Whole premium exposure units", primary: "INT",
      formula: "=INT(SUM(Inputs!$E$4:$E$11)/Assumptions!$B$13)",
      expected: Math.floor(premiums.reduce((a, b) => a + b, 0) / exposureUnit),
      meaning: "Converts total premium into complete EUR 100,000 exposure units.",
    },
    {
      id: "FML-004", label: "Rounded positive reserve adjustment", primary: "ROUND",
      formula: "=ROUND(Assumptions!$B$9,Assumptions!$B$6)",
      expected: excelRound(positiveBoundary, roundDigits),
      meaning: "Exercises Excel half-away-from-zero rounding on a positive amount.",
    },
    {
      id: "FML-005", label: "Conservative negative adjustment", primary: "ROUNDUP",
      formula: "=ROUNDUP(Assumptions!$B$10,Assumptions!$B$7)",
      expected: excelRoundUp(negativeBoundary, decimalDigits),
      meaning: "Rounds a negative adjustment away from zero.",
    },
    {
      id: "FML-006", label: "Truncated negative adjustment", primary: "ROUNDDOWN",
      formula: "=ROUNDDOWN(Assumptions!$B$10,Assumptions!$B$7)",
      expected: excelRoundDown(negativeBoundary, decimalDigits),
      meaning: "Rounds a negative adjustment towards zero.",
    },
    {
      id: "FML-007", label: "Upper reserve reporting band", primary: "CEILING",
      formula: "=CEILING(Assumptions!$B$9,Assumptions!$B$8)",
      expected: excelCeiling(positiveBoundary, significance),
      meaning: "Rounds an amount up to the next EUR 100 reporting band.",
    },
    {
      id: "FML-008", label: "Lower reserve reporting band", primary: "FLOOR",
      formula: "=FLOOR(Assumptions!$B$9,Assumptions!$B$8)",
      expected: excelFloor(positiveBoundary, significance),
      meaning: "Rounds an amount down to the previous EUR 100 reporting band.",
    },
    {
      id: "FML-009", label: "SUMIF product reserve total", primary: "SUMIF",
      formula: "=SUMIF(Inputs!$B$4:$B$11,Assumptions!$B$4,Inputs!$F$4:$F$11)",
      expected: inputRows.filter((row) => row[1] === selectedProduct).reduce((a, row) => a + row[5], 0),
      meaning: "Adds claims for the selected product.",
    },
    {
      id: "FML-010", label: "SUMIFS product-region reserve total", primary: "SUMIFS",
      formula: "=SUMIFS(Inputs!$F$4:$F$11,Inputs!$B$4:$B$11,Assumptions!$B$4,Inputs!$C$4:$C$11,Assumptions!$B$5)",
      expected: inputRows.filter((row) => row[1] === selectedProduct && row[2] === selectedRegion).reduce((a, row) => a + row[5], 0),
      meaning: "Adds claims for one product and one region.",
    },
    {
      id: "FML-011", label: "COUNTIF selected product population", primary: "COUNTIF",
      formula: "=COUNTIF(Inputs!$B$4:$B$11,Assumptions!$B$4)",
      expected: inputRows.filter((row) => row[1] === selectedProduct).length,
      meaning: "Counts cohorts for the selected product.",
    },
    {
      id: "FML-012", label: "COUNTIFS active North population", primary: "COUNTIFS",
      formula: "=COUNTIFS(Inputs!$C$4:$C$11,Assumptions!$B$5,Inputs!$D$4:$D$11,Assumptions!$B$14)",
      expected: inputRows.filter((row) => row[2] === selectedRegion && row[3] === "Active").length,
      meaning: "Counts active cohorts in the selected region.",
    },
    {
      id: "FML-013", label: "AVERAGEIF optional Property mean", primary: "AVERAGEIF",
      formula: "=AVERAGEIF(Inputs!$B$4:$B$11,Assumptions!$B$4,Inputs!$H$4:$H$11)",
      expected: 2500,
      meaning: "Excludes blanks but includes the genuine zero in the average.",
    },
    {
      id: "FML-014", label: "AVERAGEIFS active North mean", primary: "AVERAGEIFS",
      formula: "=AVERAGEIFS(Inputs!$F$4:$F$11,Inputs!$C$4:$C$11,Assumptions!$B$5,Inputs!$D$4:$D$11,Assumptions!$B$14)",
      expected: 87500,
      meaning: "Averages claims meeting region and active-status conditions.",
    },
    {
      id: "FML-015", label: "MINIFS active claims minimum", primary: "MINIFS",
      formula: "=_xlfn.MINIFS(Inputs!$F$4:$F$11,Inputs!$D$4:$D$11,Assumptions!$B$14)",
      expected: 70000,
      meaning: "Finds the minimum claim estimate in the active population.",
    },
    {
      id: "FML-016", label: "MAXIFS active claims maximum", primary: "MAXIFS",
      formula: "=_xlfn.MAXIFS(Inputs!$F$4:$F$11,Inputs!$D$4:$D$11,Assumptions!$B$14)",
      expected: 125000,
      meaning: "Finds the maximum claim estimate in the active population.",
    },
    {
      id: "FML-017", label: "IF first active claim", primary: "IF",
      formula: "=IF(Assumptions!$B$15,Inputs!$F$4,IF(Assumptions!$B$16,Inputs!$F$5,Assumptions!$B$11))",
      expected: 80000,
      meaning: "Returns the claim estimate when the cohort flag is TRUE.",
    },
    {
      id: "FML-018", label: "Exact product risk factor", primary: "VLOOKUP",
      formula: "=VLOOKUP(Assumptions!$B$12,Lookups!$A$4:$B$6,2,FALSE())",
      expected: factorByProduct[selectedProduct],
      meaning: "Retrieves a product factor using an exact VLOOKUP.",
    },
    {
      id: "FML-019", label: "Exact product table position", primary: "MATCH",
      formula: "=MATCH(Assumptions!$B$12,Lookups!$A$4:$A$6,0)",
      expected: 2,
      meaning: "Finds the exact position of the selected product.",
    },
    {
      id: "FML-020", label: "Nested product risk factor", primary: "INDEX",
      formula: "=INDEX(Lookups!$B$4:$B$6,MATCH(Assumptions!$B$12,Lookups!$A$4:$A$6,0))",
      expected: factorByProduct[selectedProduct],
      meaning: "Combines INDEX and MATCH to retrieve the same risk factor.",
    },
    {
      id: "FML-021", label: "Logical all-controls result", primary: "AND",
      formula: "=IF(AND(Assumptions!$B$15,Assumptions!$B$15),1,0)",
      expected: 1,
      meaning: "Confirms that both required boolean controls are TRUE.",
    },
    {
      id: "FML-022", label: "Logical any-control result", primary: "OR",
      formula: "=IF(OR(Assumptions!$B$16,Assumptions!$B$15),1,0)",
      expected: 1,
      meaning: "Confirms that at least one boolean control is TRUE.",
    },
    {
      id: "FML-023", label: "Weighted claim estimate", primary: "SUMPRODUCT",
      formula: "=SUMPRODUCT(Inputs!$F$4:$F$11,Inputs!$I$4:$I$11)",
      expected: claims.reduce((total, claim, index) => total + claim * portfolioWeights[index], 0),
      meaning: "Weights cohort claim estimates without hiding the cohort-level drivers.",
    },
    {
      id: "FML-024", label: "Present value of periodic cash flows", primary: "NPV",
      formula: "=NPV(Assumptions!$B$17,Lookups!$E$4:$E$7)",
      expected: -230.8585479133939,
      meaning: "Applies Excel's period-one NPV convention to a visible cash-flow schedule.",
    },
    {
      id: "FML-025", label: "Selected scenario risk factor", primary: "CHOOSE",
      formula: "=CHOOSE(2,Assumptions!$B$18,Assumptions!$B$19,Assumptions!$B$20)",
      expected: 1.08,
      meaning: "Selects the second visible scenario factor using a 1-based index.",
    },
    {
      id: "FML-026", label: "Exact product factor via XLOOKUP", primary: "XLOOKUP",
      formula: "=_xlfn.XLOOKUP(Assumptions!$B$12,Lookups!$A$4:$A$6,Lookups!$B$4:$B$6,,0,1)",
      expected: factorByProduct[selectedProduct],
      meaning: "Retrieves a numeric exact-match factor with XLOOKUP.",
    },
  ];

  const exercised = new Set(cases.flatMap((item) => {
    const names = [...item.formula.matchAll(/([A-Za-z_][A-Za-z0-9_.]*)\s*\(/g)]
      .map((match) => match[1].toUpperCase().replace("_XLFN.", ""))
      .filter((name) => name !== "TRUE" && name !== "FALSE");
    return names;
  }));
  const missing = supportedFunctions.filter((name) => !exercised.has(name));
  const unexpected = [...exercised].filter((name) => !supportedFunctions.includes(name));
  if (missing.length || unexpected.length) {
    throw new Error(`Case 5 catalogue mismatch. Missing: ${missing}; unexpected: ${unexpected}`);
  }

  calculations.getRange("A1:D1").values = [["Supported formula calculations"]];
  calculations.getRange("A2:D2").values = [[
    "Each row is a separate synthetic financial control. Formula inputs remain visible on the supporting tabs.",
  ]];
  calculations.getRange("A3:D3").values = [["Output label", "Calculated result", "Primary function", "Business meaning"]];
  const lastRow = cases.length + 3;
  calculations.getRange(`A4:A${lastRow}`).values = cases.map((item) => [item.label]);
  calculations.getRange(`B4:B${lastRow}`).formulas = cases.map((item) => [item.formula]);
  calculations.getRange(`C4:D${lastRow}`).values = cases.map((item) => [item.primary, item.meaning]);

  outputs.getRange("A1:F1").values = [["Case 5: supported formula demonstration"]];
  outputs.getRange("A2:F2").values = [[
    "Select all cells in column C below at Gate 2. Each calculation should reconstruct completely.",
  ]];
  outputs.getRange("A3:F3").values = [["Control account", "Authoritative output", "Result", "Primary function", "Expected status", "Output cell"]];
  outputs.getRange(`A4:B${lastRow}`).values = cases.map((item) => [item.id, item.label]);
  outputs.getRange(`C4:C${lastRow}`).formulas = cases.map((_, index) => [`=Calculations!B${index + 4}`]);
  outputs.getRange(`D4:E${lastRow}`).values = cases.map((item) => [item.primary, "complete / pass"]);
  outputs.getRange(`F4:F${lastRow}`).values = cases.map((_, index) => [`Outputs!C${index + 4}`]);

  boundary.getRange("A1:C1").values = [["Deliberately unsupported boundary examples"]];
  boundary.getRange("A2:C2").values = [[
    "These cells are not part of the successful Gate 2 output set. Selecting either must return partial and incomplete.",
  ]];
  boundary.getRange("A3:C3").values = [["Boundary", "Cached workbook result", "Expected reconstruction"]];
  boundary.getRange("A4:A5").values = [
    ["Approximate VLOOKUP"],
    ["OFFSET reference"],
  ];
  boundary.getRange("B4:B5").formulas = [
    ["=VLOOKUP(Assumptions!$B$12,Lookups!$A$4:$B$6,2,TRUE())"],
    ["=OFFSET(Inputs!$F$4,1,0)"],
  ];
  boundary.getRange("C4:C5").values = [["partial / incomplete"], ["partial / incomplete"]];

  guide.getRange("A1:C1").values = [["Case 5 demonstration guide"]];
  guide.getRange("A3:C8").values = [
    ["Scope", `All ${supportedFunctions.length} functions declared supported by the production formula catalogue.`, null],
    ["Synthetic data", "All entities, accounts and amounts are fictional.", null],
    ["Gate 1 context", "Aurora Formula Assurance SA; 2025-Q4; EUR; synthetic formula control demonstration.", null],
    ["Gate 2 selection", `Select Outputs!C4:C${lastRow} as the authoritative outputs.`, null],
    ["Gate 3", "Approve every proposed one-to-one mapping and choose both materiality thresholds.", null],
    ["Gate 4", "Create the named approval record before generating the PDF.", null],
  ];
  guide.getRange("A10:C10").values = [["Control", "Expected outcome", "Evidence"]];
  guide.getRange("A11:C15").values = [
    ["Supported calculation population", `${cases.length} complete outputs`, `Outputs!C4:C${lastRow}`],
    ["Internal reconciliation", "pass with zero deltas", "Excel cached values compared with Python reconstruction"],
    ["External reconciliation", "pass only after human mapping approval", "Synthetic formula control extract"],
    ["Unsupported boundary", "partial and incomplete", "Scope Boundary!B4:B5"],
    ["Report", "available only after Gate 4", "Named approval record required"],
  ];

  applyBaseStyle(workbook);
  styleTitle(outputs, "A1:F1");
  styleTitle(calculations, "A1:D1");
  styleTitle(inputs, "A1:H1");
  styleTitle(assumptions, "A1:C1");
  styleTitle(lookups, "A1:B1");
  styleTitle(boundary, "A1:C1");
  styleTitle(guide, "A1:C1");
  styleHeader(outputs, "A3:F3");
  styleHeader(calculations, "A3:D3");
  styleHeader(inputs, "A3:H3");
  styleHeader(assumptions, "A3:C3");
  styleHeader(lookups, "A3:B3");
  styleHeader(boundary, "A3:C3");
  styleHeader(guide, "A10:C10");
  outputs.getRange(`C4:C${lastRow}`).format.numberFormat = currencyFormat;
  outputs.getRange(`C4:C${lastRow}`).format.font = { name: FONT, size: 10, color: DARK };
  calculations.getRange(`B4:B${lastRow}`).format.numberFormat = currencyFormat;
  calculations.getRange(`B4:B${lastRow}`).format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  inputs.getRange("E4:H11").format.numberFormat = currencyFormat;
  inputs.getRange("I4:I11").format.numberFormat = precisePercentageFormat;
  inputs.getRange("A4:I11").format.borders = { preset: "inside", style: "thin", color: "#E5E7EB" };
  assumptions.getRange("B4:B20").format.fill = INPUT_YELLOW;
  assumptions.getRange("B4:B20").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  assumptions.getRange("B17:B20").format.numberFormat = precisePercentageFormat;
  lookups.getRange("B4:B6").format.fill = INPUT_YELLOW;
  lookups.getRange("B4:B6").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  boundary.getRange("B4:B5").format.fill = RED;
  guide.getRange("A3:A8").format.font = { name: FONT, size: 10, bold: true, color: NAVY };
  outputs.freezePanes.freezeRows(3);
  calculations.freezePanes.freezeRows(3);
  inputs.freezePanes.freezeRows(3);
  setWidths(outputs, { "A:A": 14, "B:B": 36, "C:C": 18, "D:D": 16, "E:E": 18, "F:F": 18 });
  setWidths(calculations, { "A:A": 36, "B:B": 18, "C:C": 16, "D:D": 62 });
  setWidths(inputs, { "A:A": 14, "B:C": 14, "D:D": 11, "E:H": 20, "I:I": 16 });
  setWidths(assumptions, { "A:A": 31, "B:B": 20, "C:C": 48 });
  setWidths(lookups, { "A:A": 20, "B:B": 16, "D:D": 12, "E:E": 22 });
  setWidths(boundary, { "A:A": 28, "B:B": 24, "C:C": 28 });
  setWidths(guide, { "A:A": 30, "B:B": 85, "C:C": 48 });

  workbook.recalculate();
  await renderPreviews(workbook, path.join(outputDir, "case5_previews"), {
    Outputs: `A1:F${lastRow}`,
    Calculations: `A1:D${lastRow}`,
    Inputs: "A1:I11",
    Assumptions: "A1:C20",
    Lookups: "A1:E7",
    "Scope Boundary": "A1:C5",
    "Demo Guide": "A1:C15",
  });

  const workbookPath = path.join(outputDir, "case_5_supported_formula_demonstration.xlsx");
  await exportWorkbook(workbook, workbookPath);
  const authoritativeOutputs = cases.map((_, index) => `Outputs!C${index + 4}`);
  const expected = {
    schema_version: 1,
    case_number: 5,
    case_title: "Supported formula demonstration",
    synthetic_data: true,
    gate1_context: {
      entity: "Aurora Formula Assurance SA",
      period: "2025-Q4",
      currency: "EUR",
      basis: "Synthetic formula control demonstration",
    },
    authoritative_output_cells: authoritativeOutputs,
    formula_cases: cases.map((item, index) => ({
      case_id: item.id,
      cell: `Outputs!C${index + 4}`,
      calculation_cell: `Calculations!B${index + 4}`,
      label: item.label,
      primary_function: item.primary,
      expected_value: item.expected,
      expected_status: "complete",
      expected_verdict: "pass",
    })),
    unsupported_boundary_cells: ["Scope Boundary!B4", "Scope Boundary!B5"],
    expected_finding_count: 0,
    expected_internal_verdict: "pass",
    expected_external_verdict: "pass",
    required_gate_order: [1, 2, 3, 4],
    pdf_available_after_gate4: true,
  };
  const referenceLines = cases.map((item, index) =>
    signedLine(item.label, item.id, item.expected, `Case 5 formula row ${index + 1}`)
  );
  const controlTotal = referenceLines.reduce(
    (total, line) => total + (line.debit_credit === "credit" ? -line.amount : line.amount),
    0
  );
  expected.reference_control_total = excelRound(controlTotal, 6);
  return { workbookPath, expected, referenceLines };
}

function createLcg(seed) {
  let state = seed >>> 0;
  return () => {
    state = (1664525 * state + 1013904223) >>> 0;
    return state;
  };
}

async function buildCase6() {
  const workbook = Workbook.create();
  const summary = workbook.worksheets.add("Impact Summary");
  const bridge = workbook.worksheets.add("Accounting Bridge");
  const assumptions = workbook.worksheets.add("Assumptions");
  const base = workbook.worksheets.add("Base Scenario");
  const adverse = workbook.worksheets.add("Adverse Scenario");
  const aggregation = workbook.worksheets.add("Aggregation");
  const cohortData = workbook.worksheets.add("Cohort Data");
  const controls = workbook.worksheets.add("Controls");
  const guide = workbook.worksheets.add("Demo Guide");

  const cohortCount = 300;
  const seed = 20260924;
  const random = createLcg(seed);
  const products = ["Motor", "Property", "Liability"];
  const regions = ["Belgium", "France", "United Kingdom"];
  const lossRatios = { Motor: 0.58, Property: 0.66, Liability: 0.72 };
  const baseFactors = { Motor: 1.02, Property: 1.04, Liability: 1.06 };
  const adverseFactors = { Motor: 1.12, Property: 1.15, Liability: 1.18 };
  const scenario = {
    baseInflation: 0.02,
    adverseInflation: 0.055,
    baseDiscount: 0.03,
    adverseDiscount: 0.015,
    baseExpense: 0.025,
    adverseExpense: 0.035,
    baseRiskMargin: 0.04,
    adverseRiskMargin: 0.055,
    availableResources: 75000000,
    scr: 35000000,
    currencyDigits: 2,
    ratioDigits: 4,
    reportingUnit: 100000,
    significance: 100,
  };

  const cohorts = [];
  for (let index = 1; index <= cohortCount; index += 1) {
    const product = products[(index - 1) % products.length];
    const region = regions[(index - 1) % regions.length];
    const premium = 60000 + (random() % 191) * 1000;
    const incurred = excelRound(premium * lossRatios[product], 2);
    const paidRatio = 0.52 + (random() % 29) / 100;
    const paid = excelRound(incurred * paidRatio, 2);
    const outstanding = Math.abs(incurred - paid);
    const openingReserve = excelRound(outstanding * (0.9 + (random() % 11) / 100), 2);
    cohorts.push({
      id: `SYN-${String(index).padStart(4, "0")}`,
      product,
      region,
      active: index % 19 !== 0,
      premium,
      incurred,
      paid,
      openingReserve,
    });
  }

  function calculateScenario(cohort, type) {
    if (!cohort.active) {
      return { projected: 0, expense: 0, bel: 0, riskMargin: 0, tp: 0 };
    }
    const isBase = type === "base";
    const factor = isBase ? baseFactors[cohort.product] : adverseFactors[cohort.product];
    const inflation = isBase ? scenario.baseInflation : scenario.adverseInflation;
    const expenseRate = isBase ? scenario.baseExpense : scenario.adverseExpense;
    const discountRate = isBase ? scenario.baseDiscount : scenario.adverseDiscount;
    const riskMarginRate = isBase ? scenario.baseRiskMargin : scenario.adverseRiskMargin;
    const outstanding = Math.abs(cohort.incurred - cohort.paid);
    const projected = excelRound(outstanding * factor * (1 + inflation), 2);
    const expense = excelRound(cohort.premium * expenseRate, 2);
    const bel = excelRound((projected + expense) / (1 + discountRate), 2);
    const riskMargin = excelRound(bel * riskMarginRate, 2);
    return { projected, expense, bel, riskMargin, tp: excelRound(bel + riskMargin, 2) };
  }

  const independent = cohorts.map((cohort) => ({
    cohort,
    base: calculateScenario(cohort, "base"),
    adverse: calculateScenario(cohort, "adverse"),
  }));
  // Sum integer cents so the independent oracle follows financial rounding
  // semantics instead of accumulating JavaScript binary floating-point noise.
  const baseTp = independent.reduce((total, row) => total + Math.round(row.base.tp * 100), 0) / 100;
  const adverseTp = independent.reduce((total, row) => total + Math.round(row.adverse.tp * 100), 0) / 100;
  const tpIncrease = excelRound(adverseTp - baseTp, 2);
  const baseOwnFunds = excelRound(scenario.availableResources - baseTp, 2);
  const adverseOwnFunds = excelRound(scenario.availableResources - adverseTp, 2);
  const ownFundsReduction = excelRound(baseOwnFunds - adverseOwnFunds, 2);
  const baseRatio = excelRound(baseOwnFunds / scenario.scr, scenario.ratioDigits);
  const adverseRatio = excelRound(adverseOwnFunds / scenario.scr, scenario.ratioDigits);
  const ratioDeterioration = excelRound(baseRatio - adverseRatio, scenario.ratioDigits);

  summary.getRange("A1:D1").values = [["Case 6: synthetic reserve stress and solvency impact"]];
  summary.getRange("A2:D2").values = [[
    "A baseline-versus-adverse calculation across 300 fictional cohorts. This is a workflow demonstration, not certified actuarial methodology.",
  ]];
  summary.getRange("A4:D4").values = [["Business result", "Value", "Unit", "Interpretation"]];
  const summaryLabels = [
    ["Baseline technical provisions", "EUR", "Calculated using baseline claims, inflation and discount assumptions."],
    ["Adverse technical provisions", "EUR", "Calculated using adverse claims, inflation and discount assumptions."],
    ["Increase in technical provisions", "EUR", "Additional reserve requirement under the adverse scenario."],
    ["Baseline available own funds", "EUR", "Synthetic resources less baseline technical provisions."],
    ["Adverse available own funds", "EUR", "Synthetic resources less adverse technical provisions."],
    ["Reduction in available own funds", "EUR", "Direct effect of the additional technical provisions."],
    ["Solvency Capital Requirement", "EUR", "Fixed synthetic SCR used for the comparison."],
    ["Baseline solvency ratio", "%", "Baseline available own funds divided by SCR."],
    ["Adverse solvency ratio", "%", "Adverse available own funds divided by SCR."],
    ["Solvency ratio deterioration", "percentage points", "Difference between baseline and adverse ratios."],
  ];
  summary.getRange("A5:A14").values = summaryLabels.map((row) => [row[0]]);
  summary.getRange("B5:B14").formulas = [
    ["=ROUND(SUM('Base Scenario'!$G$4:$G$303),Assumptions!$B$10)"],
    ["=ROUND(SUM('Adverse Scenario'!$G$4:$G$303),Assumptions!$B$10)"],
    ["=ROUND(B6-B5,Assumptions!$B$10)"],
    ["=ROUND(Assumptions!$B$8-B5,Assumptions!$B$10)"],
    ["=ROUND(Assumptions!$B$8-B6,Assumptions!$B$10)"],
    ["=ROUND(B8-B9,Assumptions!$B$10)"],
    ["=Assumptions!$B$9"],
    ["=ROUND(B8/B11,Assumptions!$B$15)"],
    ["=ROUND(B9/B11,Assumptions!$B$15)"],
    ["=ROUND(B12-B13,Assumptions!$B$15)"],
  ];
  summary.getRange("C5:D14").values = summaryLabels.map((row) => [row[1], row[2]]);
  summary.getRange("A16:D16").values = [["Gate 2 accounting outputs", "Cell", "Expected signed value", "Reference"]];
  summary.getRange("A17:D18").values = [
    ["Base technical provisions", "Accounting Bridge!C4", -baseTp, "Synthetic Q4 trial balance account 2200"],
    ["Base available own funds", "Accounting Bridge!C5", baseOwnFunds, "Synthetic Q4 trial balance account 1100"],
  ];

  bridge.getRange("A1:C1").values = [["Accounting outputs for external reconciliation"]];
  bridge.getRange("A2:C2").values = [["Credits are negative and debits are positive. All figures are synthetic."]];
  bridge.getRange("A3:C3").values = [["Account", "Authoritative output", "Signed balance (EUR)"]];
  bridge.getRange("A4:B6").values = [
    ["2200", "Base technical provisions"],
    ["1100", "Base available own funds"],
    ["CTRL", "Signed reference control total"],
  ];
  bridge.getRange("C4:C6").formulas = [
    ["=-'Impact Summary'!B5"],
    ["='Impact Summary'!B8"],
    ["=SUM(C4:C5)"],
  ];

  assumptions.getRange("A1:J1").values = [["Synthetic baseline and adverse assumptions"]];
  assumptions.getRange("A3:D3").values = [["Assumption", "Baseline", "Adverse", "Use"]];
  assumptions.getRange("A4:D13").values = [
    ["Claims inflation", scenario.baseInflation, scenario.adverseInflation, "Projected outstanding claims"],
    ["Discount rate", scenario.baseDiscount, scenario.adverseDiscount, "Present-value calculation"],
    ["Expense loading", scenario.baseExpense, scenario.adverseExpense, "Claims handling expense"],
    ["Risk margin rate", scenario.baseRiskMargin, scenario.adverseRiskMargin, "Synthetic risk margin"],
    ["Resources before technical provisions", scenario.availableResources, null, "Own-funds comparison"],
    ["Solvency Capital Requirement", scenario.scr, null, "Solvency ratio denominator"],
    ["Currency precision", scenario.currencyDigits, null, "ROUND precision"],
    ["Exposure unit", scenario.reportingUnit, null, "INT calculation"],
    ["Reporting significance", scenario.significance, null, "CEILING and FLOOR controls"],
    ["Fallback zero", 0, null, "Inactive cohorts"],
  ];
  assumptions.getRange("A15:C15").values = [["Ratio precision", scenario.ratioDigits, "Displayed ratio calculation"]];
  assumptions.getRange("A16:C16").values = [["Active status", "Active", "Cohort inclusion criterion"]];
  assumptions.getRange("E3:F3").values = [["Formula helper", "Calculated value"]];
  assumptions.getRange("E4:E7").values = [
    ["Baseline inflation multiplier"],
    ["Adverse inflation multiplier"],
    ["Baseline discount divisor"],
    ["Adverse discount divisor"],
  ];
  assumptions.getRange("F4:F7").formulas = [
    ["=1+B4"],
    ["=1+C4"],
    ["=1+B5"],
    ["=1+C5"],
  ];
  assumptions.getRange("H3:J3").values = [["Product", "Baseline factor", "Adverse factor"]];
  assumptions.getRange("H4:J6").values = products.map((product) => [
    product, baseFactors[product], adverseFactors[product],
  ]);

  cohortData.getRange("A1:L1").values = [["Synthetic cohort data and derived lookup fields"]];
  cohortData.getRange("A2:L2").values = [[
    "Aurora General Insurance SA; 2025-Q4; EUR. All 300 cohorts are fictional.",
  ]];
  cohortData.getRange("A3:L3").values = [[
    "Cohort ID", "Product", "Region", "Active", "Premium", "Incurred claims", "Paid claims",
    "Opening reserve", "Product position", "Baseline factor", "Adverse factor", "Outstanding claims",
  ]];
  cohortData.getRange("A4:H303").values = cohorts.map((row) => [
    row.id, row.product, row.region, row.active ? "Active" : "Inactive", row.premium, row.incurred, row.paid, row.openingReserve,
  ]);
  cohortData.getRange("I4:L303").formulas = cohorts.map((_, offset) => {
    const row = offset + 4;
    return [
      `=MATCH(B${row},Assumptions!$H$4:$H$6,0)`,
      `=INDEX(Assumptions!$I$4:$I$6,I${row})`,
      `=VLOOKUP(B${row},Assumptions!$H$4:$J$6,3,FALSE())`,
      `=ABS(F${row}-G${row})`,
    ];
  });

  const scenarioHeaders = [
    "Cohort ID", "Projected outstanding", "Expense", "Undiscounted requirement",
    "Best estimate liability", "Risk margin", "Technical provisions", "Change from opening",
    "Ceiling control", "Floor control", "Rounded up", "Rounded down", "Whole exposure units",
  ];
  for (const [sheet, type] of [[base, "base"], [adverse, "adverse"]]) {
    const title = type === "base" ? "Baseline cohort calculation" : "Adverse cohort calculation";
    sheet.getRange("A1:M1").values = [[title]];
    sheet.getRange("A2:M2").values = [["Amounts in EUR for 2025-Q4"]];
    sheet.getRange("A3:M3").values = [scenarioHeaders];
    sheet.getRange("A4:A303").values = cohorts.map((row) => [row.id]);
    sheet.getRange("B4:M303").formulas = cohorts.map((_, offset) => {
      const row = offset + 4;
      const assumptionColumn = type === "base" ? "B" : "C";
      const factorColumn = type === "base" ? "J" : "K";
      const inflationHelperRow = type === "base" ? 4 : 5;
      const discountHelperRow = type === "base" ? 6 : 7;
      return [
        `=IF('Cohort Data'!D${row}=Assumptions!$B$16,ROUND('Cohort Data'!L${row}*'Cohort Data'!${factorColumn}${row}*Assumptions!$F$${inflationHelperRow},Assumptions!$B$10),Assumptions!$B$13)`,
        `=IF('Cohort Data'!D${row}=Assumptions!$B$16,ROUND('Cohort Data'!E${row}*Assumptions!$${assumptionColumn}$6,Assumptions!$B$10),Assumptions!$B$13)`,
        `=SUM(B${row}:C${row})`,
        `=ROUND(D${row}/Assumptions!$F$${discountHelperRow},Assumptions!$B$10)`,
        `=ROUND(E${row}*Assumptions!$${assumptionColumn}$7,Assumptions!$B$10)`,
        `=SUM(E${row}:F${row})`,
        `=ROUND(G${row}-'Cohort Data'!H${row},Assumptions!$B$10)`,
        `=CEILING(G${row},Assumptions!$B$12)`,
        `=FLOOR(G${row},Assumptions!$B$12)`,
        `=ROUNDUP(G${row},Assumptions!$B$10)`,
        `=ROUNDDOWN(G${row},Assumptions!$B$10)`,
        `=INT('Cohort Data'!E${row}/Assumptions!$B$11)`,
      ];
    });
  }

  aggregation.getRange("A1:J1").values = [["Product-level scenario comparison"]];
  aggregation.getRange("A2:J2").values = [["The portfolio total reconciles to the Impact Summary."]];
  aggregation.getRange("A3:J3").values = [[
    "Product", "Cohort count", "Active count", "Baseline provisions", "Adverse provisions",
    "Average baseline", "Average adverse active", "Minimum adverse", "Maximum adverse", "Increase",
  ]];
  aggregation.getRange("A4:A6").values = products.map((product) => [product]);
  aggregation.getRange("B4:J6").formulas = products.map((_, offset) => {
    const row = offset + 4;
    return [
      `=COUNTIF('Cohort Data'!$B$4:$B$303,A${row})`,
      `=COUNTIFS('Cohort Data'!$B$4:$B$303,A${row},'Cohort Data'!$D$4:$D$303,Assumptions!$B$16)`,
      `=SUMIF('Cohort Data'!$B$4:$B$303,A${row},'Base Scenario'!$G$4:$G$303)`,
      `=SUMIFS('Adverse Scenario'!$G$4:$G$303,'Cohort Data'!$B$4:$B$303,A${row},'Cohort Data'!$D$4:$D$303,Assumptions!$B$16)`,
      `=AVERAGEIF('Cohort Data'!$B$4:$B$303,A${row},'Base Scenario'!$G$4:$G$303)`,
      `=AVERAGEIFS('Adverse Scenario'!$G$4:$G$303,'Cohort Data'!$B$4:$B$303,A${row},'Cohort Data'!$D$4:$D$303,Assumptions!$B$16)`,
      `=_xlfn.MINIFS('Adverse Scenario'!$G$4:$G$303,'Cohort Data'!$B$4:$B$303,A${row},'Cohort Data'!$D$4:$D$303,Assumptions!$B$16)`,
      `=_xlfn.MAXIFS('Adverse Scenario'!$G$4:$G$303,'Cohort Data'!$B$4:$B$303,A${row},'Cohort Data'!$D$4:$D$303,Assumptions!$B$16)`,
      `=E${row}-D${row}`,
    ];
  });
  aggregation.getRange("A8:J8").values = [["Portfolio total", null, null, null, null, null, null, null, null, null]];
  aggregation.getRange("B8:E8").formulas = [["=SUM(B4:B6)", "=SUM(C4:C6)", "=SUM(D4:D6)", "=SUM(E4:E6)"]];
  aggregation.getRange("J8").formulas = [["=SUM(J4:J6)"]];

  controls.getRange("A1:B1").values = [["Independent workbook controls"]];
  controls.getRange("A2:B2").values = [["Every difference below should equal zero."]];
  controls.getRange("A3:B3").values = [["Control", "Difference"]];
  controls.getRange("A4:A7").values = [
    ["Base provision bridge"],
    ["Base own-funds bridge"],
    ["Reserve increase equals own-funds reduction"],
    ["Product aggregation equals adverse total"],
  ];
  controls.getRange("B4:B7").formulas = [
    ["='Accounting Bridge'!C4+'Impact Summary'!B5"],
    ["='Accounting Bridge'!C5-'Impact Summary'!B8"],
    ["=ROUND('Impact Summary'!B7-'Impact Summary'!B10,Assumptions!$B$10)"],
    ["=ROUND(Aggregation!E8-'Impact Summary'!B6,Assumptions!$B$10)"],
  ];

  guide.getRange("A1:C1").values = [["Case 6 demonstration guide"]];
  guide.getRange("A3:C10").values = [
    ["Scope", "Baseline-versus-adverse reserve calculation across 300 synthetic cohorts.", null],
    ["Boundary", "This workbook does not implement or validate certified Solvency II or IFRS 17 methodology.", null],
    ["Gate 1 context", "Aurora General Insurance SA; 2025-Q4; EUR; synthetic reserve stress demonstration.", null],
    ["Gate 2 selection", "Select Accounting Bridge!C4 and Accounting Bridge!C5.", null],
    ["Gate 3", "Approve both one-to-one mappings and choose internal and external materiality thresholds.", null],
    ["Gate 4", "Create the named approval record before generating the PDF.", null],
    ["Baseline", "The baseline technical provisions and own funds reconcile to the synthetic trial balance.", null],
    ["Adverse impact", "The adverse scenario raises technical provisions, reduces own funds and lowers the solvency ratio.", null],
  ];
  guide.getRange("A12:C12").values = [["Business impact", "Expected value", "Cell"]];
  guide.getRange("A13:C18").values = [
    ["Baseline technical provisions", baseTp, "Impact Summary!B5"],
    ["Adverse technical provisions", adverseTp, "Impact Summary!B6"],
    ["Increase in technical provisions", tpIncrease, "Impact Summary!B7"],
    ["Reduction in available own funds", ownFundsReduction, "Impact Summary!B10"],
    ["Baseline solvency ratio", baseRatio, "Impact Summary!B12"],
    ["Solvency ratio deterioration", ratioDeterioration, "Impact Summary!B14"],
  ];

  applyBaseStyle(workbook);
  for (const [sheet, titleRange] of [
    [summary, "A1:D1"], [bridge, "A1:C1"], [assumptions, "A1:J1"], [base, "A1:M1"],
    [adverse, "A1:M1"], [aggregation, "A1:J1"], [cohortData, "A1:L1"],
    [controls, "A1:B1"], [guide, "A1:C1"],
  ]) styleTitle(sheet, titleRange);
  styleHeader(summary, "A4:D4");
  styleHeader(summary, "A16:D16");
  styleHeader(bridge, "A3:C3");
  styleHeader(assumptions, "A3:D3");
  styleHeader(assumptions, "E3:F3");
  styleHeader(assumptions, "H3:J3");
  styleHeader(base, "A3:M3");
  styleHeader(adverse, "A3:M3");
  styleHeader(aggregation, "A3:J3");
  styleHeader(cohortData, "A3:L3");
  styleHeader(controls, "A3:B3");
  styleHeader(guide, "A12:C12");
  styleSection(summary, "A7:D7");
  styleSection(summary, "A10:D10");
  styleSection(summary, "A14:D14");
  summary.getRange("B5:B11").format.numberFormat = currencyFormat;
  summary.getRange("B12:B14").format.numberFormat = precisePercentageFormat;
  summary.getRange("B5:B14").format.font = { name: FONT, size: 10, bold: true, color: DARK };
  summary.getRange("C17:C18").format.numberFormat = currencyFormat;
  bridge.getRange("C4:C6").format.numberFormat = currencyFormat;
  assumptions.getRange("B4:C13").format.fill = INPUT_YELLOW;
  assumptions.getRange("B4:C13").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  assumptions.getRange("B4:C7").format.numberFormat = percentageFormat;
  assumptions.getRange("B8:B9").format.numberFormat = currencyFormat;
  assumptions.getRange("B15").format.fill = INPUT_YELLOW;
  assumptions.getRange("B15").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  assumptions.getRange("B16").format.fill = INPUT_YELLOW;
  assumptions.getRange("B16").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  assumptions.getRange("F4:F7").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  assumptions.getRange("F4:F7").format.numberFormat = percentageFormat;
  assumptions.getRange("I4:J6").format.fill = INPUT_YELLOW;
  assumptions.getRange("I4:J6").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  for (const sheet of [base, adverse]) {
    sheet.getRange("B4:L303").format.numberFormat = currencyFormat;
    sheet.getRange("B4:M303").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
    sheet.freezePanes.freezeRows(3);
    sheet.freezePanes.freezeColumns(1);
  }
  cohortData.getRange("E4:H303").format.numberFormat = currencyFormat;
  cohortData.getRange("I4:L303").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  cohortData.freezePanes.freezeRows(3);
  cohortData.freezePanes.freezeColumns(1);
  aggregation.getRange("D4:J8").format.numberFormat = currencyFormat;
  aggregation.getRange("A8:J8").format.font = { name: FONT, size: 10, bold: true, color: DARK };
  controls.getRange("B4:B7").format.numberFormat = currencyFormat;
  guide.getRange("B13:B16").format.numberFormat = currencyFormat;
  guide.getRange("B17:B18").format.numberFormat = precisePercentageFormat;
  summary.freezePanes.freezeRows(4);
  aggregation.freezePanes.freezeRows(3);
  setWidths(summary, { "A:A": 36, "B:B": 20, "C:C": 18, "D:D": 70 });
  setWidths(bridge, { "A:A": 16, "B:B": 34, "C:C": 22 });
  setWidths(assumptions, { "A:A": 36, "B:C": 18, "D:D": 34, "E:E": 30, "F:F": 18, "H:H": 18, "I:J": 18 });
  setWidths(base, { "A:A": 14, "B:L": 22, "M:M": 19 });
  setWidths(adverse, { "A:A": 14, "B:L": 22, "M:M": 19 });
  setWidths(aggregation, { "A:A": 18, "B:J": 20 });
  setWidths(cohortData, { "A:A": 14, "B:C": 18, "D:D": 11, "E:H": 18, "I:L": 18 });
  setWidths(controls, { "A:A": 46, "B:B": 22 });
  setWidths(guide, { "A:A": 34, "B:B": 86, "C:C": 24 });

  workbook.recalculate();
  await renderPreviews(workbook, path.join(outputDir, "case6_previews"), {
    "Impact Summary": "A1:D18",
    "Accounting Bridge": "A1:C6",
    Assumptions: "A1:J16",
    "Base Scenario": "A1:M20",
    "Adverse Scenario": "A1:M20",
    Aggregation: "A1:J8",
    "Cohort Data": "A1:L20",
    Controls: "A1:B7",
    "Demo Guide": "A1:C18",
  });

  const workbookPath = path.join(outputDir, "case_6_reserve_stress_business_impact.xlsx");
  await exportWorkbook(workbook, workbookPath);
  const expected = {
    schema_version: 1,
    case_number: 6,
    case_title: "Reserve stress and solvency impact",
    synthetic_data: true,
    seed,
    cohort_count: cohortCount,
    gate1_context: {
      entity: "Aurora General Insurance SA",
      period: "2025-Q4",
      currency: "EUR",
      basis: "Synthetic reserve stress demonstration",
    },
    authoritative_output_cells: ["Accounting Bridge!C4", "Accounting Bridge!C5"],
    business_impact: {
      baseline_technical_provisions: { cell: "Impact Summary!B5", expected_value: baseTp },
      adverse_technical_provisions: { cell: "Impact Summary!B6", expected_value: adverseTp },
      technical_provisions_increase: { cell: "Impact Summary!B7", expected_value: tpIncrease },
      baseline_available_own_funds: { cell: "Impact Summary!B8", expected_value: baseOwnFunds },
      adverse_available_own_funds: { cell: "Impact Summary!B9", expected_value: adverseOwnFunds },
      available_own_funds_reduction: { cell: "Impact Summary!B10", expected_value: ownFundsReduction },
      solvency_capital_requirement: { cell: "Impact Summary!B11", expected_value: scenario.scr },
      baseline_solvency_ratio: { cell: "Impact Summary!B12", expected_value: baseRatio },
      adverse_solvency_ratio: { cell: "Impact Summary!B13", expected_value: adverseRatio },
      solvency_ratio_deterioration: { cell: "Impact Summary!B14", expected_value: ratioDeterioration },
    },
    expected_finding_count: 0,
    minimum_formula_cell_count: 3000,
    expected_internal_verdict: "pass",
    expected_external_verdict: "pass",
    required_gate_order: [1, 2, 3, 4],
    pdf_available_after_gate4: true,
    limitation: "Synthetic workflow demonstration only; not certified Solvency II or IFRS 17 methodology.",
  };
  const referenceLines = [
    {
      account_number: "2200",
      label: "Base technical provisions",
      amount: baseTp,
      debit_credit: "credit",
      ledger_source: "Synthetic Q4 2025 trial balance",
      evidence_reference: "Case 6 account 2200",
    },
    {
      account_number: "1100",
      label: "Base available own funds",
      amount: baseOwnFunds,
      debit_credit: "debit",
      ledger_source: "Synthetic Q4 2025 trial balance",
      evidence_reference: "Case 6 account 1100",
    },
  ];
  expected.reference_control_total = excelRound(baseOwnFunds - baseTp, 2);
  return { workbookPath, expected, referenceLines };
}

await fs.mkdir(outputDir, { recursive: true });
const case5 = await buildCase5();
const case6 = await buildCase6();
await fs.writeFile(
  path.join(outputDir, "case_5_builder_metadata.json"),
  JSON.stringify(case5, null, 2) + "\n",
  "utf8"
);
await fs.writeFile(
  path.join(outputDir, "case_6_builder_metadata.json"),
  JSON.stringify(case6, null, 2) + "\n",
  "utf8"
);
console.log(JSON.stringify({
  case5: case5.workbookPath,
  case6: case6.workbookPath,
  supportedFunctions,
}, null, 2));
