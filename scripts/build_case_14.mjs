#!/usr/bin/env node
/** Author the pre-assumption-change Case 14 workbook with Artifact Tool. */

import fs from "node:fs/promises";
import path from "node:path";

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
const { FileBlob, SpreadsheetFile, Workbook } = artifactModule;

if (process.argv[2] === "--verify") {
  const inputPath = process.argv[3];
  const previewDir = process.argv[4];
  if (!inputPath || !previewDir) {
    throw new Error("Usage: build_case_14.mjs --verify INPUT.xlsx PREVIEW_DIR");
  }
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  for (const sheetName of ["Board Summary", "Assumptions", "Reserve Calculation"]) {
    const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 2, format: "png" });
    const fileName = `${sheetName.toLowerCase().replaceAll(" ", "_")}.png`;
    await fs.mkdir(previewDir, { recursive: true });
    await fs.writeFile(path.join(previewDir, fileName), new Uint8Array(await preview.arrayBuffer()));
  }
  const summary = await workbook.inspect({
    kind: "table",
    sheetId: "Board Summary",
    range: "A2:B12",
    include: "values,formulas",
    tableMaxRows: 20,
    tableMaxCols: 4,
  });
  const assumptions = await workbook.inspect({
    kind: "table",
    sheetId: "Assumptions",
    range: "A12:C18",
    include: "values,formulas",
    tableMaxRows: 12,
    tableMaxCols: 4,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 },
    summary: "Case 14 formula error scan",
  });
  console.log(summary.ndjson);
  console.log(assumptions.ndjson);
  console.log(errors.ndjson);
  process.exit(0);
}

const outputPath = process.argv[2];
if (!outputPath) {
  throw new Error("Usage: build_case_14.mjs OUTPUT.xlsx");
}

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Board Summary");
const assumptions = workbook.worksheets.add("Assumptions");
const calculation = workbook.worksheets.add("Reserve Calculation");

const navy = "#17365D";
const blue = "#D9EAF7";
const paleBlue = "#EAF3F8";
const amber = "#FFF2CC";
const green = "#008000";
const body = "#1F2937";
const lightBorder = "#D9E2F3";
const fontFamily = "Arial";
const moneyFormat = '€#,##0.0,,"m";[Red](€#,##0.0,,"m");-';

for (const sheet of [summary, assumptions, calculation]) {
  sheet.showGridLines = false;
  sheet.getRange("A1:F24").format.font = { name: fontFamily, size: 10, color: body };
  sheet.getRange("A1:F24").format.verticalAlignment = "center";
}

