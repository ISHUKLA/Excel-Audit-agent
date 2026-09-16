#!/usr/bin/env node
/** Build the competition demonstration workbooks with @oai/artifact-tool.
 *
 * This script creates deterministic workbook content only. A separate Python
 * finaliser recalculates each workbook with LibreOffice, verifies it through
 * the production parser/reconciliation path, and writes execution evidence.
 */

import fs from "node:fs/promises";
import path from "node:path";

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

const outputDir = args.get("--output-dir");
const requestedCases = (args.get("--cases") || "7,8")
  .split(",")
  .map((item) => Number(item.trim()))
  .filter(Number.isFinite);
if (!outputDir || requestedCases.length === 0) {
  throw new Error("--output-dir and --cases are required");
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
const LIGHT_BLUE = "#EAF2F8";
const INPUT_YELLOW = "#FFF2CC";
const WARNING_RED = "#FCE4D6";
const WHITE = "#FFFFFF";
const DARK = "#1F2937";
const FORMULA_GREEN = "#008000";
const INPUT_BLUE = "#0000FF";
const BORDER = "#B7C9D6";
const currencyFormat = '€#,##0.0,,"m";[Red](€#,##0.0,,"m");-';
const percentageFormat = "0.0%";

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
    await fs.writeFile(
      path.join(folder, `${sheetName.replaceAll(" ", "_")}.png`),
      new Uint8Array(await preview.arrayBuffer())
    );
  }
}

async function exportWorkbook(workbook, outputPath) {
  workbook.recalculate();
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
}

function styleCase7(workbook) {
  applyBaseStyle(workbook);
  const guide = workbook.worksheets.getItem("Case Guide");
  const context = workbook.worksheets.getItem("Context");
  const assumptions = workbook.worksheets.getItem("Assumptions");
  const cohorts = workbook.worksheets.getItem("Cohort Data");
  const cashFlows = workbook.worksheets.getItem("Cash Flow Calculation");
  const summary = workbook.worksheets.getItem("Reserve Summary");
  const bridge = workbook.worksheets.getItem("Accounting Bridge");

  for (const [sheet, range] of [
    [guide, "A1:C1"], [context, "A1:B1"], [assumptions, "A1:F1"],
    [cohorts, "A1:F1"], [cashFlows, "A1:E1"], [summary, "A1:B1"],
    [bridge, "A1:C1"],
  ]) styleTitle(sheet, range);
  styleHeader(guide, "A10:C10");
  styleHeader(context, "A3:B3");
  styleHeader(assumptions, "A3:B3");
  styleHeader(assumptions, "E3:F3");
  styleHeader(cohorts, "A3:F3");
  styleHeader(cashFlows, "A3:E3");
  styleHeader(summary, "A4:B4");
  styleHeader(bridge, "A3:C3");
  styleSection(summary, "A8:B8");
  styleSection(summary, "A11:B11");

  assumptions.getRange("B4:B8").format.fill = INPUT_YELLOW;
  assumptions.getRange("B4:B8").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  assumptions.getRange("F4").format.fill = INPUT_YELLOW;
  assumptions.getRange("F4").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  cohorts.getRange("C4:F9").format.fill = INPUT_YELLOW;
  cohorts.getRange("C4:F9").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  cashFlows.getRange("B4:E9").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  summary.getRange("B5:B11").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  bridge.getRange("B4:B5").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  for (const range of ["B4:B5", "C4:E9", "B5:B11", "B4:B5"]) {
    // Applied to the owning sheets below; the shared format is kept consistent.
  }
  assumptions.getRange("B4:B5").format.numberFormat = currencyFormat;
  cohorts.getRange("C4:E9").format.numberFormat = currencyFormat;
  cashFlows.getRange("B4:E9").format.numberFormat = currencyFormat;
  summary.getRange("B5:B11").format.numberFormat = currencyFormat;
  bridge.getRange("B4:B5").format.numberFormat = currencyFormat;

  cohorts.freezePanes.freezeRows(3);
  cashFlows.freezePanes.freezeRows(3);
  setWidths(guide, { "A:A": 29, "B:B": 84, "C:C": 29 });
  setWidths(context, { "A:A": 22, "B:B": 52 });
  setWidths(assumptions, { "A:A": 34, "B:B": 19, "C:C": 4, "E:E": 19, "F:F": 16 });
  setWidths(cohorts, { "A:A": 16, "B:B": 18, "C:E": 22, "F:F": 12 });
  setWidths(cashFlows, { "A:A": 16, "B:E": 24 });
  setWidths(summary, { "A:A": 38, "B:B": 22 });
  setWidths(bridge, { "A:A": 36, "B:B": 22, "C:C": 42 });
}

