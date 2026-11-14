# -*- coding: utf-8 -*-
"""
وحدة تصدير محادثات واتساب ونتائج التحليل إلى صيغ:
- HTML (واجهة تشبه واتساب باللغة العربية RTL وجاهزة للطباعة)
- CSV (بترميز UTF-8 BOM متوافق مع Microsoft Excel)
- PDF (باستخدام ReportLab مع تشكيل الحروف العربية والاتجاه RTL)
"""

import csv
import html
import os
from datetime import datetime
from typing import List, Optional

from whatsapp_parser import ChatInfo, ChatMessage
from sqlite_forensics import ForensicArtifact

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    HAS_ARABIC_SHAPING = True
except ImportError:
    HAS_ARABIC_SHAPING = False


def _shape_arabic(text: str) -> str:
    """إعادة تشكيل النص العربي واتجاهه عند التصدير إلى PDF."""
    if not text:
        return ""
    if HAS_ARABIC_SHAPING:
        try:
            reshaped = arabic_reshaper.reshape(text)
            return get_display(reshaped)
        except Exception:
            return text
    return text


def _find_arabic_ttf_font() -> Optional[str]:
    """البحث عن خط TrueType يدعم العربية على Windows / Linux / macOS."""
    candidates = [
        # Windows
        r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Tahoma.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class ChatExporter:
    """مصدر المحادثات إلى HTML و CSV و PDF."""

    @staticmethod
    def export_csv(chat: ChatInfo, messages: List[ChatMessage], output_path: str) -> str:
        """تصدير المحادثة إلى ملف CSV متوافق مع Excel."""
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "المعرف",
                "المحادثة",
                "المرسل",
                "رقم الهاتف",
                "الاتجاه",
                "التاريخ والوقت",
                "نوع الرسالة",
                "الحالة",
                "محذوفة؟",
                "الرد على",
                "النص",
                "مسار الوسائط",
            ])
            for m in messages:
                writer.writerow([
                    m.row_id,
                    chat.display_name,
                    m.sender_name,
                    m.sender_phone,
                    "مرسلة" if m.from_me else "واردة",
                    m.timestamp_str,
                    m.type_label,
                    m.status_label,
                    "نعم" if m.is_deleted else "لا",
                    f"{m.quoted_sender}: {m.quoted_text}" if m.quoted_text else "",
                    m.text,
                    m.media_full_path or m.media_rel_path,
                ])
        return output_path

    @staticmethod
    def export_html(chat: ChatInfo, messages: List[ChatMessage], output_path: str) -> str:
        """تصدير المحادثة إلى صفحة HTML بتصميم فقاعات واتساب (RTL)."""
        bubbles_html: List[str] = []
        for m in messages:
            side_cls = "sent" if m.from_me else "recv"
            del_cls = " deleted" if m.is_deleted else ""
            sender_escaped = html.escape(m.sender_name)
            text_escaped = html.escape(m.text).replace("\n", "<br>")
            time_escaped = html.escape(f"{m.date_str} {m.time_str}".strip())
            status_escaped = html.escape(m.status_icon if m.from_me else "")

            quote_block = ""
            if m.quoted_text:
                q_s = html.escape(m.quoted_sender)
                q_t = html.escape(m.quoted_text)
                quote_block = (
                    f'<div class="quote"><strong>{q_s}</strong><div>{q_t}</div></div>'
                )

            media_block = ""
            if m.media_full_path and os.path.isfile(m.media_full_path):
                ext = os.path.splitext(m.media_full_path)[1].lower()
                uri = "file:///" + m.media_full_path.replace("\\", "/").lstrip("/")
                if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                    media_block = f'<div class="media"><img src="{html.escape(uri)}" alt="media"></div>'
                else:
                    media_block = (
                        f'<div class="media-link">📎 <a href="{html.escape(uri)}">'
                        f'{html.escape(os.path.basename(m.media_full_path))}</a></div>'
                    )
            elif m.media_rel_path:
                media_block = f'<div class="media-link">📎 {html.escape(m.media_rel_path)}</div>'

            bubbles_html.append(
                f"""
                <div class="msg-row {side_cls}">
                  <div class="bubble{del_cls}">
                    <div class="sender">{sender_escaped}</div>
                    {quote_block}
                    {media_block}
                    <div class="body">{text_escaped}</div>
                    <div class="meta">{time_escaped} <span class="ticks">{status_escaped}</span></div>
                  </div>
                </div>
                """
            )

        doc = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>محادثة: {html.escape(chat.display_name)}</title>
