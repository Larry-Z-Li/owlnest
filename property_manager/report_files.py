"""The cash-flow report (cash_flow.py) as an Excel file or a PDF, for her accountant.

The Excel file has two sheets: "Cash flow" (the report, with real numbers the
accountant can work with) and "Transactions" (every transaction it adds up).
The PDF is the report on one or more pages, landscape, sized to fit its columns.
"""

import sys
from io import BytesIO
from pathlib import Path

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import categories
from .cash_flow import BY_LLC, BY_MONTH, Report
from .money import format_money

# Shows negative amounts in red, with the minus sign, like the app does.
_NUMBER_FORMAT = '#,##0.00;[Red]-#,##0.00'
_HEADING_FILL = PatternFill("solid", fgColor="E7EBF0")
_RESULT_FILL = PatternFill("solid", fgColor="DCE6F2")
_TOP_LINE = Border(top=Side(style="thin"))


def file_name(report: Report, extension: str) -> str:
    """ "Cash flow - All properties - 2026-01-01 to 2026-12-31.xlsx" """
    return f"Cash flow - {report.property_name} - {report.start} to {report.end}.{extension}"


def describe(report: Report) -> str:
    """ "All properties, Jan 1, 2026 to Dec 31, 2026, by month" """
    return (
        f"{report.property_name}, {_nice(report.start)} to {_nice(report.end)}, "
        + ("by month" if report.interval == BY_MONTH else "by LLC" if report.interval == BY_LLC else "by property")
    )


# ---------- Excel ----------


