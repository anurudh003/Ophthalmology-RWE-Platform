import io
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle

def test_pdf():
    buf = "test_output.pdf"
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    small_style = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=8)

    header_style = ParagraphStyle(
        "TrajHeader",
        parent=small_style,
        fontSize=8,
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )

    traj_data = [
        ["Visit", "Branch Retinal Vein Occlusion (BRVO)", "Central Retinal Vein Occlusion (CRVO)", "Diabetic Macular Oedema (DMO)", "Neovascular AMD (nAMD)"],
        ["1.0", "62.0", "50.9", "63.3", "48.8"],
        ["2.0", "65.5", "54.2", "66.7", "52.2"],
    ]
    
    # Try the exact code we used
    traj_data_p = []
    traj_data_p.append([Paragraph(str(c), header_style) for c in traj_data[0]])
    traj_data_p.extend(traj_data[1:])

    n_cols = len(traj_data[0])
    col_widths = [2.0 * cm] + [(15.0 * cm) / (n_cols - 1)] * (n_cols - 1)

    traj_tbl = Table(traj_data_p, colWidths=col_widths)
    traj_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#2980b9")),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.grey),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
    ]))

    doc.build([traj_tbl])
    print("PDF built!")

test_pdf()