async function buildCase7(defective) {
  const workbook = Workbook.create();
  const guide = workbook.worksheets.add("Case Guide");
  const context = workbook.worksheets.add("Context");
  const assumptions = workbook.worksheets.add("Assumptions");
  const cohorts = workbook.worksheets.add("Cohort Data");
  const cashFlows = workbook.worksheets.add("Cash Flow Calculation");
  const summary = workbook.worksheets.add("Reserve Summary");
  const bridge = workbook.worksheets.add("Accounting Bridge");

  const variant = defective ? "7b: missing IFRS 17 cohort" : "7a: clean IFRS 17 cohort";
  const expectedClosing = defective ? 122_200_000 : 128_400_000;
  guide.getRange("A1").values = [[`Case ${variant}`]];
  guide.getRange("A3:B8").values = [
    ["Business problem", "Synthetic IFRS 17 fulfilment-cash-flow aggregation and accounting bridge."],
    ["Decision", "A human reviewer decides whether the workbook evidence supports continuing the reporting workflow."],
    ["Gate 2 output", "Accounting Bridge!B4 (signed closing liability)."],
    ["Planted issue", defective ? "The claims aggregation stops before the final populated cohort, excluding EUR 6.2 million." : "None. This is the clean control case."],
    ["Expected internal result", "Complete and pass because Python reconstructs the workbook formula as written."],
    ["Expected external result", defective ? "Block below the EUR 6.2 million difference using human-entered thresholds." : "Pass after the proposed mapping is reviewed and approved."],
  ];
  guide.getRange("A10:C10").values = [["Manual check", "Expected value", "Location"]];
  guide.getRange("A11:C15").values = [
    ["Present value of claims", defective ? 98_000_000 : 104_200_000, "Reserve Summary!B5"],
    ["Fulfilment cash flows", defective ? 92_200_000 : 98_400_000, "Reserve Summary!B8"],
    ["Closing liability", expectedClosing, "Reserve Summary!B11"],
    ["Accounting output", -expectedClosing, "Accounting Bridge!B4"],
    ["Scope boundary", "Spreadsheet translation and reconciliation only", "No actuarial-methodology validation"],
  ];
  guide.getRange("B11:B14").format.numberFormat = currencyFormat;

  context.getRange("A1").values = [["Confirmed workbook context"]];
  context.getRange("A3:B3").values = [["Field", "Workbook value"]];
  context.getRange("A4:B8").values = [
    ["Entity", "Glass Box Life Belgium SA"],
    ["Period", "31 December 2025"],
    ["Currency", "EUR"],
    ["Basis", "Synthetic IFRS 17 reporting demonstration"],
    ["Data status", "Entirely synthetic"],
  ];

  assumptions.getRange("A1").values = [["Synthetic calculation assumptions"]];
  assumptions.getRange("A3:B3").values = [["Assumption", "Value"]];
  assumptions.getRange("A4:B8").values = [
    ["Risk adjustment", 7_800_000],
    ["Contractual service margin", 22_200_000],
    ["Currency rounding digits", 0],
    ["Active cohort status", "Active"],
    ["Included flag", 1],
  ];
  assumptions.getRange("E3:F3").values = [["Status", "Claim factor"]];
  assumptions.getRange("E4:F4").values = [["Active", 1]];

  cohorts.getRange("A1").values = [["Synthetic IFRS 17 cohort data"]];
  cohorts.getRange("A2").values = [["All amounts are fictional and stated in EUR."]];
  cohorts.getRange("A3:F3").values = [[
    "Cohort ID", "Status", "Claims PV", "Expenses PV", "Premiums PV", "Included",
  ]];
  cohorts.getRange("A4:F9").values = [
    ["C-2020", "Active", 18_000_000, 2_000_000, -3_000_000, 1],
    ["C-2021", "Active", 20_000_000, 2_100_000, -3_000_000, 1],
    ["C-2022", "Active", 17_000_000, 2_100_000, -3_000_000, 1],
    ["C-2023", "Active", 21_000_000, 2_100_000, -3_000_000, 1],
    ["C-2024", "Active", 22_000_000, 2_100_000, -3_000_000, 1],
    ["C-2025", "Active", 6_200_000, 2_200_000, -3_400_000, 1],
  ];

  cashFlows.getRange("A1").values = [["Cohort cash-flow calculation"]];
  cashFlows.getRange("A2").values = [["The final populated cohort contributes EUR 6.2 million to claims present value."]];
  cashFlows.getRange("A3:E3").values = [[
    "Cohort ID", "Claims PV", "Expenses PV", "Premiums PV", "Fulfilment cash flow",
  ]];
  cashFlows.getRange("A4:A9").values = [
    ["C-2020"], ["C-2021"], ["C-2022"], ["C-2023"], ["C-2024"], ["C-2025"],
  ];
  cashFlows.getRange("B4:E9").formulas = Array.from({ length: 6 }, (_, offset) => {
    const row = offset + 4;
    return [
      `=IF('Cohort Data'!$F${row}=Assumptions!$B$8,ROUND('Cohort Data'!$C${row}*VLOOKUP('Cohort Data'!$B${row},Assumptions!$E$4:$F$4,2,FALSE()),Assumptions!$B$6),0)`,
      `=ROUND('Cohort Data'!$D${row},Assumptions!$B$6)`,
      `=ROUND('Cohort Data'!$E${row},Assumptions!$B$6)`,
      `=SUM(B${row}:D${row})`,
    ];
  });

  summary.getRange("A1").values = [["IFRS 17 reserve summary"]];
  summary.getRange("A2").values = [[defective
    ? "Workbook calculation as written: final cohort omitted from claims aggregation."
    : "Clean calculation including every populated cohort."]];
  summary.getRange("A4:B4").values = [["Component", "Amount (EUR)"]];
  summary.getRange("A5:A11").values = [
    ["Present value of claims"],
    ["Present value of expenses"],
    ["Present value of premiums"],
    ["Fulfilment cash flows"],
    ["Risk adjustment"],
    ["Contractual service margin"],
    ["Closing liability"],
  ];
  summary.getRange("B5:B11").formulas = [
    [defective
      ? "=SUM('Cash Flow Calculation'!$B$4:$B$8)"
      : "=SUM('Cash Flow Calculation'!$B$4:$B$9)"],
    ["=SUMIFS('Cash Flow Calculation'!$C$4:$C$9,'Cohort Data'!$F$4:$F$9,Assumptions!$B$8)"],
    ["=SUM('Cash Flow Calculation'!$D$4:$D$9)"],
    ["=SUM(B5:B7)"],
    ["=Assumptions!$B$4"],
    ["=Assumptions!$B$5"],
    ["=SUM(B8:B10)"],
  ];

  bridge.getRange("A1").values = [["Accounting bridge"]];
  bridge.getRange("A2").values = [["Credit balances are negative for reconciliation. All figures are synthetic."]];
  bridge.getRange("A3:C3").values = [["Authoritative output", "Signed balance (EUR)", "Use"]];
  bridge.getRange("A4:A5").values = [["Closing liability"], ["Closing liability magnitude"]];
  bridge.getRange("B4:B5").formulas = [["=-'Reserve Summary'!B11"], ["='Reserve Summary'!B11"]];
  bridge.getRange("C4:C5").values = [["Designate this monetary output at Gate 2"], ["Display only"]];

  styleCase7(workbook);
  if (defective) {
    summary.getRange("B5").format.fill = WARNING_RED;
  }
  const stem = defective ? "case_7b_ifrs17_missing_cohort" : "case_7a_ifrs17_clean";
  await renderPreviews(workbook, path.join(outputDir, `${stem}_previews`), {
    "Case Guide": "A1:C15",
    Context: "A1:B8",
    Assumptions: "A1:F8",
    "Cohort Data": "A1:F9",
    "Cash Flow Calculation": "A1:E9",
    "Reserve Summary": "A1:B11",
    "Accounting Bridge": "A1:C5",
  });
  const workbookPath = path.join(outputDir, `${stem}.xlsx`);
  await exportWorkbook(workbook, workbookPath);
  return {
    workbookPath,
    caseId: defective ? "case_7b_ifrs17_missing_cohort" : "case_7a_ifrs17_clean",
    caseVersion: "1.0.0",
    expected: {
      gate1_context: {
        entity: "Glass Box Life Belgium SA",
        period: "31 December 2025",
        currency: "EUR",
        basis: "Synthetic IFRS 17 reporting demonstration",
      },
      authoritative_output_cells: ["Accounting Bridge!B4"],
      summary_outputs: {
        present_value_claims: { cell: "Reserve Summary!B5", expected_value: defective ? 98_000_000 : 104_200_000 },
        present_value_expenses: { cell: "Reserve Summary!B6", expected_value: 12_600_000 },
        present_value_premiums: { cell: "Reserve Summary!B7", expected_value: -18_400_000 },
        fulfilment_cash_flows: { cell: "Reserve Summary!B8", expected_value: defective ? 92_200_000 : 98_400_000 },
        risk_adjustment: { cell: "Reserve Summary!B9", expected_value: 7_800_000 },
        contractual_service_margin: { cell: "Reserve Summary!B10", expected_value: 22_200_000 },
        closing_liability: { cell: "Reserve Summary!B11", expected_value: expectedClosing },
        signed_closing_liability: { cell: "Accounting Bridge!B4", expected_value: -expectedClosing },
      },
      expected_finding: defective ? {
        category: "adjacent_populated_row_omitted",
        formula_cell: "Reserve Summary!B5",
        omitted_cell: "Cash Flow Calculation!B9",
      } : null,
      omitted_cohort_amount: defective ? 6_200_000 : 0,
      expected_internal_verdict: "pass",
      expected_external_verdict: defective ? "block" : "pass",
      expected_gate_block: defective ? 3 : null,
      pdf_available_after_gate4: !defective,
    },
    referenceLines: [{
      account_number: "LRC-IFRS17",
      label: "Closing liability",
      amount: 128_400_000,
      debit_credit: "credit",
      ledger_source: "Synthetic IFRS 17 accounting extract",
      evidence_reference: defective ? "Case 7b authoritative accounting row" : "Case 7a authoritative accounting row",
    }],
    referenceControlTotal: -128_400_000,
  };
}

