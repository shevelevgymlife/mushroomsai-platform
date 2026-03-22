import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT


GOLD = HexColor("#00f5ff")
DARK = HexColor("#080808")
LIGHT = HexColor("#e8e8e8")


def generate_recipe_pdf(title: str, content: str, user_name: str = "Пользователь") -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=24,
        textColor=GOLD,
        spaceAfter=20,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=GOLD,
        spaceBefore=16,
        spaceAfter=8,
        fontName="Helvetica-Bold",
    )
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["Normal"],
        fontSize=11,
        textColor=black,
        spaceAfter=8,
        leading=16,
    )
    small_style = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontSize=9,
        textColor=HexColor("#888888"),
        alignment=TA_CENTER,
    )

    story = []
    story.append(Paragraph("MushroomsAI", title_style))
    story.append(Paragraph(f"Персональный протокол для: {user_name}", small_style))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(title, heading_style))
    story.append(Spacer(1, 0.3 * cm))

    for line in content.split("\n"):
        if line.strip():
            if line.startswith("##"):
                story.append(Paragraph(line.replace("##", "").strip(), heading_style))
            else:
                story.append(Paragraph(line, body_style))
        else:
            story.append(Spacer(1, 0.2 * cm))

    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("mushroomsai.ru | Евгений Шевелев, эксперт по фунготерапии", small_style))

    doc.build(story)
    return buffer.getvalue()
