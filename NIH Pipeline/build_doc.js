const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, BorderStyle, WidthType, ShadingType,
  VerticalAlign, Header, Footer, PageNumber, LevelFormat,
  ExternalHyperlink, PageBreak, UnderlineType
} = require("docx");

// ── Constants ────────────────────────────────────────────────────────────────
const PAGE_W    = 12240;
const PAGE_H    = 15840;
const MARGIN    = 1080;   // 0.75 inch
const CONTENT_W = PAGE_W - MARGIN * 2;  // 10080 DXA

const C = {
  NIH_DARK:   "1F3864",
  NIH_MID:    "2E5090",
  NIH_LIGHT:  "D6E4F7",
  NSF_DARK:   "0D3349",
  NSF_MID:    "1A5276",
  NSF_LIGHT:  "D1ECF1",
  ACCENT:     "C55A11",
  CODE_BG:    "F2F2F2",
  CODE_BORDER:"AAAAAA",
  TBL_HEADER: "1F3864",
  TBL_ALT:    "EEF4FB",
  WHITE:      "FFFFFF",
  DARK_TEXT:  "1A1A1A",
  MID_TEXT:   "444444",
  LIGHT_TEXT: "777777",
  GREEN:      "1E6B3C",
  DIVIDER:    "CCCCCC",
};

const FONT = "Arial";

const border1 = (color) => ({ style: BorderStyle.SINGLE, size: 4, color });
const noBorder = () => ({ style: BorderStyle.NONE, size: 0, color: "FFFFFF" });
const cellBorder = { top: border1("CCCCCC"), bottom: border1("CCCCCC"), left: border1("CCCCCC"), right: border1("CCCCCC") };
const noBorders  = { top: noBorder(), bottom: noBorder(), left: noBorder(), right: noBorder() };

// ── Helpers ───────────────────────────────────────────────────────────────────
function txt(text, opts = {}) {
  return new TextRun({ text, font: FONT, size: opts.size || 20, bold: opts.bold, italic: opts.italic,
    color: opts.color || C.DARK_TEXT, underline: opts.underline ? { type: UnderlineType.SINGLE } : undefined,
    highlight: opts.highlight });
}

function para(children, opts = {}) {
  const runs = Array.isArray(children) ? children : [typeof children === "string" ? txt(children, opts) : children];
  return new Paragraph({
    children: runs,
    heading: opts.heading,
    alignment: opts.align || AlignmentType.LEFT,
    spacing: { before: opts.before || 120, after: opts.after || 80, line: opts.line || 280 },
    indent: opts.indent ? { left: opts.indent } : undefined,
    numbering: opts.numbering,
    border: opts.border,
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 320, after: 160 },
    children: [new TextRun({ text, font: FONT, size: 32, bold: true, color: C.NIH_DARK })],
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: C.NIH_MID, space: 4 } },
  });
}

function h2(text, color) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 260, after: 120 },
    children: [new TextRun({ text, font: FONT, size: 26, bold: true, color: color || C.NIH_MID })],
  });
}

function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 80 },
    children: [new TextRun({ text, font: FONT, size: 22, bold: true, color: C.ACCENT })],
  });
}

function body(text, opts = {}) {
  return para([txt(text, { size: 20, color: C.DARK_TEXT, ...opts })], { before: 60, after: 80, line: 300 });
}

function bullet(text, sub = false) {
  return new Paragraph({
    numbering: { reference: "bullets", level: sub ? 1 : 0 },
    spacing: { before: 40, after: 40, line: 280 },
    children: [new TextRun({ text, font: FONT, size: 20, color: C.DARK_TEXT })],
  });
}

function codeLine(text) {
  return new Paragraph({
    spacing: { before: 20, after: 20, line: 240 },
    indent: { left: 360 },
    children: [new TextRun({ text, font: "Courier New", size: 18, color: "1E1E1E" })],
  });
}

function codeBlock(lines) {
  const paragraphs = lines.map(l => new Paragraph({
    spacing: { before: 10, after: 10, line: 240 },
    children: [new TextRun({ text: l === "" ? " " : l, font: "Courier New", size: 18, color: "1E1E1E" })],
  }));
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    rows: [new TableRow({ children: [new TableCell({
      borders: { top: border1(C.CODE_BORDER), bottom: border1(C.CODE_BORDER), left: border1(C.CODE_BORDER), right: border1(C.CODE_BORDER) },
      shading: { fill: C.CODE_BG, type: ShadingType.CLEAR },
      width: { size: CONTENT_W, type: WidthType.DXA },
      margins: { top: 80, bottom: 80, left: 200, right: 200 },
      children: paragraphs,
    })]})]
  });
}

function inline_code(text) {
  return new TextRun({ text: ` ${text} `, font: "Courier New", size: 18, color: C.NSF_DARK,
    shading: { fill: C.CODE_BG, type: ShadingType.CLEAR } });
}

function spacer(before = 100) {
  return new Paragraph({ spacing: { before, after: 0 }, children: [new TextRun("")] });
}

function divider() {
  return new Paragraph({
    spacing: { before: 160, after: 160 },
    children: [new TextRun("")],
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.DIVIDER, space: 1 } },
  });
}