async function buildCase8() {
  const workbook = Workbook.create();
  const guide = workbook.worksheets.add("Case Guide");
  const context = workbook.worksheets.add("Context");
  const assumptions = workbook.worksheets.add("Assumptions");
  const ownFunds = workbook.worksheets.add("Own Funds");
  const modules = workbook.worksheets.add("SCR Modules");
  const diversification = workbook.worksheets.add("Diversification");
  const summary = workbook.worksheets.add("Capital Summary");
  const bridge = workbook.worksheets.add("QRT Bridge");

  guide.getRange("A1").values = [["Case 8: Solvency II capital and dividend decision"]];
  guide.getRange("A3:B9").values = [
    ["Business problem", "Synthetic Solvency II capital summary used in a management dividend discussion."],
    ["Management floor", "150% internal capital-management floor."],
    ["Apparent decision", "The defective 162.2% ratio appears to support considering a EUR 10 million dividend."],
    ["Correct comparison", "The approved-basis 146.3% ratio is below the management floor."],
    ["Gate 2 output", "QRT Bridge!B4 (Final SCR only)."],
    ["Boundary", "Glass Box does not decide whether a dividend should be paid."],
    ["Methodology boundary", "Glass Box does not validate Solvency II methodology."],
  ];
  guide.getRange("A11:C11").values = [["Manual check", "Expected value", "Location"]];
  guide.getRange("A12:C16").values = [
    ["Approved Final SCR", 123_000_000, "Capital Summary!B9"],
    ["Workbook Final SCR", 111_000_000, "Capital Summary!C9"],
    ["Approved-basis ratio", 1.463, "Capital Summary!B10"],
    ["Workbook ratio", 1.622, "Capital Summary!C10"],
    ["External difference", 12_000_000, "Approved reference minus workbook SCR"],
  ];

  context.getRange("A1").values = [["Confirmed workbook context"]];
  context.getRange("A3:B3").values = [["Field", "Workbook value"]];
  context.getRange("A4:B8").values = [
    ["Entity", "Glass Box Life Belgium SA"],
    ["Period", "31 December 2025"],
    ["Currency", "EUR"],
    ["Basis", "Synthetic Solvency II capital demonstration"],
    ["Data status", "Entirely synthetic"],
  ];

  assumptions.getRange("A1").values = [["Approved capital assumptions"]];
  assumptions.getRange("A3:B3").values = [["Assumption", "Approved value"]];
  assumptions.getRange("A4:B8").values = [
    ["Diversification benefit", 35_000_000],
    ["Operational risk", 8_000_000],
    ["Internal capital-management floor", 1.5],
    ["Illustrative dividend", 10_000_000],
    ["Included status", "Included"],
  ];
  assumptions.getRange("E3:F3").values = [["SCR module", "Approved factor"]];
  assumptions.getRange("E4:F7").values = [
    ["Market", 1], ["Life underwriting", 1], ["Counterparty", 1], ["Other", 1],
  ];

  ownFunds.getRange("A1").values = [["Eligible own funds"]];
  ownFunds.getRange("A3:C3").values = [["Component", "Amount (EUR)", "Eligibility"]];
  ownFunds.getRange("A4:C5").values = [
    ["Share capital and reserves", 100_000_000, "Included"],
    ["Retained earnings", 80_000_000, "Included"],
  ];
  ownFunds.getRange("A7").values = [["Eligible own funds"]];
  ownFunds.getRange("B7").formulas = [["=SUM(B4:B5)"]];

  modules.getRange("A1").values = [["SCR modules before diversification"]];
  modules.getRange("A3:E3").values = [["Module", "Base SCR", "Approved factor", "Adjusted SCR", "Status"]];
  modules.getRange("A4:B7").values = [
    ["Market", 60_000_000],
    ["Life underwriting", 50_000_000],
    ["Counterparty", 25_000_000],
    ["Other", 15_000_000],
  ];
  modules.getRange("C4:D7").formulas = Array.from({ length: 4 }, (_, offset) => {
    const row = offset + 4;
    return [
      `=VLOOKUP(A${row},Assumptions!$E$4:$F$7,2,FALSE())`,
      `=ROUND(B${row}*C${row},0)`,
    ];
  });
  modules.getRange("E4:E7").values = [["Included"], ["Included"], ["Included"], ["Included"]];
  modules.getRange("A9").values = [["SCR before diversification"]];
  modules.getRange("D9").formulas = [["=SUMIFS(D4:D7,E4:E7,Assumptions!$B$8)"]];

  diversification.getRange("A1").values = [["Diversification comparison"]];
  diversification.getRange("A3:B3").values = [["Measure", "Amount (EUR)"]];
  diversification.getRange("A4:B5").values = [
    ["Approved diversification benefit", 35_000_000],
    ["Amount embedded in workbook formula", 47_000_000],
  ];

  summary.getRange("A1").values = [["Solvency capital summary"]];
  summary.getRange("A2").values = [["The two columns must remain distinct: approved basis versus workbook calculation."]];
  summary.getRange("A4:C4").values = [["Metric", "Approved basis", "Workbook calculation"]];
  summary.getRange("A5:A12").values = [
    ["Eligible own funds"], ["SCR before diversification"], ["Diversification benefit"],
    ["Operational risk"], ["Final SCR"], ["Solvency ratio"],
    ["Internal capital-management floor"], ["Management indicator"],
  ];
  summary.getRange("B5:C12").formulas = [
    ["='Own Funds'!B7", "='Own Funds'!B7"],
    ["='SCR Modules'!D9", "='SCR Modules'!D9"],
    ["=-Assumptions!$B$4", "=-Diversification!$B$5"],
    ["=Assumptions!$B$5", "=Assumptions!$B$5"],
    ["=ROUND(B6-Assumptions!$B$4+B8,0)", "=ROUND(C6-47000000+C8,0)"],
    ["=ROUND(B5/B9,3)", "=ROUND(C5/C9,3)"],
    ["=Assumptions!$B$6", "=Assumptions!$B$6"],
    ["=IF(B10>=B11,\"Dividend review may be considered\",\"Below management floor\")", "=IF(C10>=C11,\"Dividend review may be considered\",\"Below management floor\")"],
  ];

  bridge.getRange("A1").values = [["Approved-capital reconciliation bridge"]];
  bridge.getRange("A2").values = [["Only the monetary Final SCR is designated. The solvency ratio is a management metric."]];
  bridge.getRange("A3:C3").values = [["Authoritative output", "Workbook amount (EUR)", "Reference basis"]];
  bridge.getRange("A4:C4").values = [["Final SCR", null, "Approved capital result: EUR 123.0 million"]];
  bridge.getRange("B4").formulas = [["='Capital Summary'!C9"]];

  applyBaseStyle(workbook);
  for (const [sheet, range] of [
    [guide, "A1:C1"], [context, "A1:B1"], [assumptions, "A1:F1"],
    [ownFunds, "A1:C1"], [modules, "A1:E1"], [diversification, "A1:B1"],
    [summary, "A1:C1"], [bridge, "A1:C1"],
  ]) styleTitle(sheet, range);
  for (const [sheet, range] of [
    [guide, "A11:C11"], [context, "A3:B3"], [assumptions, "A3:B3"],
    [assumptions, "E3:F3"], [ownFunds, "A3:C3"], [modules, "A3:E3"],
    [diversification, "A3:B3"], [summary, "A4:C4"], [bridge, "A3:C3"],
  ]) styleHeader(sheet, range);
  styleSection(ownFunds, "A7:C7");
  styleSection(modules, "A9:E9");
  styleSection(summary, "A9:C9");
  assumptions.getRange("B4:B8").format.fill = INPUT_YELLOW;
  assumptions.getRange("B4:B8").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  assumptions.getRange("F4:F7").format.fill = INPUT_YELLOW;
  assumptions.getRange("F4:F7").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  ownFunds.getRange("B4:B5").format.fill = INPUT_YELLOW;
  ownFunds.getRange("B4:B5").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  modules.getRange("B4:B7").format.fill = INPUT_YELLOW;
  modules.getRange("B4:B7").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  modules.getRange("C4:E7").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  summary.getRange("B5:C12").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  summary.getRange("C9").format.fill = WARNING_RED;
  bridge.getRange("B4").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  for (const [sheet, range] of [
    [guide, "B12:B13"], [guide, "B16"], [assumptions, "B4:B5"],
    [assumptions, "B7"], [ownFunds, "B4:B7"], [modules, "B4:D9"],
    [diversification, "B4:B5"], [summary, "B5:C9"], [bridge, "B4"],
  ]) sheet.getRange(range).format.numberFormat = currencyFormat;
  guide.getRange("B14:B15").format.numberFormat = percentageFormat;
  assumptions.getRange("B6").format.numberFormat = percentageFormat;
  summary.getRange("B10:C11").format.numberFormat = percentageFormat;
  modules.freezePanes.freezeRows(3);
  setWidths(guide, { "A:A": 30, "B:B": 88, "C:C": 30 });
  setWidths(context, { "A:A": 22, "B:B": 50 });
  setWidths(assumptions, { "A:A": 38, "B:B": 19, "E:E": 22, "F:F": 17 });
  setWidths(ownFunds, { "A:A": 34, "B:B": 20, "C:C": 18 });
  setWidths(modules, { "A:A": 24, "B:D": 19, "E:E": 14 });
  setWidths(diversification, { "A:A": 38, "B:B": 22 });
  setWidths(summary, { "A:A": 42, "B:C": 23 });
  setWidths(bridge, { "A:A": 28, "B:B": 24, "C:C": 46 });

  await renderPreviews(workbook, path.join(outputDir, "case_8_previews"), {
    "Case Guide": "A1:C16",
    Context: "A1:B8",
    Assumptions: "A1:F8",
    "Own Funds": "A1:C7",
    "SCR Modules": "A1:E9",
    Diversification: "A1:B5",
    "Capital Summary": "A1:C12",
    "QRT Bridge": "A1:C4",
  });
  const workbookPath = path.join(outputDir, "case_8_solvency_capital_decision.xlsx");
  await exportWorkbook(workbook, workbookPath);
  return {
    workbookPath,
    caseId: "case_8_solvency_capital",
    caseVersion: "1.0.0",
    expected: {
      gate1_context: {
        entity: "Glass Box Life Belgium SA",
        period: "31 December 2025",
        currency: "EUR",
        basis: "Synthetic Solvency II capital demonstration",
      },
      authoritative_output_cells: ["QRT Bridge!B4"],
      summary_outputs: {
        eligible_own_funds: { cell: "Capital Summary!C5", expected_value: 180_000_000 },
        scr_before_diversification: { cell: "Capital Summary!C6", expected_value: 150_000_000 },
        defective_diversification_benefit: { cell: "Capital Summary!C7", expected_value: -47_000_000 },
        approved_diversification_benefit: { cell: "Capital Summary!B7", expected_value: -35_000_000 },
        operational_risk: { cell: "Capital Summary!C8", expected_value: 8_000_000 },
        defective_final_scr: { cell: "Capital Summary!C9", expected_value: 111_000_000 },
        approved_final_scr: { cell: "Capital Summary!B9", expected_value: 123_000_000 },
        defective_solvency_ratio: { cell: "Capital Summary!C10", expected_value: 1.622 },
        approved_solvency_ratio: { cell: "Capital Summary!B10", expected_value: 1.463 },
        authoritative_final_scr: { cell: "QRT Bridge!B4", expected_value: 111_000_000 },
      },
      expected_finding: {
        category: "hardcoded_numeric_assumption",
        formula_cell: "Capital Summary!C9",
        literal: "47000000",
      },
      external_difference: 12_000_000,
      expected_internal_verdict: "pass",
      expected_external_verdict: "block",
      expected_gate_block: 3,
      pdf_available_after_gate4: false,
    },
    referenceLines: [{
      account_number: "S.23.01.01.01",
      label: "Final SCR",
      amount: 123_000_000,
      debit_credit: "debit",
      ledger_source: "Synthetic approved-capital extract",
      evidence_reference: "Case 8 approved Final SCR",
    }],
    referenceControlTotal: 123_000_000,
  };
}

