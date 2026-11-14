# -*- coding: utf-8 -*-
"""
وحدة التحليل الجنائي العميق لملفات SQLite لاسترجاع بقايا السجلات المحذوفة.
تعمل بوضع القراءة فقط على:
1. صفحات القائمة الحرة (Freelist Trunk & Leaf Pages) داخل ملف .db
2. المساحة غير المخصصة والكتل الحرة (Unallocated Space & Freeblocks) داخل صفحات B-Tree Leaf (0x0D)
3. ملفات سجل الكتابة المسبقة (SQLite WAL: -wal) لاستخراج السجلات غير المدمجة أو القديمة
"""

import os
import re
import struct
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple


# تعبير منتظم لاستخراج النصوص المقروءة (العربية والإنجليزية والأرقام والرموز الشائعة)
READABLE_UTF8_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077FA-Za-z0-9@#\+:/\.\-_,\?!\s]{5,}"
)
JID_OR_PHONE_RE = re.compile(r"(\d{8,15}@(?:s\.whatsapp\.net|g\.us|lid)|\+\d{8,15})")


@dataclass
class ForensicArtifact:
    """بقايا سجل محذوف مستخرج من المستويات الثنائية لـ SQLite."""
    source: str       # Freelist Page / Unallocated B-Tree / WAL Frame
    page_or_frame: int
    offset: int
    extracted_text: str
    related_jid: str = ""
    confidence: str = "متوسطة"


def _extract_strings_from_bytes(payload: bytes, min_len: int = 5) -> List[str]:
    """استخراج الجمل النصية النظيفة (UTF-8) من بايتات ثنائية."""
    text = payload.decode("utf-8", errors="ignore")
    results: List[str] = []
    for match in READABLE_UTF8_RE.finditer(text):
        cleaned = " ".join(match.group(0).split())
        # تجاهل السلاسل المكررة من نفس الحرف أو الضوضاء القصيرة جدًا
        if len(cleaned) >= min_len and len(set(cleaned)) > 2:
            # تجاهل أسماء الجداول التقنية فقط إذا لم يكن معها نص مفيد
            if cleaned.lower().startswith(("create table", "create index", "sqlite_")):
                continue
            results.append(cleaned)
    return results


def _find_related_jid(text: str) -> str:
    """البحث عن معرف واتساب أو رقم هاتف داخل النص المستخرج."""
    m = JID_OR_PHONE_RE.search(text)
    return m.group(1) if m else ""


