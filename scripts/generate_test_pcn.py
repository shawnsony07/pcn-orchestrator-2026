"""
Generate a test PCN PDF for smoke-testing the pipeline.
Saves: test_pcn_INA219AIDR.pdf in the current directory.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

OUTPUT = "test_pcn_INA219AIDR.pdf"

doc = SimpleDocTemplate(
    OUTPUT,
    pagesize=A4,
    topMargin=20*mm, bottomMargin=20*mm,
    leftMargin=20*mm, rightMargin=20*mm,
)

styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    "Title", parent=styles["Heading1"],
    fontSize=16, spaceAfter=4, textColor=colors.HexColor("#1a1a2e")
)
subtitle_style = ParagraphStyle(
    "Sub", parent=styles["Normal"],
    fontSize=10, textColor=colors.HexColor("#555555"), spaceAfter=10
)
h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceAfter=4)
body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=8)
bold_body = ParagraphStyle("Bold", parent=body, fontName="Helvetica-Bold")
warning = ParagraphStyle(
    "Warning", parent=body,
    backColor=colors.HexColor("#fff3cd"),
    borderColor=colors.HexColor("#ffc107"),
    borderWidth=1, borderPadding=6,
)

story = []

# ── Header ──────────────────────────────────────────────────────────────────
story.append(Paragraph("PRODUCT CHANGE NOTIFICATION", title_style))
story.append(Paragraph(
    "Texas Instruments Incorporated · Semiconductor Division", subtitle_style
))
story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1a1a2e")))
story.append(Spacer(1, 6*mm))

# ── Meta table ───────────────────────────────────────────────────────────────
meta = [
    ["PCN Reference #:",  "TI-PCN-2026-0482"],
    ["Issue Date:",        "2026-08-20"],
    ["Effective Date:",    "2026-11-01"],
    ["Classification:",    "End-of-Life / Last-Time Buy"],
    ["Affected Part:",     "INA219AIDR"],
    ["Replacement Part(s):", "INA226AIDR  /  INA228AIDR"],
    ["Contact:",           "pcn-support@ti.com"],
]
meta_table = Table(meta, colWidths=[55*mm, 110*mm])
meta_table.setStyle(TableStyle([
    ("FONTNAME",    (0, 0), (0, -1), "Helvetica-Bold"),
    ("FONTSIZE",    (0, 0), (-1, -1), 10),
    ("TEXTCOLOR",   (0, 0), (0, -1), colors.HexColor("#1a1a2e")),
    ("ROWBACKGROUNDS", (0, 0), (-1, -1),
     [colors.HexColor("#f0f4f8"), colors.white]),
    ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
    ("TOPPADDING",  (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
]))
story.append(meta_table)
story.append(Spacer(1, 8*mm))

# ── Section 1 ────────────────────────────────────────────────────────────────
story.append(Paragraph("1.  Summary", h2))
story.append(Paragraph(
    "Texas Instruments is announcing the End-of-Life (EOL) of the "
    "<b>INA219AIDR</b> (12-bit I²C current/power monitor, SOIC-8 package). "
    "The part will enter Last-Time Buy (LTB) status on <b>2026-11-01</b> and "
    "will no longer be manufactured after <b>2027-02-28</b>. "
    "Customers must place final orders before the LTB cutoff date.",
    body
))

# ── Section 2 ────────────────────────────────────────────────────────────────
story.append(Paragraph("2.  Reason for Change", h2))
story.append(Paragraph(
    "The INA219AIDR has been superseded by next-generation current-sensing "
    "devices with improved accuracy, wider voltage range, and additional "
    "alert functionality. Texas Instruments is consolidating its "
    "current-sensing product line to focus investment on the INA22x family.",
    body
))

# ── Section 3 ────────────────────────────────────────────────────────────────
story.append(Paragraph("3.  Affected Part Numbers", h2))
parts = [
    ["Part Number", "Description", "Package", "Status"],
    ["INA219AIDR", "12-bit I²C Current/Power Monitor", "SOIC-8 (150mil)", "EOL — Last-Time Buy"],
]
pt = Table(parts, colWidths=[38*mm, 72*mm, 38*mm, 42*mm])
pt.setStyle(TableStyle([
    ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
    ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
    ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE",    (0, 0), (-1, -1), 9),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f9f9f9"), colors.white]),
    ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
    ("TOPPADDING",  (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
]))
story.append(pt)
story.append(Spacer(1, 6*mm))

# ── Section 4 ────────────────────────────────────────────────────────────────
story.append(Paragraph("4.  Recommended Replacement", h2))
story.append(Paragraph(
    "The following parts are pin-compatible, I²C-compatible drop-in replacements "
    "for the INA219AIDR. Firmware changes are limited to updating the I²C "
    "register map for the calibration register (REG_CALIB) and alert "
    "configuration registers present only on the INA226/INA228.",
    body
))
rep = [
    ["Replacement Part", "Key Improvement", "Package", "Drop-in?"],
    ["INA226AIDR", "16-bit resolution, programmable alert, ±0.1% gain error", "SOIC-8", "Yes — pin compatible"],
    ["INA228AIDR", "20-bit resolution, integrated temperature sensor, SPI option", "SOIC-8", "Yes — pin compatible"],
]
rt = Table(rep, colWidths=[38*mm, 82*mm, 22*mm, 36*mm])
rt.setStyle(TableStyle([
    ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
    ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
    ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE",    (0, 0), (-1, -1), 9),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f9f9f9"), colors.white]),
    ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
    ("TOPPADDING",  (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("WORDWRAP",    (0, 0), (-1, -1), True),
]))
story.append(rt)
story.append(Spacer(1, 6*mm))

# ── Section 5 ────────────────────────────────────────────────────────────────
story.append(Paragraph("5.  HAL / Firmware Impact", h2))
story.append(Paragraph(
    "The HAL driver for the INA219AIDR (<b>hal_ina219.h</b> / <b>hal_ina219.c</b>) "
    "must be updated to target the INA226AIDR. Affected register definitions:",
    body
))
story.append(Paragraph("• <b>INA219_REG_CALIBRATION (0x05)</b> → recalculate per INA226 datasheet §8.6", body))
story.append(Paragraph("• <b>INA219_CONFIG_BADCRES_12BIT</b> → remove; INA226 uses 16-bit fixed resolution", body))
story.append(Paragraph("• Add <b>INA226_REG_MASK_ENABLE (0x06)</b> and <b>INA226_REG_ALERT_LIMIT (0x07)</b>", body))
story.append(Paragraph(
    "No changes required to I²C bus address, supply voltage range, or PCB footprint.",
    body
))
story.append(Spacer(1, 6*mm))

# ── Section 6 ────────────────────────────────────────────────────────────────
story.append(Paragraph("6.  Action Required", h2))
story.append(Paragraph(
    "⚠  <b>Customers must complete the following before 2026-11-01:</b>",
    warning
))
story.append(Spacer(1, 3*mm))
story.append(Paragraph("1. Review BOM and identify all assemblies using INA219AIDR.", body))
story.append(Paragraph("2. Qualify INA226AIDR or INA228AIDR as the replacement.", body))
story.append(Paragraph("3. Update HAL driver source files and rebuild firmware.", body))
story.append(Paragraph("4. Place Last-Time Buy orders for remaining INA219AIDR stock if needed.", body))
story.append(Spacer(1, 6*mm))

# ── Footer ───────────────────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#aaaaaa")))
story.append(Spacer(1, 3*mm))
story.append(Paragraph(
    "This document is issued by Texas Instruments Incorporated for informational purposes. "
    "© 2026 Texas Instruments Incorporated. All rights reserved. "
    "PCN-2026-0482 · Rev 1.0",
    ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8,
                   textColor=colors.HexColor("#888888"))
))

doc.build(story)
print(f"OK Generated: {OUTPUT}")
print("  Attach this file to an email and send it to the watched Gmail address.")
print("  Subject line suggestion:")
print('  "[PCN] TI INA219AIDR End-of-Life Notice -- Action Required"')