summary.tabColor = navy;
summary.getRange("A2:F2").merge();
summary.getRange("A2").values = [["Q3 2026 Reserving Committee"]];
summary.getRange("A2").format.font = { name: fontFamily, size: 16, bold: true, color: navy };
summary.getRange("A3:F3").format.borders = { bottom: { style: "thin", color: navy } };
summary.getRange("A4:B4").values = [["Opening reserve", null]];
summary.getRange("B4").formulas = [["='Reserve Calculation'!B4"]];
summary.getRange("A6:B6").values = [["Claims position", "EUR million"]];
summary.getRange("A6:B6").format = {
  fill: navy,
  font: { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" },
  borders: { preset: "outside", style: "thin", color: navy },
};
summary.getRange("A7:A9").values = [
  ["Tail development factor"],
  ["Ultimate claims"],
  ["Closing IBNR"],
];
summary.getRange("B7").formulas = [["=Assumptions!C14"]];
summary.getRange("B8").formulas = [["='Reserve Calculation'!B8"]];
summary.getRange("B9").formulas = [["='Reserve Calculation'!B10"]];
summary.getRange("A7:B9").format.borders = {
  insideHorizontal: { style: "thin", color: lightBorder },
  bottom: { style: "double", color: navy },
};
summary.getRange("A9:B9").format.font = { name: fontFamily, size: 11, bold: true, color: navy };
summary.getRange("B4").format.numberFormat = moneyFormat;
summary.getRange("B7").format.numberFormat = "0.000";
summary.getRange("B8:B9").format.numberFormat = moneyFormat;
summary.getRange("B4:B9").format.horizontalAlignment = "right";
summary.getRange("A12:F13").merge();
summary.getRange("A12").values = [[
  "Prepared from the claims-triangle model for committee review. Figures are synthetic and do not constitute an opinion on reserve adequacy or methodology."
]];
summary.getRange("A12:F13").format = {
  fill: paleBlue,
  font: { name: fontFamily, size: 9, italic: true, color: "#44546A" },
  wrapText: true,
  borders: { preset: "outside", style: "thin", color: lightBorder },
};
summary.getRange("A:A").format.columnWidth = 31;
summary.getRange("B:B").format.columnWidth = 18;
summary.getRange("C:F").format.columnWidth = 4;
summary.getRange("2:2").format.rowHeight = 25;
summary.getRange("12:13").format.rowHeight = 24;

assumptions.tabColor = "#5B9BD5";
assumptions.getRange("A2:F2").merge();
assumptions.getRange("A2").values = [["Reserve assumptions"]];
assumptions.getRange("A2").format.font = { name: fontFamily, size: 15, bold: true, color: navy };
assumptions.getRange("A3:F3").format.borders = { bottom: { style: "thin", color: navy } };
assumptions.getRange("A12:C12").values = [["Driver", "Previous setting", "Current setting"]];
assumptions.getRange("A12:C12").format = {
  fill: navy,
  font: { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" },
};
assumptions.getRange("A13:C18").values = [
  ["As-of period", "Q3:26", "Q3:26"],
  ["Tail development factor", 1.042, 1.042],
  ["Tail sensitivity volume", null, 489473684.2105263],
  ["Ultimate claims before tail change", null, 148200000],
  ["Reported claims", null, 106500000],
  ["Opening reserve", null, 65000000],
];
assumptions.getRange("C14:C18").format.fill = amber;
assumptions.getRange("C14:C18").format.font = { name: fontFamily, size: 10, color: "#0000FF" };
assumptions.getRange("B14:C14").format.numberFormat = "0.000";
assumptions.getRange("C15:C18").format.numberFormat = moneyFormat;
assumptions.getRange("A13:C18").format.borders = {
  insideHorizontal: { style: "thin", color: lightBorder },
  bottom: { style: "thin", color: navy },
};
assumptions.getRange("A20:F21").merge();
assumptions.getRange("A20").values = [[
  "Editable inputs are highlighted. This synthetic workbook separates assumption selection from the calculation and output views."
]];
assumptions.getRange("A20:F21").format = {
  fill: paleBlue,
  font: { name: fontFamily, size: 9, italic: true, color: "#44546A" },
  wrapText: true,
};
assumptions.getRange("A:A").format.columnWidth = 38;
assumptions.getRange("B:C").format.columnWidth = 20;
assumptions.getRange("D:F").format.columnWidth = 4;

calculation.getRange("A2:F2").merge();
calculation.getRange("A2").values = [["Claims reserve calculation"]];
calculation.getRange("A2").format.font = { name: fontFamily, size: 15, bold: true, color: navy };
calculation.getRange("A3:F3").format.borders = { bottom: { style: "thin", color: navy } };
calculation.getRange("A4:B4").values = [["Opening reserve", null]];
calculation.getRange("B4").formulas = [["=Assumptions!C18"]];
calculation.getRange("A6:B6").values = [["Calculation", "Amount"]];
calculation.getRange("A6:B6").format = {
  fill: navy,
  font: { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" },
};
calculation.getRange("A7:A10").values = [
  ["Tail assumption impact"],
  ["Ultimate claims"],
  ["Reported claims"],
  ["Closing IBNR"],
];
calculation.getRange("B7").formulas = [["=(Assumptions!C14-Assumptions!B14)*Assumptions!C15"]];
calculation.getRange("B8").formulas = [["=Assumptions!C16+B7"]];
calculation.getRange("B9").formulas = [["=Assumptions!C17"]];
calculation.getRange("B10").formulas = [["=B8-B9"]];
calculation.getRange("B4:B10").format.numberFormat = moneyFormat;
calculation.getRange("B7:B10").format.font = { name: fontFamily, size: 10, color: "#000000" };
calculation.getRange("A7:B10").format.borders = {
  insideHorizontal: { style: "thin", color: lightBorder },
  bottom: { style: "double", color: navy },
};
calculation.getRange("A10:B10").format.font = { name: fontFamily, size: 11, bold: true, color: navy };
calculation.getRange("A12:F13").merge();
calculation.getRange("A12").values = [[
  "The tail impact is the change from the previous factor multiplied by the synthetic tail-sensitive claims volume."
]];
calculation.getRange("A12:F13").format = {
  fill: blue,
  font: { name: fontFamily, size: 9, italic: true, color: "#44546A" },
  wrapText: true,
};
calculation.getRange("A:A").format.columnWidth = 38;
calculation.getRange("B:B").format.columnWidth = 20;
calculation.getRange("C:F").format.columnWidth = 4;

workbook.recalculate();
const inspection = await workbook.inspect({
  kind: "table",
  sheetId: "Board Summary",
  range: "A2:B12",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 4,
});
console.log(inspection.ndjson);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
