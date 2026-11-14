# -*- coding: utf-8 -*-
"""
ملف الإعدادات والثوابت العامة لبرنامج استرجاع الملفات ومحادثات واتساب.
يحتوي على بصمات الملفات (Magic Bytes) وخرائط حالات وأنواع رسائل واتساب.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class FileSignature:
    """تعريف بصمة ملف للاسترجاع بطريقة File Carving."""
    ext: str
    category: str          # صور، فيديو، مستندات، صوتيات، قواعد بيانات
    header: bytes
    footer: Optional[bytes] = None
    header_offset: int = 0
    max_size: int = 25 * 1024 * 1024  # الحد الأقصى الافتراضي 25 ميجابايت
    description: str = ""


# بصمات الملفات المدعومة للاسترجاع من الأقراص أو صور الأقراص
FILE_SIGNATURES: List[FileSignature] = [
    # --- الصور ---
    FileSignature(
        ext="jpg",
        category="صور",
        header=b"\xFF\xD8\xFF",
        footer=b"\xFF\xD9",
        max_size=20 * 1024 * 1024,
        description="صورة JPEG",
    ),
    FileSignature(
        ext="png",
        category="صور",
        header=b"\x89PNG\r\n\x1a\n",
        footer=b"IEND\xAE\x42\x60\x82",
        max_size=20 * 1024 * 1024,
        description="صورة PNG",
    ),
    FileSignature(
        ext="gif",
        category="صور",
        header=b"GIF89a",
        footer=b"\x00;",
        max_size=15 * 1024 * 1024,
        description="صورة متحركة GIF",
    ),
    FileSignature(
        ext="gif",
        category="صور",
        header=b"GIF87a",
        footer=b"\x00;",
        max_size=15 * 1024 * 1024,
        description="صورة GIF87a",
    ),
    FileSignature(
        ext="webp",
        category="صور",
        header=b"RIFF",  # يتم التحقق من وجود WEBP عند الإزاحة 8
        footer=None,
        max_size=15 * 1024 * 1024,
        description="صورة WebP",
    ),
    FileSignature(
        ext="bmp",
        category="صور",
        header=b"BM",
        footer=None,
        max_size=20 * 1024 * 1024,
        description="صورة BMP",
    ),

    # --- الفيديو ---
    FileSignature(
        ext="mp4",
        category="فيديو",
        header=b"ftyp",
        header_offset=4,
        footer=None,
        max_size=150 * 1024 * 1024,
        description="فيديو MP4 / MOV / M4A",
    ),
    FileSignature(
        ext="avi",
        category="فيديو",
        header=b"RIFF",  # يتم التحقق من وجود AVI عند الإزاحة 8
        footer=None,
        max_size=150 * 1024 * 1024,
        description="فيديو AVI",
    ),
    FileSignature(
        ext="mkv",
        category="فيديو",
        header=b"\x1A\x45\xDF\xA3",
        footer=None,
        max_size=150 * 1024 * 1024,
        description="فيديو MKV / WebM",
    ),

    # --- الصوتيات (بما فيها رسائل واتساب الصوتية Opus/OGG) ---
    FileSignature(
        ext="ogg",
        category="صوتيات",
        header=b"OggS",
        footer=None,
        max_size=25 * 1024 * 1024,
        description="ملف صوتي OGG / WhatsApp Opus",
    ),
    FileSignature(
        ext="mp3",
        category="صوتيات",
        header=b"ID3",
        footer=None,
        max_size=25 * 1024 * 1024,
        description="ملف صوتي MP3",
    ),
    FileSignature(
        ext="wav",
        category="صوتيات",
        header=b"RIFF",  # يتم التحقق من وجود WAVE عند الإزاحة 8
        footer=None,
        max_size=50 * 1024 * 1024,
        description="ملف صوتي WAV",
    ),
    FileSignature(
        ext="flac",
        category="صوتيات",
        header=b"fLaC",
        footer=None,
        max_size=50 * 1024 * 1024,
        description="ملف صوتي FLAC",
    ),

    # --- المستندات والأرشيف ---
    FileSignature(
        ext="pdf",
        category="مستندات",
        header=b"%PDF-",
        footer=b"%%EOF",
        max_size=50 * 1024 * 1024,
        description="مستند PDF",
    ),
    FileSignature(
        ext="zip",
        category="مستندات",
        header=b"PK\x03\x04",
        footer=b"PK\x05\x06",
        max_size=50 * 1024 * 1024,
        description="أرشيف ZIP / مستند Office (DOCX/XLSX)",
    ),

    # --- قواعد البيانات ---
    FileSignature(
        ext="db",
        category="قواعد بيانات",
        header=b"SQLite format 3\x00",
        footer=None,
        max_size=100 * 1024 * 1024,
        description="قاعدة بيانات SQLite",
    ),
]


# ترجمة أنواع رسائل واتساب الشائعة في النسخ الحديثة والقديمة
WHATSAPP_MESSAGE_TYPES: Dict[int, str] = {
    0: "نص",
    1: "صورة",
    2: "رسالة صوتية",
    3: "فيديو",
    4: "جهة اتصال",
    5: "موقع جغرافي",
    7: "رسالة نظام",
    9: "مستند",
    10: "مكالمة فائتة",
    13: "صورة متحركة GIF",
    14: "جهات اتصال متعددة",
    15: "رسالة محذوفة",
    16: "موقع مباشر",
    20: "ملصق (Sticker)",
    36: "رسالة سريعة / زائلة",
    42: "صورة للعرض مرة واحدة",
    43: "فيديو للعرض مرة واحدة",
    66: "استطلاع رأي (Poll)",
    82: "رسالة صوتية للعرض مرة واحدة",
}

# ترجمة حالات الرسائل في قاعدة بيانات واتساب
WHATSAPP_STATUS_MAP: Dict[int, Tuple[str, str]] = {
    0: ("⏳", "في الانتظار"),
    4: ("✓", "تم الإرسال للخادم"),
    5: ("✓✓", "تم التسليم"),
    13: ("✓✓", "تمت القراءة"),
    6: ("ℹ️", "رسالة نظام"),
    7: ("⚠️", "فشل الإرسال"),
}

# مجلدات الوسائط القياسية في واتساب أندرويد
WHATSAPP_MEDIA_SUBDIRS: List[str] = [
    "WhatsApp Images",
    "WhatsApp Video",
    "WhatsApp Audio",
    "WhatsApp Voice Notes",
    "WhatsApp Documents",
    "WhatsApp Animated Gifs",
    "WhatsApp Stickers",
]