function infoBox(label, lines, color = C.NIH_LIGHT, textColor = C.NIH_DARK) {
  const children = [
    new Paragraph({ spacing: { before: 60, after: 40 }, children: [new TextRun({ text: label, font: FONT, size: 20, bold: true, color: textColor })] }),
    ...lines.map(l => new Paragraph({ spacing: { before: 20, after: 20, line: 280 }, children: [new TextRun({ text: l, font: FONT, size: 19, color: C.MID_TEXT })] })),
  ];
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    rows: [new TableRow({ children: [new TableCell({
      borders: { top: border1(textColor), bottom: border1(C.DIVIDER), left: border1(textColor), right: border1(C.DIVIDER) },
      shading: { fill: color, type: ShadingType.CLEAR },
      width: { size: CONTENT_W, type: WidthType.DXA },
      margins: { top: 100, bottom: 100, left: 180, right: 180 },
      children,
    })]})]
  });
}

function twoColTable(headers, rows, colWidths) {
  const totalW = colWidths.reduce((a, b) => a + b, 0);
  const hdrRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      borders: cellBorder,
      shading: { fill: C.TBL_HEADER, type: ShadingType.CLEAR },
      width: { size: colWidths[i], type: WidthType.DXA },
      margins: { top: 80, bottom: 80, left: 120, right: 120 },
      verticalAlign: VerticalAlign.CENTER,
      children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: h, font: FONT, size: 18, bold: true, color: C.WHITE })] })],
    })),
  });
  const dataRows = rows.map((row, ri) => new TableRow({
    children: row.map((cell, ci) => new TableCell({
      borders: cellBorder,
      shading: { fill: ri % 2 === 0 ? C.WHITE : C.TBL_ALT, type: ShadingType.CLEAR },
      width: { size: colWidths[ci], type: WidthType.DXA },
      margins: { top: 70, bottom: 70, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: cell, font: FONT, size: 18, color: C.DARK_TEXT })] })],
    })),
  }));
  return new Table({ width: { size: totalW, type: WidthType.DXA }, columnWidths: colWidths, rows: [hdrRow, ...dataRows] });
}

function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

// ── Cover Page ────────────────────────────────────────────────────────────────
function coverPage() {
  return [
    spacer(2400),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 0 },
      children: [new TextRun({ text: "NIH & NSF Grants", font: FONT, size: 56, bold: true, color: C.NIH_DARK })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 80, after: 80 },
      children: [new TextRun({ text: "Data Extraction Pipeline", font: FONT, size: 40, bold: false, color: C.NIH_MID })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 0 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: C.ACCENT, space: 4 } },
      children: [new TextRun("")],
    }),
    spacer(240),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 40, after: 40 },
      children: [new TextRun({ text: "Technical Flow & Architecture Guide", font: FONT, size: 26, italic: true, color: C.MID_TEXT })],
    }),
    spacer(240),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 40, after: 40 },
      children: [new TextRun({ text: "Johns Hopkins University  |  FY2024 – FY2026", font: FONT, size: 22, color: C.LIGHT_TEXT })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 20, after: 20 },
      children: [new TextRun({ text: "April 2026", font: FONT, size: 22, color: C.LIGHT_TEXT })],
    }),
    pageBreak(),
  ];
}