async function buildCase9() {
  const workbook = Workbook.create();
  const guide = workbook.worksheets.add("Case Guide");
  const context = workbook.worksheets.add("Context");
  const assumptions = workbook.worksheets.add("Product Assumptions");
  const projection = workbook.worksheets.add("Policy Projection");
  const cashFlows = workbook.worksheets.add("Cash Flows");
  const summary = workbook.worksheets.add("Profitability Summary");
  const independent = workbook.worksheets.add("Independent Pricing Reference");

  guide.getRange("A1").values = [["Case 9: life pricing with an incomplete reconstruction"]];
  guide.getRange("A3:B11").values = [
    ["Business problem", "Synthetic protection-product pricing projection used for a launch discussion."],
    ["Workbook result", "The workbook displays a EUR 4.6 million present-value margin."],
    ["Independent context", "A separately controlled pricing result is negative EUR 4.8 million, an illustrative EUR 9.4 million swing."],
    ["Gate 2 output", "Profitability Summary!B8 (designated profitability result)."],
    ["Published boundary", "IRR is outside the current reconstruction catalogue and is retained verbatim; SUMPRODUCT reconstructs independently."],
    ["Expected internal result", "INCOMPLETE. The cached workbook result is visible but is not reconstructed or verified."],
    ["Expected external result", "NOT PERFORMED. No accounting-reference figures are supplied."],
    ["Gate 3 demonstration", "Without the explicit existing incomplete-result acknowledgement, Gate 3 blocks and Gate 4/PDF remain unavailable."],
    ["Methodology boundary", "The independent negative result is contextual evidence, not a result certified by Glass Box."],
  ];
  guide.getRange("A13:C13").values = [["Manual check", "Expected value", "Location"]];
  guide.getRange("A14:C19").values = [
    ["Annual premium volume", 120_000_000, "Product Assumptions!B4"],
    ["Approved lapse", 0.06, "Product Assumptions!B5"],
    ["Embedded lapse", 0.03, "Policy Projection!C4"],
    ["Workbook PV margin", 4_600_000, "Profitability Summary!B5"],
    ["Independent pricing result", -4_800_000, "Independent Pricing Reference!B4"],
    ["Illustrative economic swing", 9_400_000, "Independent Pricing Reference!B5"],
  ];

  context.getRange("A1").values = [["Confirmed workbook context"]];
  context.getRange("A3:B3").values = [["Field", "Workbook value"]];
  context.getRange("A4:B8").values = [
    ["Entity", "Glass Box Life Belgium SA"],
    ["Period", "31 December 2025"],
    ["Currency", "EUR"],
    ["Basis", "Synthetic life-pricing demonstration"],
    ["Data status", "Entirely synthetic"],
  ];

  assumptions.getRange("A1").values = [["Approved product assumptions"]];
  assumptions.getRange("A3:B3").values = [["Assumption", "Approved value"]];
  assumptions.getRange("A4:B10").values = [
    ["Expected annual premium volume", 120_000_000],
    ["Approved annual lapse", 0.06],
    ["Discount rate", 0.05],
    ["Claims ratio", 0.72],
    ["Expense ratio", 0.10],
    ["Commission ratio", 0.10],
    ["Acquisition cash outflow", 34_659_129.89981896],
  ];
  assumptions.getRange("E3:F3").values = [["Product code", "Claims factor"]];
  assumptions.getRange("E4:F4").values = [["PROTECT", 0.72]];

  projection.getRange("A1").values = [["Policy projection"]];
  projection.getRange("A2").values = [["The approved 6% lapse is shown separately; the projection embeds 3% in C4."]];
  projection.getRange("A3:E3").values = [["Year", "Premium volume", "Lapse used", "In-force flag", "Product code"]];
  projection.getRange("A4:A8").values = [[1], [2], [3], [4], [5]];
  projection.getRange("B4").formulas = [["='Product Assumptions'!$B$4"]];
  projection.getRange("B5:B8").formulas = Array.from({ length: 4 }, (_, offset) => {
    const row = offset + 5;
    return [`=ROUND(B${row - 1}-B${row - 1}*C${row - 1},0)`];
  });
  projection.getRange("C4").formulas = [["=IF(B4>0,0.03,0)"]];
  projection.getRange("C5:C8").formulas = [["=C4"], ["=C4"], ["=C4"], ["=C4"]];
  projection.getRange("D4:D8").formulas = Array.from({ length: 5 }, (_, offset) => {
    const row = offset + 4;
    return [`=IF(B${row}>0,1,0)`];
  });
  projection.getRange("E4:E8").values = [["PROTECT"], ["PROTECT"], ["PROTECT"], ["PROTECT"], ["PROTECT"]];

  cashFlows.getRange("A1").values = [["Projection cash flows"]];
  cashFlows.getRange("A2").values = [["Year zero contains acquisition cash outflow; all later rows are supported intermediate calculations."]];
  cashFlows.getRange("A3:H3").values = [[
    "Year", "Premium", "Claims", "Expenses", "Commission", "Net cash flow", "Discount factor", "Status",
  ]];
  cashFlows.getRange("A4:A9").values = [[0], [1], [2], [3], [4], [5]];
  cashFlows.getRange("B4:E4").values = [[0, 0, 0, 0]];
  cashFlows.getRange("F4").formulas = [["=-'Product Assumptions'!$B$10"]];
  cashFlows.getRange("G4").values = [[1]];
  cashFlows.getRange("H4").values = [["Included"]];
  cashFlows.getRange("B5:B9").formulas = Array.from({ length: 5 }, (_, offset) => [`='Policy Projection'!B${offset + 4}`]);
  cashFlows.getRange("C5:C9").formulas = Array.from({ length: 5 }, (_, offset) => {
    const row = offset + 5;
    const projectionRow = offset + 4;
    return [`=-ROUND(B${row}*VLOOKUP('Policy Projection'!E${projectionRow},'Product Assumptions'!$E$4:$F$4,2,FALSE()),0)`];
  });
  cashFlows.getRange("D5:D9").formulas = Array.from({ length: 5 }, (_, offset) => {
    const row = offset + 5;
    return [`=-ROUND(B${row}*'Product Assumptions'!$B$8,0)`];
  });
  cashFlows.getRange("E5:E9").formulas = Array.from({ length: 5 }, (_, offset) => {
    const row = offset + 5;
    return [`=-ROUND(B${row}*'Product Assumptions'!$B$9,0)`];
  });
  cashFlows.getRange("F5:F9").formulas = Array.from({ length: 5 }, (_, offset) => {
    const row = offset + 5;
    return [`=SUM(B${row}:E${row})`];
  });
  cashFlows.getRange("G5:G9").formulas = Array.from({ length: 5 }, (_, offset) => {
    const row = offset + 5;
    return [`=G${row - 1}/(1+'Product Assumptions'!$B$6)`];
  });
  cashFlows.getRange("H5:H9").values = [["Included"], ["Included"], ["Included"], ["Included"], ["Included"]];

  workbook.names.add("NetCashFlows", "='Cash Flows'!$F$4:$F$9");
  workbook.names.add("DiscountFactors", "='Cash Flows'!$G$4:$G$9");

  summary.getRange("A1").values = [["Life-product profitability summary"]];
  summary.getRange("A2").values = [["Cached workbook values are not substitutes for unsupported reconstruction."]];
  summary.getRange("A4:B4").values = [["Metric", "Workbook value"]];
  summary.getRange("A5:A10").values = [
    ["Present-value margin"], ["Internal rate of return"], ["Profitability ratio"],
    ["Designated profitability result"], ["Included projected premium"], ["Included projection periods"],
  ];
  summary.getRange("B5:B10").formulas = [
    ["=SUMPRODUCT('Cash Flows'!$F$4:$F$9,'Cash Flows'!$G$4:$G$9)"],
    ["=IRR(NetCashFlows)"],
    ["=ROUND(B5/'Product Assumptions'!B4,3)"],
    ["=IF(B6>0,B5,0)"],
    ["=SUMIFS('Cash Flows'!B5:B9,'Cash Flows'!H5:H9,\"Included\")"],
    ["=COUNTIFS('Cash Flows'!H5:H9,\"Included\")"],
  ];

  independent.getRange("A1").values = [["Independent pricing reference"]];
  independent.getRange("A2").values = [["Contextual evidence only; not a result certified by Glass Box."]];
  independent.getRange("A3:B3").values = [["Measure", "Controlled value"]];
  independent.getRange("A4:A5").values = [["Independent pricing result"], ["Illustrative economic swing"]];
  independent.getRange("B4").values = [[-4_800_000]];
  independent.getRange("B5").formulas = [["='Profitability Summary'!B5-B4"]];

  applyBaseStyle(workbook);
  for (const [sheet, range] of [
    [guide, "A1:C1"], [context, "A1:B1"], [assumptions, "A1:F1"],
    [projection, "A1:E1"], [cashFlows, "A1:H1"], [summary, "A1:B1"], [independent, "A1:B1"],
  ]) styleTitle(sheet, range);
  for (const [sheet, range] of [
    [guide, "A13:C13"], [context, "A3:B3"], [assumptions, "A3:B3"], [assumptions, "E3:F3"],
    [projection, "A3:E3"], [cashFlows, "A3:H3"], [summary, "A4:B4"], [independent, "A3:B3"],
  ]) styleHeader(sheet, range);
  assumptions.getRange("B4:B10").format.fill = INPUT_YELLOW;
  assumptions.getRange("B4:B10").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  assumptions.getRange("F4").format.fill = INPUT_YELLOW;
  projection.getRange("B4:D8").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  projection.getRange("C4").format.fill = WARNING_RED;
  cashFlows.getRange("B5:G9").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  summary.getRange("B5:B10").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  summary.getRange("B5:B8").format.fill = WARNING_RED;
  independent.getRange("B4").format.fill = INPUT_YELLOW;
  independent.getRange("B4").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  independent.getRange("B5").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  for (const [sheet, range] of [
    [guide, "B14"], [guide, "B17:B19"], [assumptions, "B4"], [assumptions, "B10"],
    [projection, "B4:B8"], [cashFlows, "B4:F9"], [summary, "B5"], [summary, "B8:B9"], [independent, "B4:B5"],
  ]) sheet.getRange(range).format.numberFormat = currencyFormat;
  for (const [sheet, range] of [
    [guide, "B15:B16"], [assumptions, "B5:B9"], [projection, "C4:C8"], [summary, "B6:B7"],
  ]) sheet.getRange(range).format.numberFormat = percentageFormat;
  projection.freezePanes.freezeRows(3);
  cashFlows.freezePanes.freezeRows(3);
  setWidths(guide, { "A:A": 31, "B:B": 90, "C:C": 34 });
  setWidths(context, { "A:A": 22, "B:B": 52 });
  setWidths(assumptions, { "A:A": 38, "B:B": 22, "E:E": 20, "F:F": 18 });
  setWidths(projection, { "A:A": 12, "B:B": 23, "C:C": 18, "D:D": 17, "E:E": 18 });
  setWidths(cashFlows, { "A:A": 11, "B:F": 19, "G:G": 17, "H:H": 15 });
  setWidths(summary, { "A:A": 40, "B:B": 24 });
  setWidths(independent, { "A:A": 38, "B:B": 24 });

  await renderPreviews(workbook, path.join(outputDir, "case_9_previews"), {
    "Case Guide": "A1:C19",
    Context: "A1:B8",
    "Product Assumptions": "A1:F10",
    "Policy Projection": "A1:E8",
    "Cash Flows": "A1:H9",
    "Profitability Summary": "A1:B10",
    "Independent Pricing Reference": "A1:B5",
  });
  const workbookPath = path.join(outputDir, "case_9_life_pricing_incomplete.xlsx");
  await exportWorkbook(workbook, workbookPath);
  return {
    workbookPath,
    caseId: "case_9_life_pricing",
    caseVersion: "1.0.0",
    expected: {
      gate1_context: {
        entity: "Glass Box Life Belgium SA",
        period: "31 December 2025",
        currency: "EUR",
        basis: "Synthetic life-pricing demonstration",
      },
      authoritative_output_cells: ["Profitability Summary!B8"],
      summary_outputs: {
        opening_premium_volume: { cell: "Policy Projection!B4", expected_value: 120_000_000 },
        included_projected_premium: { cell: "Profitability Summary!B9", expected_value: 565_063_897 },
        included_projection_periods: { cell: "Profitability Summary!B10", expected_value: 5 },
        present_value_margin: { cell: "Profitability Summary!B5", expected_value: 4_600_000, tolerance: 0.01, expected_completeness: "complete", unsupported_functions: [] },
        internal_rate_of_return: { cell: "Profitability Summary!B6", expected_value: 0.097825786, tolerance: 1e-8, expected_completeness: "partial", unsupported_functions: ["IRR"] },
        designated_profitability_result: { cell: "Profitability Summary!B8", expected_value: 4_600_000, tolerance: 0.01, expected_completeness: "partial", unsupported_functions: ["IRR"] },
        independent_pricing_result: { cell: "Independent Pricing Reference!B4", expected_value: -4_800_000 },
        illustrative_economic_swing: { cell: "Independent Pricing Reference!B5", expected_value: 9_400_000, tolerance: 0.01, expected_completeness: "complete", unsupported_functions: [] },
      },
      expected_finding: {
        category: "hardcoded_numeric_assumption",
        formula_cell: "Policy Projection!C4",
        literal: "0.03",
      },
      unsupported_formulas: ["IRR"],
      approved_lapse: 0.06,
      embedded_lapse: 0.03,
      independent_result_is_context_only: true,
      expected_internal_verdict: "incomplete",
      expected_external_verdict: "not_performed",
      expected_gate_block: 3,
      pdf_available_after_gate4: false,
    },
    referenceLines: [],
    referenceControlTotal: null,
  };
}

