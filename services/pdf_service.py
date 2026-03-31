import io
from xml.sax.saxutils import escape

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
    story.append(Paragraph("NEUROFUNGI AI", title_style))
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


def generate_wellness_journal_pdf(user_name: str, sections: list[tuple[str, str]]) -> bytes:
    """Экспорт сводки дневника фунготерапии (структура как у рецепта; кириллица — через стандартные стили)."""
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
        "WjTitle",
        parent=styles["Title"],
        fontSize=22,
        textColor=GOLD,
        spaceAfter=16,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )
    heading_style = ParagraphStyle(
        "WjHead",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=GOLD,
        spaceBefore=12,
        spaceAfter=6,
        fontName="Helvetica-Bold",
    )
    body_style = ParagraphStyle(
        "WjBody",
        parent=styles["Normal"],
        fontSize=10,
        textColor=black,
        spaceAfter=6,
        leading=14,
    )
    small_style = ParagraphStyle(
        "WjSmall",
        parent=styles["Normal"],
        fontSize=9,
        textColor=HexColor("#666666"),
        alignment=TA_CENTER,
    )
    story = []
    story.append(Paragraph("NEUROFUNGI — дневник терапии", title_style))
    story.append(Paragraph(f"Пользователь: {escape(user_name)}", small_style))
    story.append(Spacer(1, 0.4 * cm))
    for title, body in sections:
        story.append(Paragraph(title, heading_style))
        for line in (body or "").split("\n"):
            line = line.strip()
            if line:
                story.append(Paragraph(escape(line), body_style))
            else:
                story.append(Spacer(1, 0.12 * cm))
    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph("mushroomsai.ru | Не медицинское заключение; самонаблюдение.", small_style))
    doc.build(story)
    return buffer.getvalue()


def generate_wellness_admin_overview_pdf(sections: list[tuple[str, str]]) -> bytes:
    """Сводка дневника по платформе для администратора (тот же PDF-движок)."""
    return generate_wellness_journal_pdf("Сводка платформы (админ)", sections)