class SqliteForensics:
    """محرك استخراج السجلات المحذوفة من صفحات SQLite الحرة وملفات WAL."""

    def scan_database_deep(
        self, db_path: str, wal_path: Optional[str] = None, max_artifacts: int = 2000
    ) -> List[ForensicArtifact]:
        """
        فحص ملف قاعدة البيانات الرئيسي (+ ملف WAL إن وُجد أو تم تمريره).
        """
        artifacts: List[ForensicArtifact] = []
        seen_texts: Set[str] = set()

        if os.path.isfile(db_path):
            db_artifacts = self._scan_main_db(db_path, seen_texts, max_artifacts)
            artifacts.extend(db_artifacts)

        # البحث التلقائي عن ملف -wal بجوار قاعدة البيانات إن لم يُمرر يدويًا
        effective_wal = wal_path
        if not effective_wal and db_path:
            auto_wal = f"{db_path}-wal"
            if os.path.isfile(auto_wal):
                effective_wal = auto_wal

        if effective_wal and os.path.isfile(effective_wal) and len(artifacts) < max_artifacts:
            wal_artifacts = self._scan_wal_file(
                effective_wal, seen_texts, max_artifacts - len(artifacts)
            )
            artifacts.extend(wal_artifacts)

        return artifacts

    def _scan_main_db(
        self, db_path: str, seen_texts: Set[str], max_artifacts: int
    ) -> List[ForensicArtifact]:
        """فحص Freelist Pages والمساحات غير المخصصة داخل صفحات B-Tree Leaf."""
        artifacts: List[ForensicArtifact] = []
        file_size = os.path.getsize(db_path)
        if file_size < 100:
            return artifacts

        with open(db_path, "rb") as f:
            hdr = f.read(100)
            if hdr[:16] != b"SQLite format 3\x00":
                return artifacts

            page_size = struct.unpack(">H", hdr[16:18])[0]
            if page_size == 1:
                page_size = 65536
            if page_size < 512:
                return artifacts

            first_freelist_trunk = struct.unpack(">I", hdr[32:36])[0]
            total_freelist_pages = struct.unpack(">I", hdr[36:40])[0]

            # 1) تتبع صفحات القائمة الحرة (Freelist Trunk & Leaf Pages)
            freelist_pages: Set[int] = set()
            trunk = first_freelist_trunk
            visited_trunks: Set[int] = set()
            while trunk > 0 and trunk not in visited_trunks and len(freelist_pages) < total_freelist_pages + 100:
                visited_trunks.add(trunk)
                freelist_pages.add(trunk)
                page_offset = (trunk - 1) * page_size
                if page_offset + page_size > file_size:
                    break
                f.seek(page_offset)
                trunk_data = f.read(page_size)
                if len(trunk_data) < 8:
                    break
                next_trunk, leaf_count = struct.unpack(">II", trunk_data[:8])
                max_entries = min(leaf_count, (page_size - 8) // 4)
                for i in range(max_entries):
                    leaf_pg = struct.unpack(">I", trunk_data[8 + i * 4: 12 + i * 4])[0]
                    if leaf_pg > 0:
                        freelist_pages.add(leaf_pg)
                trunk = next_trunk

            for pg_num in sorted(freelist_pages):
                if len(artifacts) >= max_artifacts:
                    break
                off = (pg_num - 1) * page_size
                if off + page_size > file_size:
                    continue
                f.seek(off)
                pg_data = f.read(page_size)
                for text in _extract_strings_from_bytes(pg_data):
                    if text not in seen_texts:
                        seen_texts.add(text)
                        artifacts.append(
                            ForensicArtifact(
                                source="صفحة محذوفة (Freelist)",
                                page_or_frame=pg_num,
                                offset=off,
                                extracted_text=text,
                                related_jid=_find_related_jid(text),
                                confidence="عالية" if len(text) >= 12 else "متوسطة",
                            )
                        )
                        if len(artifacts) >= max_artifacts:
                            break

            # 2) فحص المساحات غير المخصصة والـ Freeblocks داخل صفحات B-Tree Table Leaf (0x0D)
            total_pages = file_size // page_size
            for pg_num in range(2, total_pages + 1):
                if len(artifacts) >= max_artifacts:
                    break
                if pg_num in freelist_pages:
                    continue
                off = (pg_num - 1) * page_size
                f.seek(off)
                pg_data = f.read(page_size)
                if len(pg_data) < 8:
                    continue
                # نوع الصفحة 0x0D = Table Leaf B-Tree Page
                if pg_data[0] != 0x0D:
                    continue

                first_freeblock = struct.unpack(">H", pg_data[1:3])[0]
                cell_count = struct.unpack(">H", pg_data[3:5])[0]
                cell_content_start = struct.unpack(">H", pg_data[5:7])[0]
                if cell_content_start == 0:
                    cell_content_start = 65536

                # المنطقة غير المخصصة تقع بين نهاية مصفوفة المؤشرات وبداية محتوى الخلايا
                ptr_array_end = 8 + cell_count * 2
                if ptr_array_end < cell_content_start <= page_size:
                    unalloc_slice = pg_data[ptr_array_end:cell_content_start]
                    for text in _extract_strings_from_bytes(unalloc_slice):
                        if text not in seen_texts:
                            seen_texts.add(text)
                            artifacts.append(
                                ForensicArtifact(
                                    source="مساحة غير مخصصة (B-Tree Unallocated)",
                                    page_or_frame=pg_num,
                                    offset=off + ptr_array_end,
                                    extracted_text=text,
                                    related_jid=_find_related_jid(text),
                                    confidence="عالية" if len(text) >= 12 else "متوسطة",
                                )
                            )
                            if len(artifacts) >= max_artifacts:
                                break

                # تتبع سلسلة الكتل الحرة (Freeblocks) داخل الصفحة
                fb = first_freeblock
                visited_fb: Set[int] = set()
                while 8 <= fb < page_size - 4 and fb not in visited_fb and len(artifacts) < max_artifacts:
                    visited_fb.add(fb)
                    next_fb, fb_size = struct.unpack(">HH", pg_data[fb: fb + 4])
                    if fb_size <= 4 or fb + fb_size > page_size:
                        break
                    fb_payload = pg_data[fb + 4: fb + fb_size]
                    for text in _extract_strings_from_bytes(fb_payload):
                        if text not in seen_texts:
                            seen_texts.add(text)
                            artifacts.append(
                                ForensicArtifact(
                                    source="كتلة حرة داخل صفحة (Freeblock)",
                                    page_or_frame=pg_num,
                                    offset=off + fb,
                                    extracted_text=text,
                                    related_jid=_find_related_jid(text),
                                    confidence="عالية",
                                )
                            )
                            if len(artifacts) >= max_artifacts:
                                break
                    fb = next_fb

        return artifacts

    def _scan_wal_file(
        self, wal_path: str, seen_texts: Set[str], max_artifacts: int
    ) -> List[ForensicArtifact]:
        """
        تحليل ملف SQLite WAL (-wal) واستخراج النصوص من إطارات الصفحات (Frames).
        """
        artifacts: List[ForensicArtifact] = []
        wal_size = os.path.getsize(wal_path)
        if wal_size < 32:
            return artifacts

        with open(wal_path, "rb") as f:
            wal_hdr = f.read(32)
            magic = struct.unpack(">I", wal_hdr[:4])[0]
            if magic not in (0x377F0682, 0x377F0683):
                return artifacts

            page_size = struct.unpack(">I", wal_hdr[8:12])[0]
            if page_size < 512 or page_size > 65536:
                return artifacts

            frame_idx = 0
            offset = 32
            frame_total = 24 + page_size
            while offset + frame_total <= wal_size and len(artifacts) < max_artifacts:
                f.seek(offset)
                frame_hdr = f.read(24)
                pg_num = struct.unpack(">I", frame_hdr[:4])[0]
                page_data = f.read(page_size)
                frame_idx += 1

                # استخراج النصوص خصوصًا من صفحات B-Tree Leaf (0x0D) أو أي صفحة تحتوي نصوصًا
                source_tag = (
                    f"إطار WAL (صفحة B-Tree #{pg_num})"
                    if page_data and page_data[0] == 0x0D
                    else f"إطار WAL (صفحة #{pg_num})"
                )
                for text in _extract_strings_from_bytes(page_data):
                    if text not in seen_texts:
                        seen_texts.add(text)
                        artifacts.append(
                            ForensicArtifact(
                                source=source_tag,
                                page_or_frame=frame_idx,
                                offset=offset + 24,
                                extracted_text=text,
                                related_jid=_find_related_jid(text),
                                confidence="عالية" if page_data[0] == 0x0D else "متوسطة",
                            )
                        )
                        if len(artifacts) >= max_artifacts:
                            break
                offset += frame_total

        return artifacts