<style>
  body {{ font-family: 'Segoe UI', Tahoma, Arial, sans-serif; background: #efeae2; margin: 0; padding: 0; color: #111b21; }}
  .header {{ background: #008069; color: #fff; padding: 16px 24px; position: sticky; top: 0; box-shadow: 0 2px 6px rgba(0,0,0,0.15); }}
  .header h1 {{ margin: 0; font-size: 18px; }}
  .header p {{ margin: 4px 0 0; font-size: 12px; opacity: 0.9; }}
  .chat-container {{ max-width: 860px; margin: 20px auto; padding: 0 16px 40px; }}
  .msg-row {{ display: flex; margin-bottom: 10px; }}
  .msg-row.sent {{ justify-content: flex-start; }}
  .msg-row.recv {{ justify-content: flex-end; }}
  .bubble {{ max-width: 72%; padding: 8px 12px; border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.12); background: #fff; }}
  .msg-row.sent .bubble {{ background: #d9fdd3; }}
  .bubble.deleted {{ background: #fdecec; border: 1px dashed #d9534f; }}
  .sender {{ font-size: 12px; font-weight: bold; color: #008069; margin-bottom: 4px; }}
  .quote {{ background: rgba(0,0,0,0.06); border-right: 3px solid #00a884; padding: 5px 8px; border-radius: 5px; margin-bottom: 6px; font-size: 12px; }}
  .body {{ font-size: 14px; line-height: 1.55; word-wrap: break-word; }}
  .meta {{ font-size: 11px; color: #667781; text-align: left; margin-top: 4px; }}
  .ticks {{ color: #53bdeb; font-weight: bold; }}
  .media img {{ max-width: 280px; border-radius: 8px; margin-bottom: 6px; display: block; }}
  .media-link {{ font-size: 12px; background: rgba(0,0,0,0.05); padding: 4px 8px; border-radius: 6px; margin-bottom: 6px; }}
</style>
</head>
<body>
  <div class="header">
    <h1>{html.escape(chat.display_name)} ({html.escape(chat.phone_or_jid)})</h1>
    <p>عدد الرسائل المصدرة: {len(messages)} | تاريخ التصدير: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
  </div>
  <div class="chat-container">
    {"".join(bubbles_html)}
  </div>
</body>
</html>"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(doc)
        return output_path

    @staticmethod
    def export_pdf(chat: ChatInfo, messages: List[ChatMessage], output_path: str) -> str:
        """تصدير المحادثة إلى ملف PDF مع دعم الخط العربي."""
        if not HAS_REPORTLAB:
            raise RuntimeError(
                "مكتبة reportlab غير مثبتة. قم بتثبيتها عبر: pip install reportlab arabic-reshaper python-bidi"
            )

        font_path = _find_arabic_ttf_font()
        font_name = "Helvetica"
        if font_path:
            pdfmetrics.registerFont(TTFont("ArabicUI", font_path))
            font_name = "ArabicUI"

        doc = SimpleDocTemplate(output_path, pagesize=A4, rightMargin=32, leftMargin=32, topMargin=32, bottomMargin=32)
        title_style = ParagraphStyle(
            "TitleAr",
            fontName=font_name,
            fontSize=14,
            leading=20,
            alignment=2,  # محاذاة لليمين
            textColor=colors.HexColor("#008069"),
        )
        msg_style_sent = ParagraphStyle(
            "MsgSent",
            fontName=font_name,
            fontSize=10,
            leading=15,
            alignment=2,
            backColor=colors.HexColor("#E7FFDB"),
            borderPadding=6,
        )
        msg_style_recv = ParagraphStyle(
            "MsgRecv",
            fontName=font_name,
            fontSize=10,
            leading=15,
            alignment=2,
            backColor=colors.HexColor("#F5F5F5"),
            borderPadding=6,
        )

        story = [
            Paragraph(
                html.escape(_shape_arabic(f"تصدير محادثة واتساب: {chat.display_name} ({len(messages)} رسالة)")),
                title_style,
            ),
            Spacer(1, 12),
        ]

        for m in messages:
            header_line = f"{m.sender_name}  |  {m.timestamp_str}  {m.status_icon}"
            body_lines = [header_line]
            if m.quoted_text:
                body_lines.append(f"[رد على {m.quoted_sender}]: {m.quoted_text}")
            body_lines.append(m.text)
            if m.media_rel_path:
                body_lines.append(f"[ملف مرفق]: {os.path.basename(m.media_rel_path)}")

            shaped_text = "<br/>".join(
                html.escape(_shape_arabic(line)) for line in body_lines
            )
            style = msg_style_sent if m.from_me else msg_style_recv
            story.append(Paragraph(shaped_text, style))
            story.append(Spacer(1, 6))

        doc.build(story)
        return output_path

    @staticmethod
    def export_forensics_csv(artifacts: List[ForensicArtifact], output_path: str) -> str:
        """تصدير بقايا SQLite المستخرجة إلى CSV."""
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["المصدر", "الصفحة / الإطار", "الإزاحة (Offset)", "المعرف المرتبط", "الثقة", "النص المستخرج"])
            for art in artifacts:
                writer.writerow([
                    art.source,
                    art.page_or_frame,
                    hex(art.offset),
                    art.related_jid,
                    art.confidence,
                    art.extracted_text,
                ])
        return output_path