// ── Document ──────────────────────────────────────────────────────────────────
const doc = new Document({
  numbering: {
    config: [
      { reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 540, hanging: 300 } }, run: { font: FONT } } },
          { level: 1, format: LevelFormat.BULLET, text: "\u25E6", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 900, hanging: 300 } }, run: { font: FONT } } },
        ],
      },
    ],
  },
  styles: {
    default: { document: { run: { font: FONT, size: 20, color: C.DARK_TEXT } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, color: C.NIH_DARK, font: FONT },
        paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, color: C.NIH_MID, font: FONT },
        paragraph: { spacing: { before: 260, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, color: C.ACCENT, font: FONT },
        paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: PAGE_W, height: PAGE_H },
        margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
      },
    },
    headers: {
      default: new Header({ children: [
        new Paragraph({
          alignment: AlignmentType.RIGHT,
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.NIH_MID, space: 4 } },
          spacing: { before: 0, after: 80 },
          children: [new TextRun({ text: "NIH & NSF Grants Data Extraction Pipeline", font: FONT, size: 18, color: C.LIGHT_TEXT })],
        }),
      ]}),
    },
    footers: {
      default: new Footer({ children: [
        new Paragraph({
          alignment: AlignmentType.CENTER,
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: C.DIVIDER, space: 4 } },
          spacing: { before: 80, after: 0 },
          children: [
            new TextRun({ text: "Confidential  |  Page ", font: FONT, size: 18, color: C.LIGHT_TEXT }),
            new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: C.LIGHT_TEXT }),
            new TextRun({ text: " of ", font: FONT, size: 18, color: C.LIGHT_TEXT }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT, size: 18, color: C.LIGHT_TEXT }),
          ],
        }),
      ]}),
    },
    children: [

      // ── COVER ──────────────────────────────────────────────────────────────
      ...coverPage(),

      // ══════════════════════════════════════════════════════════════════════
      // 1. OVERVIEW
      // ══════════════════════════════════════════════════════════════════════
      h1("1. Overview"),
      body("This document describes the end-to-end technical pipeline used to extract, paginate, and store grants and funding data from two major U.S. federal research agencies:"),
      spacer(60),
      bullet("NIH RePORTER API  —  National Institutes of Health (api.reporter.nih.gov)"),
      bullet("NSF Awards API  —  National Science Foundation (api.nsf.gov)"),
      spacer(80),
      body("The pipeline is implemented in Python using the requests library. No API keys or authentication are required for either source. Data is stored as structured JSON and exported to formatted Excel workbooks using openpyxl."),
      spacer(80),
      infoBox("Scope: Johns Hopkins University (FY2024 – FY2026)", [
        "NIH:  3,801 grants fetched  |  Source: NIH RePORTER v2 API",
        "NSF:    147 grants fetched  |  Source: NSF Awards API v1",
        "UEI (Unique Entity Identifier) used for exact org matching: FTMTDMBR29C7",
      ], C.NIH_LIGHT, C.NIH_DARK),
      spacer(120),

      // ── High-level architecture ──
      h2("1.1  High-Level Architecture"),
      body("The pipeline follows a simple linear flow: configure filters, call the API, paginate through all pages, save raw JSON, then flatten and export to Excel."),
      spacer(80),
      twoColTable(
        ["Stage", "What Happens"],
        [
          ["1. Configure Filters", "Set search criteria: org UEI, fiscal year range, grant type, keywords, amount range"],
          ["2. Build Request", "NIH: build JSON payload for POST request. NSF: build query params for GET request"],
          ["3. Call API", "Send HTTP request to the respective API endpoint using Python requests"],
          ["4. Parse Response", "Extract the results array + pagination metadata from the JSON response"],
          ["5. Paginate", "Loop, incrementing offset/page, until all records are retrieved or limit reached"],
          ["6. Save JSON", "Write all records to a .json file as the raw source of truth"],
          ["7. Flatten & Export", "Unnest nested fields (org, PI, institute), write rows to Excel with formatting"],
        ],
        [2800, 7280]
      ),
      spacer(120),

      pageBreak(),

      // ══════════════════════════════════════════════════════════════════════
      // 2. NIH RePORTER API
      // ══════════════════════════════════════════════════════════════════════
      h1("2. NIH RePORTER API"),
      infoBox("API Details", [
        "Base URL:     https://api.reporter.nih.gov/v2",
        "Method:       POST (JSON body)",
        "Auth:         None required",
        "Rate Limit:   1 request per second (recommended)",
        "Max per page: 500 records",
        "Max offset:   14,999 (15,000 record cap per query)",
        "Docs:         https://api.reporter.nih.gov",
      ], C.NIH_LIGHT, C.NIH_DARK),
      spacer(120),

      // 2.1 Endpoints
      h2("2.1  Available Endpoints"),
      twoColTable(
        ["Endpoint", "Description"],
        [
          ["POST /v2/projects/search", "Search and filter NIH-funded grants and research projects"],
          ["POST /v2/publications/search", "Search publications linked to NIH-funded projects (by PMID or project number)"],
        ],
        [3600, 6480]
      ),
      spacer(120),

      // 2.2 Request Structure
      h2("2.2  Request Structure"),
      body("NIH RePORTER uses POST requests with a JSON payload. The payload contains a criteria object (your filters), pagination controls, and sort options."),
      spacer(80),
      h3("Example Request Payload"),
      codeBlock([
        "POST https://api.reporter.nih.gov/v2/projects/search",
        "Content-Type: application/json",
        "",
        "{",
        '  "criteria": {',
        '    "fiscal_years": [2024, 2025, 2026],',
        '    "org_names": ["JOHNS HOPKINS UNIVERSITY"],',
        '    "activity_codes": ["R01", "K23"],',
        '    "award_amount_range": {',
        '      "min_amount": 100000,',
        '      "max_amount": 999999999',
        '    }',
        "  },",
        '  "offset": 0,',
        '  "limit": 500,',
        '  "sort_field": "project_start_date",',
        '  "sort_order": "desc"',
        "}",
      ]),
      spacer(120),

      // 2.3 All Filter Criteria
      h2("2.3  Available Filter Criteria"),
      twoColTable(
        ["Parameter", "Description & Example"],
        [
          ["fiscal_years", "List of fiscal years  e.g. [2024, 2025]"],
          ["org_names", "Recipient org name (partial match)  e.g. [\"JOHNS HOPKINS UNIVERSITY\"]"],
          ["pi_names", "PI name as object  e.g. [{\"last_name\": \"Smith\", \"first_name\": \"John\"}]"],
          ["activity_codes", "Grant type codes  e.g. [\"R01\", \"K23\", \"F31\"]"],
          ["agencies", "NIH institute abbreviations  e.g. [\"NCI\", \"NHLBI\", \"NIMH\"]"],
          ["project_nums", "Specific project numbers  e.g. [\"1R01CA123456-01\"]"],
          ["opportunity_numbers", "FOA/PA numbers  e.g. [\"PA-20-185\"]"],
          ["award_amount_range", "Min/max award in dollars  {\"min_amount\": 0, \"max_amount\": 500000}"],
          ["project_start_date", "Date range  {\"from_date\": \"2024-01-01\", \"to_date\": \"2024-12-31\"}"],
          ["project_end_date", "Same format as project_start_date"],
          ["advanced_text_search", "Free-text search  {\"operator\": \"and\", \"search_field\": \"all\", \"search_text\": \"CRISPR\"}"],
        ],
        [2800, 7280]
      ),
      spacer(120),

      // 2.4 Response Structure
      h2("2.4  Response Structure"),
      codeBlock([
        "{",
        '  "meta": {',
        '    "total": 3801,',
        '    "offset": 0,',
        '    "limit": 500,',
        '    "sort_field": "project_start_date",',
        '    "sort_order": "desc"',
        "  },",
        '  "results": [',
        "    {",
        '      "appl_id": 10798138,',
        '      "project_num": "5R01CA241169-05",',
        '      "fiscal_year": 2024,',
        '      "award_amount": 413715,',
        '      "direct_cost_amt": 251193,',
        '      "indirect_cost_amt": 162522,',
        '      "project_title": "The Role of Egfl6 in Tumor Immunity",',
        '      "organization": { "org_name": "JOHNS HOPKINS UNIVERSITY", "org_city": "BALTIMORE", ... },',
        '      "principal_investigators": [{ "full_name": "...", "first_name": "...", ... }],',
        '      "agency_ic_admin": { "abbreviation": "NCI", "name": "National Cancer Institute", ... },',
        '      "abstract_text": "PROJECT SUMMARY: ...",',
        '      "project_start_date": "2024-01-01T00:00:00",',
        '      "project_end_date": "2028-12-31T00:00:00",',
        '      "project_detail_url": "https://reporter.nih.gov/project-details/10798138"',
        "      ... 44 fields total",
        "    }",
        "  ]",
        "}",
      ]),
      spacer(120),

      // 2.5 Key Fields
      h2("2.5  Key Data Fields Returned"),
      twoColTable(
        ["Field Group", "Fields"],
        [
          ["Identity", "appl_id, project_num, core_project_num, fiscal_year, activity_code, award_type, funding_mechanism"],
          ["Financials", "award_amount, direct_cost_amt, indirect_cost_amt, agency_ic_fundings[ ]"],
          ["Organization", "organization.org_name, org_city, org_state, org_zipcode, dept_type"],
          ["People", "principal_investigators[ ].full_name / first_name / last_name / title, contact_pi_name, program_officers[ ]"],
          ["NIH Institute", "agency_ic_admin.abbreviation, name, code (e.g. NCI, NHLBI)"],
          ["Dates", "project_start_date, project_end_date, budget_start, budget_end, award_notice_date, date_added"],
          ["Science", "project_title, abstract_text, phr_text (public health relevance), pref_terms (keywords)"],
          ["Review", "full_study_section.name, opportunity_number, cfda_code"],
          ["Links", "project_detail_url (direct link to NIH RePORTER)"],
        ],
        [2400, 7680]
      ),
      spacer(120),

      // 2.6 Pagination
      h2("2.6  Pagination Strategy"),
      body("NIH RePORTER supports up to 500 records per request and a maximum offset of 14,999. The search_all_grants() function automatically loops through all pages:"),
      spacer(80),
      codeBlock([
        "def search_all_grants(max_records=500, **kwargs):",
        "    all_results = []",
        "    offset = 0",
        "    limit  = 500          # max per page",
        "",
        "    while len(all_results) < max_records:",
        "        data    = search_grants(offset=offset, limit=limit, **kwargs)",
        "        results = data['results']",
        "        total   = data['meta']['total']",
        "",
        "        all_results.extend(results)",
        "",
        "        # Stop when we have all records or hit the 15,000 API cap",
        "        if offset + limit >= total or offset + limit >= 15000:",
        "            break",
        "",
        "        offset += limit",
        "        time.sleep(1)     # respect rate limit",
        "",
        "    return all_results[:max_records]",
      ]),
      spacer(80),
      body("For JHU FY2024-2026 with 3,801 grants, this required 8 pages (500 x 7 + 301):"),
      spacer(40),
      bullet("Page 1: offset=0,    limit=500  →  500 grants"),
      bullet("Page 2: offset=500,  limit=500  →  500 grants"),
      bullet("Page 3: offset=1000, limit=500  →  500 grants"),
      bullet("..."),
      bullet("Page 8: offset=3500, limit=500  →  301 grants  (last page, stops here)"),
      spacer(120),

      pageBreak(),

      // ══════════════════════════════════════════════════════════════════════
      // 3. NSF Awards API
      // ══════════════════════════════════════════════════════════════════════
      h1("3. NSF Awards API"),
      infoBox("API Details", [
        "Base URL:     https://api.nsf.gov/services/v1/awards.json",
        "Method:       GET (URL query parameters)",
        "Auth:         None required",
        "Rate Limit:   No official limit; 1 req/sec used as best practice",
        "Max per page: 25 records (rpp parameter)",
        "Offset:       Starts at 1 (not 0)",
        "Docs:         https://resources.research.gov/common/webapi/awardapisearch-v1.htm",
      ], C.NSF_LIGHT, C.NSF_DARK),
      spacer(120),

      // 3.1 Key Difference
      h2("3.1  Key Difference from NIH: GET vs POST"),
      body("Unlike NIH which uses POST with a JSON body, the NSF API uses simple GET requests where all filters are passed as URL query parameters. This makes it easy to test in a browser."),
      spacer(80),
      codeBlock([
        "# NIH: POST with JSON body",
        "requests.post(",
        '    "https://api.reporter.nih.gov/v2/projects/search",',
        '    json={"criteria": {"fiscal_years": [2024]}, "limit": 500}',
        ")",
        "",
        "# NSF: GET with URL parameters",
        "requests.get(",
        '    "https://api.nsf.gov/services/v1/awards.json",',
        '    params={"ueiNumber": "FTMTDMBR29C7", "dateStart": "01/01/2024", "rpp": 25}',
        ")",
      ]),
      spacer(120),

      // 3.2 Important: UEI for exact matching
      h2("3.2  Using UEI for Exact Organization Matching"),
      body("A critical discovery during development: the NSF awardeeName parameter performs a broad keyword search across all records, NOT an exact org filter. Searching by name returned results from completely unrelated universities."),
      spacer(80),
      infoBox("Solution: Use ueiNumber for Exact Matching", [
        "The Unique Entity Identifier (UEI) is a government-wide org identifier that guarantees exact matching.",
        "Johns Hopkins University UEI:  FTMTDMBR29C7",
        "NSF org name:                  THE JOHNS HOPKINS UNIVERSITY",
        "",
        "The UEI was discovered by cross-referencing JHU records in the NIH data (organization.org_ueis field),",
        "then confirmed via a targeted NSF search filtering on awardeeStateCode=MD.",
      ], C.NSF_LIGHT, C.NSF_DARK),
      spacer(120),

      // 3.3 Request structure
      h2("3.3  Request Structure"),
      codeBlock([
        "GET https://api.nsf.gov/services/v1/awards.json",
        "    ?ueiNumber=FTMTDMBR29C7",
        "    &dateStart=01/01/2024",
        "    &dateEnd=12/31/2024",
        "    &rpp=25",
        "    &offset=1",
        "    &printFields=id,title,fundsObligatedAmt,piFirstName,piLastName,",
        "                 dirAbbr,divAbbr,fundProgramName,abstractText,...",
      ]),
      spacer(120),

      // 3.4 Filter Parameters
      h2("3.4  Available Filter Parameters"),
      twoColTable(
        ["Parameter", "Description & Example"],
        [
          ["ueiNumber", "Unique Entity Identifier for exact org match  e.g. FTMTDMBR29C7"],
          ["awardeeName", "Keyword search within org name (broad, not exact)  e.g. Johns Hopkins"],
          ["awardeeStateCode", "State abbreviation  e.g. MD, NY, CA"],
          ["awardeeCity", "City name  e.g. BALTIMORE"],
          ["dateStart / dateEnd", "Award date range in mm/dd/yyyy format  e.g. 01/01/2024"],
          ["startDateStart / startDateEnd", "Project start date range"],
          ["pdPIName", "Principal Investigator name"],
          ["poName", "Program Officer name"],
          ["fundsObligatedAmtFrom/To", "Dollar range filter  e.g. 100000 to 999999"],
          ["transType", "Grant type  e.g. Standard Grant, Cooperative Agreement, Fellowship Award"],
          ["ActiveAwards / ExpiredAwards", "Filter by status  e.g. true"],
          ["keyword", "Free-text search (supports AND, OR, NOT operators)"],
          ["printFields", "Comma-separated list of fields to return in response"],
          ["rpp", "Results per page, max 25"],
          ["offset", "Pagination offset, starts at 1"],
          ["sortKey", "Sort field: awardNumber, startDate, organization, principalInvestigator"],
        ],
        [2800, 7280]
      ),
      spacer(120),

      // 3.5 Response structure
      h2("3.5  Response Structure"),
      codeBlock([
        "{",
        '  "response": {',
        '    "award": [',
        "      {",
        '        "id": "2421670",',
        '        "title": "TRAILBLAZER: Quantum-Enabled Dial (QED)...",',
        '        "transType": "Standard Grant",',
        '        "date": "08/15/2024",',
        '        "startDate": "09/01/2024",',
        '        "expDate": "08/31/2027",',
        '        "fundsObligatedAmt": "3000000",',
        '        "estimatedTotalAmt": "3000000",',
        '        "piFirstName": "Yun",',
        '        "piLastName": "Chen",',
        '        "piEmail": "ychen@jhu.edu",',
        '        "awardee": "THE JOHNS HOPKINS UNIVERSITY",',
        '        "awardeeCity": "BALTIMORE",',
        '        "awardeeStateCode": "MD",',
        '        "dirAbbr": "ENG",',
        '        "divAbbr": "CBET",',
        '        "fundProgramName": "TRAILBLAZER",',
        '        "abstractText": "NON-TECHNICAL SUMMARY...",',
        "        ... 35 fields total",
        "      }",
        "    ]",
        "  }",
        "}",
      ]),
      spacer(120),

      // 3.6 Key Fields
      h2("3.6  Key Data Fields Returned"),
      twoColTable(
        ["Field Group", "Fields"],
        [
          ["Identity", "id (award number), transType, agency, activeAwd, cfdaNumber"],
          ["Financials", "fundsObligatedAmt, estimatedTotalAmt, fundsObligated (FY breakdown)"],
          ["Organization", "awardee, awardeeCity, awardeeStateCode, awardeeZipCode, ueiNumber"],
          ["People", "piFirstName, piLastName, piEmail, coPDPI (co-PIs), pdPIName, poName, poEmail"],
          ["Program", "fundProgramName, program, dirAbbr, divAbbr (Directorate & Division)"],
          ["Dates", "date (award date), startDate, expDate, initAmendmentDate, latestAmendmentDate"],
          ["Science", "abstractText, projectOutComesReport, publicationResearch"],
          ["Admin", "progEleCode, progRefCode, cfdaNumber, publicAccessMandate"],
        ],
        [2400, 7680]
      ),
      spacer(120),

      // 3.7 Pagination
      h2("3.7  Pagination Strategy"),
      body("NSF's API allows a maximum of 25 results per page and offset starts at 1 (not 0). It does not reliably return a totalCount, so pagination stops when a page returns fewer than 25 records:"),
      spacer(80),
      codeBlock([
        "def search_all_grants(max_records=500, **kwargs):",
        "    all_results = []",
        "    offset = 1            # NSF starts at 1, not 0",
        "    rpp    = 25           # NSF max per page",
        "",
        "    while len(all_results) < max_records:",
        "        data   = search_grants(offset=offset, rpp=rpp, **kwargs)",
        "        awards = data['response']['award']",
        "",
        "        if not awards:",
        "            break         # no more results",
        "",
        "        all_results.extend(awards)",
        "",
        "        if len(awards) < rpp:",
        "            break         # last page (partial)",
        "",
        "        offset += rpp",
        "        time.sleep(1)",
        "",
        "    return all_results[:max_records]",
      ]),
      spacer(80),
      body("For JHU 2024 with 74 grants, this required 3 pages:"),
      spacer(40),
      bullet("Page 1: offset=1,  rpp=25  →  25 grants"),
      bullet("Page 2: offset=26, rpp=25  →  25 grants"),
      bullet("Page 3: offset=51, rpp=25  →  24 grants  (< 25, stops here)"),
      spacer(120),

      pageBreak(),

      // ══════════════════════════════════════════════════════════════════════
      // 4. SIDE-BY-SIDE COMPARISON
      // ══════════════════════════════════════════════════════════════════════
      h1("4. NIH vs NSF — Side-by-Side Comparison"),
      twoColTable(
        ["Aspect", "NIH RePORTER", "NSF Awards API"],
        [
          ["Base URL", "api.reporter.nih.gov/v2", "api.nsf.gov/services/v1/awards.json"],
          ["HTTP Method", "POST (JSON body)", "GET (URL query params)"],
          ["Authentication", "None required", "None required"],
          ["Max per page", "500 records", "25 records"],
          ["Pagination offset", "Starts at 0", "Starts at 1"],
          ["Max total records", "15,000 per query", "No hard cap"],
          ["Total count returned", "Yes (meta.total)", "Unreliable / not always present"],
          ["Org exact match", "org_names (partial match ok)", "ueiNumber required for exact match"],
          ["Date format", "YYYY-MM-DD", "mm/dd/yyyy"],
          ["Rate limit", "1 req/sec (recommended)", "No official limit; 1/sec used"],
          ["Field selection", "include_fields (unreliable)", "printFields (works reliably)"],
          ["JHU grants (2024-2026)", "3,801 grants", "147 grants"],
          ["Focus area", "Medical / health sciences", "Fundamental / engineering research"],
        ],
        [3000, 3630, 3450]
      ),
      spacer(120),

      pageBreak(),

      // ══════════════════════════════════════════════════════════════════════
      // 5. CODE WALKTHROUGH
      // ══════════════════════════════════════════════════════════════════════
      h1("5. Complete Code Walkthrough"),

      h2("5.1  NIH: nih_grants.py"),

      h3("Step 1 — Build the criteria payload"),
      codeBlock([
        "def search_grants(fiscal_years=None, org_names=None,",
        "                  activity_codes=None, agencies=None, keywords=None, ...):",
        "",
        "    criteria = {}",
        "    if fiscal_years:    criteria['fiscal_years']    = fiscal_years",
        "    if org_names:       criteria['org_names']       = org_names",
        "    if activity_codes:  criteria['activity_codes']  = activity_codes",
        "    if agencies:        criteria['agencies']        = agencies",
        "    if keywords:",
        "        criteria['advanced_text_search'] = {",
        "            'operator': 'and',",
        "            'search_field': 'all',",
        "            'search_text': ' '.join(keywords)",
        "        }",
      ]),
      spacer(80),

      h3("Step 2 — Send POST request"),
      codeBlock([
        "    payload = {",
        "        'criteria': criteria,",
        "        'offset':   offset,",
        "        'limit':    min(limit, 500),",
        "        'sort_field': sort_field,",
        "        'sort_order': sort_order",
        "    }",
        "    resp = requests.post(",
        "        'https://api.reporter.nih.gov/v2/projects/search',",
        "        json=payload,",
        "        timeout=30",
        "    )",
        "    resp.raise_for_status()    # raises exception on 4xx/5xx",
        "    return resp.json()         # returns { meta: {...}, results: [...] }",
      ]),
      spacer(80),

      h3("Step 3 — Paginate all results"),
      codeBlock([
        "def search_all_grants(max_records=500, **kwargs):",
        "    all_results, offset = [], 0",
        "    limit = 500",
        "    while len(all_results) < max_records:",
        "        data  = search_grants(offset=offset, limit=limit, **kwargs)",
        "        total = data['meta']['total']",
        "        all_results.extend(data['results'])",
        "        if offset + limit >= total: break",
        "        offset += limit",
        "        time.sleep(1)",
        "    return all_results[:max_records]",
      ]),
      spacer(120),

      h2("5.2  NSF: nsf_grants.py"),

      h3("Step 1 — Build GET parameters"),
      codeBlock([
        "def search_grants(uei_number=None, date_start=None, date_end=None,",
        "                  keyword=None, amount_min=None, rpp=25, offset=1, ...):",
        "",
        "    params = {",
        "        'rpp':         min(rpp, 25),",
        "        'offset':      offset,",
        "        'printFields': ','.join(ALL_FIELDS),    # request all 50+ fields",
        "    }",
        "    if uei_number:  params['ueiNumber']  = uei_number",
        "    if date_start:  params['dateStart']  = date_start   # mm/dd/yyyy",
        "    if date_end:    params['dateEnd']    = date_end",
        "    if keyword:     params['keyword']    = keyword",
        "    if amount_min:  params['fundsObligatedAmtFrom'] = amount_min",
      ]),
      spacer(80),

      h3("Step 2 — Send GET request"),
      codeBlock([
        "    resp = requests.get(",
        "        'https://api.nsf.gov/services/v1/awards.json',",
        "        params=params,",
        "        timeout=30",
        "    )",
        "    resp.raise_for_status()",
        "    return resp.json()    # returns { response: { award: [...] } }",
      ]),
      spacer(80),

      h3("Step 3 — Paginate (stops on partial page)"),
      codeBlock([
        "def search_all_grants(max_records=500, **kwargs):",
        "    all_results, offset = [], 1    # NSF starts at 1",
        "    while len(all_results) < max_records:",
        "        data   = search_grants(offset=offset, rpp=25, **kwargs)",
        "        awards = data['response']['award']",
        "        if not awards: break",
        "        all_results.extend(awards)",
        "        if len(awards) < 25: break    # last page",
        "        offset += 25",
        "        time.sleep(1)",
        "    return all_results[:max_records]",
      ]),
      spacer(120),

      pageBreak(),

      // ══════════════════════════════════════════════════════════════════════
      // 6. DATA STORAGE & OUTPUT
      // ══════════════════════════════════════════════════════════════════════
      h1("6. Data Storage & Output Files"),

      h2("6.1  JSON — Raw Source of Truth"),
      body("All records are first saved to JSON as-is from the API response. This preserves nested structures and allows re-processing without re-fetching."),
      spacer(80),
      twoColTable(
        ["File", "Contents", "Records"],
        [
          ["jhu_grants_all.json", "All NIH grants FY2024-2026 (raw API response)", "3,801"],
          ["jhu_nsf_grants_all.json", "All NSF grants 2024-2026 (raw API response)", "147"],
        ],
        [3600, 4680, 1800]
      ),
      spacer(120),

      h2("6.2  Excel — Formatted Workbooks"),
      body("Nested JSON fields are flattened into tabular rows for each workbook. Both workbooks are built with openpyxl and contain multiple sheets."),
      spacer(80),
      twoColTable(
        ["File", "Sheets", "Key Contents"],
        [
          ["jhu_grants_2024_2026.xlsx", "Summary Dashboard", "Grants by fiscal year, activity code, NIH institute, top 5 awards"],
          ["", "All Grants", "3,801 rows with 45 flattened fields per grant"],
          ["", "Top 50 by Award", "50 highest-value grants ranked by award amount"],
          ["", "Activity Codes", "Reference table: 65 activity codes with full descriptions"],
          ["jhu_nsf_grants_2024_2026.xlsx", "Summary Dashboard", "Grants by year, transaction type, NSF Directorate/Division, top 5"],
          ["", "All Grants", "147 rows with 35 flattened fields per grant"],
          ["", "Top 25 by Award", "25 highest-value grants ranked by funds obligated"],
        ],
        [3400, 2600, 4080]
      ),
      spacer(120),

      h2("6.3  Flattening Nested Fields"),
      body("Both APIs return nested JSON objects. These are unpacked into flat columns for Excel. Key examples:"),
      spacer(80),
      codeBlock([
        "# NIH: organization is a nested dict",
        "org = grant.get('organization') or {}",
        "row['Organization'] = org.get('org_name')    # 'JOHNS HOPKINS UNIVERSITY'",
        "row['City']         = org.get('org_city')    # 'BALTIMORE'",
        "row['State']        = org.get('org_state')   # 'MD'",
        "",
        "# NIH: principal_investigators is a list of dicts — take first (contact PI)",
        "pi_list = grant.get('principal_investigators') or []",
        "pi      = pi_list[0] if pi_list else {}",
        "row['PI Full Name'] = pi.get('full_name')    # 'Daniel Abate-Daga'",
        "row['PI Title']     = pi.get('title')        # 'ASSOCIATE MEMBER'",
        "",
        "# NIH: agency_ic_admin is a nested dict",
        "ic = grant.get('agency_ic_admin') or {}",
        "row['IC Abbreviation'] = ic.get('abbreviation')  # 'NCI'",
        "row['IC Name']         = ic.get('name')           # 'National Cancer Institute'",
      ]),
      spacer(120),

      pageBreak(),

      // ══════════════════════════════════════════════════════════════════════
      // 7. FILE STRUCTURE
      // ══════════════════════════════════════════════════════════════════════
      h1("7. Project File Structure"),
      codeBlock([
        "NIH Pipeline/",
        "├── nih_grants.py              # NIH RePORTER API client",
        "│   ├── search_grants()        #   single paginated request",
        "│   ├── search_all_grants()    #   auto-paginate all results",
        "│   ├── search_publications()  #   linked publications endpoint",
        "│   └── save_to_json()         #   write results to .json",
        "│",
        "├── nsf_grants.py              # NSF Awards API client",
        "│   ├── search_grants()        #   single GET request",
        "│   ├── search_all_grants()    #   auto-paginate all results",
        "│   └── save_to_json()         #   write results to .json",
        "│",
        "├── build_excel.py             # Build NIH Excel workbook",
        "├── build_nsf_excel.py         # Build NSF Excel workbook",
        "├── add_codes_tab.py           # Add Activity Codes reference tab",
        "│",
        "├── jhu_grants_all.json        # Raw NIH data  (3,801 grants)",
        "├── jhu_nsf_grants_all.json    # Raw NSF data  (  147 grants)",
        "│",
        "├── jhu_grants_2024_2026.xlsx  # NIH Excel (4 sheets)",
        "└── jhu_nsf_grants_2024_2026.xlsx  # NSF Excel (3 sheets)",
      ]),
      spacer(120),

      // ══════════════════════════════════════════════════════════════════════
      // 8. QUICK REFERENCE
      // ══════════════════════════════════════════════════════════════════════
      h1("8. Quick Reference — Common Queries"),
      h2("8.1  NIH Examples"),
      codeBlock([
        "from nih_grants import search_grants, search_all_grants",
        "",
        "# All R01 grants from NCI in 2024",
        "search_grants(fiscal_years=[2024], agencies=['NCI'], activity_codes=['R01'])",
        "",
        "# All JHU grants across 3 years (paginated)",
        "search_all_grants(",
        "    max_records=5000,",
        "    fiscal_years=[2024, 2025, 2026],",
        "    org_names=['JOHNS HOPKINS UNIVERSITY']",
        ")",
        "",
        "# Grants over $1M mentioning 'CRISPR'",
        "search_grants(keywords=['CRISPR'], award_amount_min=1_000_000)",
        "",
        "# Specific PI",
        "search_grants(pi_names=[{'last_name': 'Smith', 'first_name': 'John'}])",
      ]),
      spacer(100),

      h2("8.2  NSF Examples"),
      codeBlock([
        "from nsf_grants import search_grants, search_all_grants",
        "",
        "# All JHU NSF grants in 2024 (using UEI for exact match)",
        "search_grants(uei_number='FTMTDMBR29C7', date_start='01/01/2024', date_end='12/31/2024')",
        "",
        "# All standard grants over $500K",
        "search_grants(",
        "    uei_number='FTMTDMBR29C7',",
        "    trans_type='Standard Grant',",
        "    amount_min=500_000",
        ")",
        "",
        "# Keyword search for machine learning grants",
        "search_grants(keyword='machine learning AND neural network', date_start='01/01/2024')",
        "",
        "# All grants from a specific PI",
        "search_grants(pi_name='Chen, Yun', date_start='01/01/2023')",
      ]),
      spacer(120),

      divider(),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 160, after: 80 },
        children: [new TextRun({ text: "End of Document", font: FONT, size: 20, italic: true, color: C.LIGHT_TEXT })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 40, after: 40 },
        children: [new TextRun({ text: "NIH RePORTER API  |  NSF Awards API  |  Johns Hopkins University  |  April 2026", font: FONT, size: 18, color: C.LIGHT_TEXT })],
      }),

    ],
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync("NIH_NSF_Data_Extraction_Guide.docx", buf);
  console.log("Saved: NIH_NSF_Data_Extraction_Guide.docx");
});
