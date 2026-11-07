"""数据导出模块：把 DataFrame 导出成各种格式。

支持：CSV / Excel / Markdown / Word / PDF
所有函数返回文件的字节内容（bytes），方便 Streamlit 直接下载。
"""

from __future__ import annotations

import io
from typing import Optional

import pandas as pd


# ============================ CSV ============================

def to_csv(df: pd.DataFrame) -> bytes:
    """导出为 CSV（utf-8-sig，Excel 打开不乱码）。"""
    buf = io.StringIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    return buf.getvalue().encode("utf-8-sig")


# ============================ Excel ============================

def to_excel(df: pd.DataFrame, sheet_name: str = "数据") -> bytes:
    """导出为 Excel（.xlsx）。"""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return buf.getvalue()


# ============================ Markdown ============================

def to_markdown(df: pd.DataFrame, title: Optional[str] = None) -> str:
    """导出为 Markdown 表格（Typora 可直接打开）。"""
    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    lines.append(df.to_markdown(index=False))
    return "\n".join(lines)


# ============================ Word ============================

def to_word(df: pd.DataFrame, title: Optional[str] = None) -> bytes:
    """导出为 Word 文档（.docx），包含标题和表格。"""
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = Document()

    if title:
        heading = doc.add_heading(title, level=1)

    # 表格
    table = doc.add_table(rows=1, cols=len(df.columns))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Light Grid Accent 1"

    # 表头
    hdr_cells = table.rows[0].cells
    for i, col in enumerate(df.columns):
        hdr_cells[i].text = str(col)
        for run in hdr_cells[i].paragraphs[0].runs:
            run.bold = True

    # 数据行
    for _, row in df.iterrows():
        cells = table.add_row().cells
        for i, col in enumerate(df.columns):
            cells[i].text = str(row[col])

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ============================ PDF ============================

def _register_chinese_font():
    """注册中文字体（Windows 自带宋体/微软雅黑）。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    # 按优先级尝试常见中文字体
    candidates = [
        ("msyh", r"C:\Windows\Fonts\msyh.ttc"),       # 微软雅黑
        ("simsun", r"C:\Windows\Fonts\simsun.ttc"),   # 宋体
    ]
    for name, path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                return name
            except Exception:
                continue
    return "Helvetica"  # 兜底，不支持中文


def to_pdf(df: pd.DataFrame, title: Optional[str] = None) -> bytes:
    """导出为 PDF，自动注册中文字体。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    font_name = _register_chinese_font()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),  # 横向，表格列多时够用
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    elements = []

    # 标题
    if title:
        title_style = ParagraphStyle(
            "Title",
            parent=getSampleStyleSheet()["Title"],
            fontName=font_name,
            fontSize=16,
        )
        elements.append(Paragraph(title, title_style))
        elements.append(Spacer(1, 0.5 * cm))

    # 把数据转成嵌套列表
    data = [list(df.columns)] + df.astype(str).values.tolist()

    # 列宽自适应（按内容长度估算）
    col_widths = []
    for i, col in enumerate(df.columns):
        max_len = max(len(str(col)), df[col].astype(str).str.len().max() if len(df) else 0)
        col_widths.append(min(max(max_len * 0.35, 2), 5) * cm)

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#D9E2F3")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(table)

    doc.build(elements)
    return buf.getvalue()


# ============================ 统一入口 ============================

def export_dataframe(
    df: pd.DataFrame,
    fmt: str,
    title: Optional[str] = None,
) -> bytes:
    """统一导出入口，fmt: csv / excel / markdown / word / pdf。

    markdown 返回 str，其余返回 bytes。
    """
    fmt = fmt.lower()
    if fmt == "csv":
        return to_csv(df)
    if fmt == "excel":
        return to_excel(df)
    if fmt == "markdown":
        return to_markdown(df, title).encode("utf-8")
    if fmt == "word":
        return to_word(df, title)
    if fmt == "pdf":
        return to_pdf(df, title)
    raise ValueError(f"不支持的导出格式: {fmt}")


EXPORT_FORMATS = {
    "csv": {"label": "CSV", "ext": "csv", "mime": "text/csv"},
    "excel": {"label": "Excel", "ext": "xlsx", "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "markdown": {"label": "Markdown", "ext": "md", "mime": "text/markdown"},
    "word": {"label": "Word", "ext": "docx", "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "pdf": {"label": "PDF", "ext": "pdf", "mime": "application/pdf"},
}