def excel(report: Report) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Cash flow"
    sheet["A1"] = "Cash flow"
    sheet["A1"].font = Font(bold=True, size=14)
    sheet["A2"] = describe(report)
    sheet.append([])
    sheet.append([""] + report.columns)
    header_row = sheet.max_row
    for cell in sheet[header_row]:
        cell.font = Font(bold=True)
        cell.fill = _HEADING_FILL
        cell.alignment = Alignment(horizontal="right" if cell.column > 1 else "left")

    for section in report.sections:
        sheet.append([section.heading])
        sheet.cell(sheet.max_row, 1).font = Font(bold=True)
        for row in section.rows:
            label = row.label if row.kind != "category" else "    " + row.label
            sheet.append([label] + [cents / 100 for cents in row.values])
            for cell in sheet[sheet.max_row]:
                if cell.column > 1:
                    cell.number_format = _NUMBER_FORMAT
                if row.kind != "category":
                    cell.font = Font(bold=True)
                    cell.border = _TOP_LINE
                if row.kind == "result":
                    cell.fill = _RESULT_FILL
        sheet.append([])

    sheet.column_dimensions["A"].width = 38
    for number in range(2, len(report.columns) + 2):
        sheet.column_dimensions[get_column_letter(number)].width = 14
    sheet.freeze_panes = sheet.cell(header_row + 1, 2)

    _transactions_sheet(book, report)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def _transactions_sheet(book: Workbook, report: Report) -> None:
    sheet = book.create_sheet("Transactions")
    sheet.append(["Date", "Property", "Tenant", "Purpose", "Group", "Details", "Maintenance request", "Amount"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = _HEADING_FILL
    for transaction in report.transactions:
        sheet.append(
            [
                transaction.transaction_date,
                transaction.property.nickname if transaction.property else transaction.llc.name if transaction.llc else "",
                transaction.tenant_name,
                transaction.category,
                categories.group_of(transaction.category),
                transaction.details,
                transaction.request.title if transaction.request else "",
                transaction.amount_cents / 100,
            ]
        )
        sheet.cell(sheet.max_row, 1).number_format = "yyyy-mm-dd"
        sheet.cell(sheet.max_row, 8).number_format = _NUMBER_FORMAT
    for letter, width in zip("ABCDEFGH", (12, 18, 24, 26, 26, 36, 28, 14)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"


# ---------- PDF ----------

# Fonts that can print any name, including Chinese ones: (regular, bold, extra font
# for characters the first two don't have). The Dockerfile installs the Linux ones.
_FONTS = {
    "darwin": (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ),
    "win32": ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/simhei.ttf"),
    "linux": (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    ),
}

LABEL_WIDTH = 46  # mm, for the first column
NARROWEST_COLUMN = 15  # mm; below this, a bigger page is used (a year by month fits on Letter)


def pdf(report: Report, business_name: str = "") -> bytes:
    numbers = len(report.columns)
    # Landscape Letter if the columns fit; otherwise landscape Tabloid (11 x 17 inches, in mm).
    page = "Letter"
    if (279.4 - 20 - LABEL_WIDTH) / numbers < NARROWEST_COLUMN:
        page = (279.4, 431.8)
    document = FPDF(orientation="landscape", format=page)
    document.set_margins(10, 12, 10)
    document.set_auto_page_break(auto=True, margin=12)
    document.set_title("Cash flow")
    font = _set_up_fonts(document)
    text = _text_for(font)

    def set_font(style: str, font_size: float) -> None:
        _set_font(document, font, style, font_size)

    column = (document.w - 20 - LABEL_WIDTH) / numbers
    size = 8 if column >= 18 else 7

    def header():
        document.set_fill_color(231, 235, 240)
        set_font("B", size)
        document.cell(LABEL_WIDTH, 6, "", fill=True)
        for label in report.columns:
            set_font("B", size)  # (again for every cell: see _set_font)
            document.cell(column, 6, text(label), align="R", fill=True)
        document.ln()

    document.add_page()
    set_font("B", 15)
    document.cell(0, 8, "Cash flow", new_x="LMARGIN", new_y="NEXT")
    set_font("", 10)
    if business_name:
        document.cell(0, 5, text(business_name), new_x="LMARGIN", new_y="NEXT")
    document.cell(0, 5, text(describe(report)), new_x="LMARGIN", new_y="NEXT")
    document.ln(3)
    header()

    for section in report.sections:
        if document.get_y() > document.h - 40:
            document.add_page()
            header()
        set_font("B", size + 1)
        document.cell(0, 6, text(section.heading), new_x="LMARGIN", new_y="NEXT")
        for row in section.rows:
            bold = row.kind != "category"
            set_font("B" if bold else "", size)
            if row.kind == "result":
                document.set_fill_color(220, 230, 242)
            border = "T" if bold else 0
            label = row.label if bold else "   " + row.label
            document.cell(LABEL_WIDTH, 5, text(label), border=border, fill=row.kind == "result")
            for cents in row.values:
                if cents < 0:
                    document.set_text_color(166, 27, 27)  # red, for money out
                else:
                    document.set_text_color(0, 0, 0)
                document.cell(
                    column, 5, text(format_money(cents) if cents else "–"), align="R", border=border,
                    fill=row.kind == "result",
                )
            document.set_text_color(0, 0, 0)
            document.ln()
        document.ln(2)

    set_font("", 7)
    document.cell(
        0, 5, text(f"{report.transaction_count} transactions. Transfers aren't part of net cash flow."),
        new_x="LMARGIN", new_y="NEXT",
    )
    return bytes(document.output())


def _set_font(document: FPDF, font: str, style: str, size: float) -> None:
    """Set the font, even if fpdf2 thinks it's already set.

    After text that needed the extra font (Chinese characters), fpdf2 2.8 can
    think the page is still in the regular font when it isn't, and the next text
    comes out garbled (seen on the Linux server, with a Chinese property name).
    Switching to another size and back makes it really set the font.
    """
    document.set_font(font, style, size + 1)
    document.set_font(font, style, size)


def _set_up_fonts(document: FPDF) -> str:
    """Use Arial (plus a font with Chinese characters) if the computer has them; returns the font's name."""
    regular, bold, extra = _FONTS.get(sys.platform, (None, None, None))
    if regular and Path(regular).exists() and Path(bold).exists():
        document.add_font("Body", "", regular)
        document.add_font("Body", "B", bold)
        if extra and Path(extra).exists():
            document.add_font("Extra", "", extra)
            document.set_fallback_fonts(["Extra"], exact_match=False)
        return "Body"
    return "helvetica"  # built into PDFs, but only has Western European letters


def _text_for(font: str):
    """With the built-in font, replace characters it can't print instead of failing."""
    if font == "helvetica":
        return lambda value: str(value).replace("–", "-").encode("latin-1", "replace").decode("latin-1")
    return str


def _nice(day) -> str:
    return f"{day:%b} {day.day}, {day.year}"