async function buildCase10() {
  const workbook = Workbook.create();
  const guide = workbook.worksheets.add("Case Guide");
  const context = workbook.worksheets.add("Context");
  const calculation = workbook.worksheets.add("Reserve Calculation");
  const bridge = workbook.worksheets.add("Accounting Bridge");

  guide.getRange("A1").values = [["Case 10: right numbers, wrong accounting context"]];
  guide.getRange("A3:B12").values = [
    ["Business problem", "Synthetic reserve outputs are compared with an incompatible accounting extract."],
    ["Workbook context", "Glass Box Life Belgium SA; 31 December 2025; EUR."],
    ["Reference context", "Glass Box Life UK Ltd; 30 September 2025; GBP."],
    ["Mapped numeric difference", "Zero for the three plausible one-to-one mapping proposals."],
    ["Internal consistency", "PASS after human-supplied thresholds."],
    ["External accounting reconciliation", "BLOCK: incompatible entity, period and currency."],
    ["Population gaps", "Two duplicate-labelled reference lines remain unmatched; the management overlay remains unmapped."],
    ["Mapping control", "Every fuzzy proposal starts unapproved and needs a named human reviewer."],
    ["Gate effect", "Approving mappings cannot cure context mismatch; Gate 4 and PDF remain unavailable."],
    ["Scope boundary", "This demonstrates refusal to provide comfort, not an audit opinion."],
  ];
  guide.getRange("A14:C14").values = [["Manual check", "Expected value", "Location"]];
  guide.getRange("A15:C20").values = [
    ["Claims reserve", 78_400_000, "Accounting Bridge!B4"],
    ["Expense reserve", 9_600_000, "Accounting Bridge!B5"],
    ["Risk adjustment", 7_500_000, "Accounting Bridge!B6"],
    ["Management reserve overlay", 300_000, "Accounting Bridge!B7"],
    ["Mapped numeric difference", 0, "Three proposed mapping lines"],
    ["Reference population total", 96_250_000, "Five distinct CSV rows"],
  ];

  context.getRange("A1").values = [["Confirmed workbook context"]];
  context.getRange("A3:B3").values = [["Field", "Workbook value"]];
  context.getRange("A4:B8").values = [
    ["Entity", "Glass Box Life Belgium SA"],
    ["Period", "31 December 2025"],
    ["Currency", "EUR"],
    ["Basis", "Synthetic reserve-accounting demonstration"],
    ["Data status", "Entirely synthetic"],
  ];

  calculation.getRange("A1").values = [["Reserve calculation"]];
  calculation.getRange("A3:D3").values = [["Component", "Gross estimate", "Adjustment", "Calculated reserve"]];
  calculation.getRange("A4:C7").values = [
    ["Claims reserve provision", 80_000_000, -1_600_000],
    ["Expense reserve", 10_000_000, -400_000],
    ["Risk adjustment", 7_200_000, 300_000],
    ["Management reserve overlay", 250_000, 50_000],
  ];
  calculation.getRange("D4:D7").formulas = [["=B4+C4"], ["=B5+C5"], ["=B6+C6"], ["=B7+C7"]];
  calculation.getRange("A9").values = [["Workbook reserve total"]];
  calculation.getRange("D9").formulas = [["=SUM(D4:D7)"]];

  bridge.getRange("A1").values = [["Accounting bridge outputs"]];
  bridge.getRange("A2").values = [["All four genuine monetary outputs are designated at Gate 2."]];
  bridge.getRange("A3:C3").values = [["Authoritative output", "Workbook amount (EUR)", "Reconciliation status expected"]];
  bridge.getRange("A4:A7").values = [["Claims reserve provision"], ["Expense reserve"], ["Risk adjustment"], ["Management reserve overlay"]];
  bridge.getRange("B4:B7").formulas = [["='Reserve Calculation'!D4"], ["='Reserve Calculation'!D5"], ["='Reserve Calculation'!D6"], ["='Reserve Calculation'!D7"]];
  bridge.getRange("C4:C7").values = [
    ["Fuzzy proposal; starts unapproved"], ["Exact-label proposal; starts unapproved"],
    ["Exact-label proposal; starts unapproved"], ["No accounting counterpart"],
  ];

  applyBaseStyle(workbook);
  for (const [sheet, range] of [
    [guide, "A1:C1"], [context, "A1:B1"], [calculation, "A1:D1"], [bridge, "A1:C1"],
  ]) styleTitle(sheet, range);
  for (const [sheet, range] of [
    [guide, "A14:C14"], [context, "A3:B3"], [calculation, "A3:D3"], [bridge, "A3:C3"],
  ]) styleHeader(sheet, range);
  styleSection(calculation, "A9:D9");
  calculation.getRange("B4:C7").format.fill = INPUT_YELLOW;
  calculation.getRange("B4:C7").format.font = { name: FONT, size: 10, color: INPUT_BLUE };
  calculation.getRange("D4:D9").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  bridge.getRange("B4:B7").format.font = { name: FONT, size: 10, color: FORMULA_GREEN };
  for (const [sheet, range] of [
    [guide, "B15:B20"], [calculation, "B4:D9"], [bridge, "B4:B7"],
  ]) sheet.getRange(range).format.numberFormat = currencyFormat;
  setWidths(guide, { "A:A": 34, "B:B": 88, "C:C": 32 });
  setWidths(context, { "A:A": 22, "B:B": 54 });
  setWidths(calculation, { "A:A": 37, "B:D": 22 });
  setWidths(bridge, { "A:A": 38, "B:B": 24, "C:C": 47 });

  await renderPreviews(workbook, path.join(outputDir, "case_10_previews"), {
    "Case Guide": "A1:C20",
    Context: "A1:B8",
    "Reserve Calculation": "A1:D9",
    "Accounting Bridge": "A1:C7",
  });
  const workbookPath = path.join(outputDir, "case_10_accounting_context_failure.xlsx");
  await exportWorkbook(workbook, workbookPath);
  return {
    workbookPath,
    caseId: "case_10_accounting_context",
    caseVersion: "1.0.0",
    referenceFilename: "case_10_wrong_ledger.csv",
    referenceContext: {
      entity: "Glass Box Life UK Ltd",
      period: "30 September 2025",
      currency: "GBP",
      basis: "Synthetic UK reserve ledger extract",
    },
    expected: {
      gate1_context: {
        entity: "Glass Box Life Belgium SA",
        period: "31 December 2025",
        currency: "EUR",
        basis: "Synthetic reserve-accounting demonstration",
      },
      reference_context: {
        entity: "Glass Box Life UK Ltd",
        period: "30 September 2025",
        currency: "GBP",
        basis: "Synthetic UK reserve ledger extract",
      },
      authoritative_output_cells: [
        "Accounting Bridge!B4", "Accounting Bridge!B5", "Accounting Bridge!B6", "Accounting Bridge!B7",
      ],
      summary_outputs: {
        claims_reserve: { cell: "Accounting Bridge!B4", expected_value: 78_400_000 },
        expense_reserve: { cell: "Accounting Bridge!B5", expected_value: 9_600_000 },
        risk_adjustment: { cell: "Accounting Bridge!B6", expected_value: 7_500_000 },
        management_reserve_overlay: { cell: "Accounting Bridge!B7", expected_value: 300_000 },
      },
      expected_finding: null,
      expected_mapping_count: 3,
      expected_unmatched_reference_ids: ["REF-0004", "REF-0005"],
      expected_unmapped_output_cells: ["Accounting Bridge!B7"],
      mapped_numeric_difference: 0,
      external_block_reasons: [
        "entity_mismatch", "period_mismatch", "currency_mismatch", "unmatched_references", "unmapped_output",
      ],
      expected_internal_verdict: "pass",
      expected_external_verdict: "block",
      expected_gate_block: 3,
      pdf_available_after_gate4: false,
    },
    referenceLines: [
      { account_number: "UK-TP-001", label: "Claims provisions", amount: 78_400_000, debit_credit: "debit", ledger_source: "Synthetic wrong-ledger extract", evidence_reference: "UK ledger row 1" },
      { account_number: "UK-TP-002", label: "Expense reserve", amount: 9_600_000, debit_credit: "debit", ledger_source: "Synthetic wrong-ledger extract", evidence_reference: "UK ledger row 2" },
      { account_number: "UK-TP-003", label: "Risk adjustment", amount: 7_500_000, debit_credit: "debit", ledger_source: "Synthetic wrong-ledger extract", evidence_reference: "UK ledger row 3" },
      { account_number: "UK-TP-004", label: "Other technical provisions", amount: 500_000, debit_credit: "debit", ledger_source: "Synthetic wrong-ledger extract", evidence_reference: "UK ledger row 4" },
      { account_number: "UK-TP-005", label: "Other technical provisions", amount: 750_000, debit_credit: "debit", ledger_source: "Synthetic wrong-ledger extract", evidence_reference: "UK ledger row 5" },
    ],
    referenceControlTotal: 96_250_000,
  };
}

await fs.mkdir(outputDir, { recursive: true });
const metadata = [];
if (requestedCases.includes(7)) {
  metadata.push(await buildCase7(false));
  metadata.push(await buildCase7(true));
}
if (requestedCases.includes(8)) {
  metadata.push(await buildCase8());
}
if (requestedCases.includes(9)) {
  metadata.push(await buildCase9());
}
if (requestedCases.includes(10)) {
  metadata.push(await buildCase10());
}
for (const item of metadata) {
  await fs.writeFile(
    path.join(outputDir, `${item.caseId}_builder_metadata.json`),
    JSON.stringify(item, null, 2) + "\n",
    "utf8"
  );
}
console.log(JSON.stringify({ cases: requestedCases, workbooks: metadata.map((item) => item.workbookPath) }, null, 2));
