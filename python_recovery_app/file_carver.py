# -*- coding: utf-8 -*-
"""
محرك استرجاع الملفات العامة بطريقة File Carving.
يعمل بوضع القراءة فقط (Read-Only) على الأقراص أو كروت الذاكرة أو صور الأقراص أو المجلدات،
ويقوم بالبحث عن بصمات الملفات وحساب الحجم المتوقع وتقييم احتمالية الاسترجاع.
"""

import io
import os
import platform
import struct
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

from config import FILE_SIGNATURES, FileSignature

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class CarvedFile:
    """يمثل ملفًا تم العثور عليه أثناء الفحص."""
    id: int
    name: str
    ext: str
    category: str
    offset: int
    size: int
    probability: str        # عالية / متوسطة / منخفضة
    probability_pct: int    # نسبة مئوية تقديرية 0-100
    source_path: str
    is_direct_file: bool = False  # True عند فحص مجلد MTP/ملفات عادية بدل قرص خام
    notes: str = ""


def list_available_drives() -> List[Tuple[str, str]]:
    """
    إرجاع قائمة بالأقراص والأقسام المتاحة على النظام للعرض في الواجهة.
    يعيد قائمة من (المسار، الوصف).
    """
    drives: List[Tuple[str, str]] = []
    sys_name = platform.system()

    if sys_name == "Windows":
        import string
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for idx, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << idx):
                raw_vol = f"\\\\.\\{letter}:"
                mount = f"{letter}:\\"
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(mount)
                type_label = {
                    2: "قرص قابل للإزالة (USB/SD)",
                    3: "قرص ثابت",
                    4: "قرص شبكة",
                    5: "قرص ضوئي",
                }.get(drive_type, "وحدة تخزين")
                drives.append((raw_vol, f"{letter}: ({type_label})"))
        # إضافة أول 4 أقراص فيزيائية كخيارات متقدمة (تتطلب صلاحيات المسؤول)
        for i in range(4):
            drives.append((f"\\\\.\\PhysicalDrive{i}", f"القرص الفيزيائي {i} (يتطلب مسؤول)"))
    else:
        # لينكس / ماك: عرض نقاط التحميل الشائعة وأجهزة التخزين القابلة للقراءة
        for base_dir in ("/media", "/mnt", "/Volumes"):
            if os.path.isdir(base_dir):
                for entry in sorted(os.listdir(base_dir)):
                    full = os.path.join(base_dir, entry)
                    if os.path.isdir(full):
                        drives.append((full, f"مجلد متصل: {entry}"))
        if os.path.isdir("/dev"):
            for dev in sorted(os.listdir("/dev")):
                if dev.startswith(("sd", "nvme", "mmcblk", "disk")) and len(dev) <= 10:
                    full_dev = os.path.join("/dev", dev)
                    drives.append((full_dev, f"جهاز تخزين خام: {full_dev}"))

    return drives


def is_same_storage_root(source_path: str, dest_dir: str) -> bool:
    """
    التحقق مما إذا كان مجلد الحفظ المختار يقع على نفس القرص الذي يتم فحصه
    لتجنب الكتابة فوق القطاعات المحذوفة أثناء الاسترجاع.
    """
    dest_abs = os.path.abspath(dest_dir)
    src_norm = source_path.strip()

    if platform.system() == "Windows":
        # مثال: \\.\E: أو E:\
        src_drive = ""
        if src_norm.startswith("\\\\.\\") and len(src_norm) >= 6 and src_norm[5] == ":":
            src_drive = src_norm[4].upper()
        elif len(src_norm) >= 2 and src_norm[1] == ":":
            src_drive = src_norm[0].upper()

        dest_drive = dest_abs[0].upper() if len(dest_abs) >= 2 and dest_abs[1] == ":" else ""
        if src_drive and dest_drive:
            return src_drive == dest_drive
        return False

    # على لينكس/ماك: مقارنة رقم الجهاز (st_dev) إن أمكن
    try:
        if os.path.exists(src_norm):
            src_stat = os.stat(src_norm)
            dest_stat = os.stat(dest_abs)
            if os.path.isdir(src_norm) and src_stat.st_dev == dest_stat.st_dev:
                return True
    except OSError:
        pass
    return False


class FileCarver:
    """
    محرك فحص الأقراص والملفات الخام بطريقة القراءة فقط واسترجاع الملفات بالبصمات.
    """

    def __init__(self, chunk_size: int = 4 * 1024 * 1024):
        # حجم القراءة المتتابعة (4 ميجابايت افتراضيًا) مع تداخل لتفادي قص البصمات بين القطع
        self.chunk_size = chunk_size
        self.overlap = 64
        self._stop_requested = False

    def request_stop(self) -> None:
        """طلب إيقاف عملية الفحص بأمان."""
        self._stop_requested = True

    def scan(
        self,
        source_path: str,
        allowed_categories: Optional[Set[str]] = None,
        max_scan_bytes: Optional[int] = None,
        progress_callback: Optional[Callable[[int, Optional[int], str], None]] = None,
        found_callback: Optional[Callable[[CarvedFile], None]] = None,
    ) -> List[CarvedFile]:
        """
        فحص المصدر (قرص خام، صورة قرص .img/.dd، أو مجلد ملفات).
        يعمل بوضع القراءة فقط دائمًا.
        """
        self._stop_requested = False
        signatures = [
            sig for sig in FILE_SIGNATURES
            if not allowed_categories or sig.category in allowed_categories
        ]
        if not signatures:
            return []

        # إذا كان المصدر مجلدًا (مثلاً مجلد هاتف متصل أو مجلد نسخ محلي)
        if os.path.isdir(source_path):
            return self._scan_directory_readonly(
                source_path, signatures, progress_callback, found_callback
            )

        # إذا كان قرصًا خامًا أو صورة قرص
        return self._scan_binary_stream(
            source_path, signatures, max_scan_bytes, progress_callback, found_callback
        )

    def _scan_directory_readonly(
        self,
        root_dir: str,
        signatures: List[FileSignature],
        progress_callback: Optional[Callable[[int, Optional[int], str], None]],
        found_callback: Optional[Callable[[CarvedFile], None]],
    ) -> List[CarvedFile]:
        """فحص الملفات داخل مجلد بوضع القراءة فقط (بما في ذلك مجلدات المصغرات والملفات المخفية)."""
        results: List[CarvedFile] = []
        allowed_exts = {s.ext for s in signatures}
        ext_to_sig = {s.ext: s for s in signatures}
        scanned_files = 0

        for dirpath, _, filenames in os.walk(root_dir):
            if self._stop_requested:
                break
            for fname in filenames:
                if self._stop_requested:
                    break
                full_path = os.path.join(dirpath, fname)
                scanned_files += 1
                try:
                    size = os.path.getsize(full_path)
                    if size <= 0:
                        continue
                    with open(full_path, "rb") as f:
                        head = f.read(32)
                    matched_sig = self._match_header_in_bytes(head, signatures)
                    ext = os.path.splitext(fname)[1].lstrip(".").lower()
                    if matched_sig is None and ext in allowed_exts:
                        matched_sig = ext_to_sig.get(ext)
                    if matched_sig is None:
                        continue

                    prob_label, prob_pct, notes = self._evaluate_file_integrity(
                        full_path, 0, min(size, matched_sig.max_size), matched_sig
                    )
                    item = CarvedFile(
                        id=len(results) + 1,
                        name=fname,
                        ext=matched_sig.ext,
                        category=matched_sig.category,
                        offset=0,
                        size=size,
                        probability=prob_label,
                        probability_pct=prob_pct,
                        source_path=full_path,
                        is_direct_file=True,
                        notes=notes or "ملف موجود في المسار",
                    )
                    results.append(item)
                    if found_callback:
                        found_callback(item)
                except OSError:
                    continue

                if progress_callback and scanned_files % 50 == 0:
                    progress_callback(scanned_files, None, f"تم فحص {scanned_files} ملف...")

        if progress_callback:
            progress_callback(scanned_files, scanned_files, "اكتمل فحص المجلد.")
        return results

    def _scan_binary_stream(
        self,
        source_path: str,
        signatures: List[FileSignature],
        max_scan_bytes: Optional[int],
        progress_callback: Optional[Callable[[int, Optional[int], str], None]],
        found_callback: Optional[Callable[[CarvedFile], None]],
    ) -> List[CarvedFile]:
        """فحص القرص الخام أو صورة القرص قطاعًا تلو الآخر بحثًا عن بصمات الملفات."""
        results: List[CarvedFile] = []
        seen_offsets: Set[int] = set()
        total_size: Optional[int] = None

        try:
            if os.path.isfile(source_path):
                total_size = os.path.getsize(source_path)
        except OSError:
            total_size = None

        if max_scan_bytes and (total_size is None or max_scan_bytes < total_size):
            total_size = max_scan_bytes

        # فتح المصدر بوضع القراءة الثنائية فقط ('rb') بدون أي كتابة
        with open(source_path, "rb") as stream:
            current_offset = 0
            while not self._stop_requested:
                if max_scan_bytes and current_offset >= max_scan_bytes:
                    break

                stream.seek(current_offset)
                read_len = self.chunk_size + self.overlap
                if max_scan_bytes:
                    read_len = min(read_len, max_scan_bytes - current_offset)
                buf = stream.read(read_len)
                if not buf:
                    break

                scan_limit = min(len(buf), self.chunk_size)
                # البحث عن كل بصمة داخل المقطع الحالي
                for sig in signatures:
                    if self._stop_requested:
                        break
                    start_pos = 0
                    while start_pos < scan_limit:
                        idx = buf.find(sig.header, start_pos, scan_limit + len(sig.header))
                        if idx == -1 or idx >= scan_limit:
                            break

                        actual_start = current_offset + idx - sig.header_offset
                        start_pos = idx + 1
                        if actual_start < 0 or actual_start in seen_offsets:
                            continue

                        # التحقق الإضافي لبصمات RIFF (WebP / AVI / WAVE)
                        if sig.header == b"RIFF":
                            if idx + 12 > len(buf):
                                continue
                            riff_type = buf[idx + 8: idx + 12]
                            expected = {
                                "webp": b"WEBP",
                                "avi": b"AVI ",
                                "wav": b"WAVE",
                            }.get(sig.ext)
                            if riff_type != expected:
                                continue

                        # التحقق الإضافي لـ MP4/MOV/M4A (وجود ذرة ftyp بحجم منطقي)
                        if sig.ext == "mp4":
                            if idx < 4 or idx + 8 > len(buf):
                                continue
                            atom_size = struct.unpack(">I", buf[idx - 4: idx])[0]
                            if atom_size < 16 or atom_size > 1024:
                                continue

                        seen_offsets.add(actual_start)
                        estimated_size, has_clean_end, extra_note = self._determine_file_bounds(
                            source_path, actual_start, sig
                        )
                        if estimated_size <= 16:
                            continue

                        prob_label, prob_pct, verify_note = self._score_probability(
                            source_path, actual_start, estimated_size, sig, has_clean_end
                        )
                        file_name = f"recovered_{len(results) + 1:05d}_0x{actual_start:X}.{sig.ext}"
                        item = CarvedFile(
                            id=len(results) + 1,
                            name=file_name,
                            ext=sig.ext,
                            category=sig.category,
                            offset=actual_start,
                            size=estimated_size,
                            probability=prob_label,
                            probability_pct=prob_pct,
                            source_path=source_path,
                            is_direct_file=False,
                            notes=verify_note or extra_note,
                        )
                        results.append(item)
                        if found_callback:
                            found_callback(item)

                current_offset += len(buf) if len(buf) < self.chunk_size else self.chunk_size
                if progress_callback:
                    mb_done = current_offset / (1024 * 1024)
                    progress_callback(
                        current_offset,
                        total_size,
                        f"تم فحص {mb_done:.1f} ميجابايت | تم العثور على {len(results)} ملف",
                    )

        if progress_callback:
            progress_callback(
                total_size or current_offset,
                total_size or current_offset,
                f"انتهى الفحص: {len(results)} ملف.",
            )
        return results

    def _match_header_in_bytes(
        self, head: bytes, signatures: List[FileSignature]
    ) -> Optional[FileSignature]:
        """مطابقة ترويسة ملف صغير مع البصمات المعرفة."""
        for sig in signatures:
            off = sig.header_offset
            if len(head) >= off + len(sig.header):
                if head[off: off + len(sig.header)] == sig.header:
                    if sig.header == b"RIFF" and len(head) >= 12:
                        riff_type = head[8:12]
                        expected = {"webp": b"WEBP", "avi": b"AVI ", "wav": b"WAVE"}.get(sig.ext)
                        if riff_type != expected:
                            continue
                    return sig
        return None

    def _determine_file_bounds(
        self, source_path: str, offset: int, sig: FileSignature
    ) -> Tuple[int, bool, str]:
        """
        حساب الحجم الدقيق أو التقديري للملف المستخرج باستخدام البنية الداخلية
        أو علامة النهاية (Footer).
        """
        try:
            with open(source_path, "rb") as f:
                f.seek(offset)
                # 1) ملفات RIFF (WebP, WAV, AVI): الحجم محفوظ في البايتات 4..8 بترتيب Little-Endian
                if sig.header == b"RIFF":
                    hdr = f.read(12)
                    if len(hdr) == 12:
                        riff_len = struct.unpack("<I", hdr[4:8])[0] + 8
                        if 32 <= riff_len <= sig.max_size:
                            return riff_len, True, "تم حساب الحجم من ترويسة RIFF"

                # 2) ملفات BMP: الحجم الكلي محفوظ عند الإزاحة 2..6
                if sig.ext == "bmp":
                    hdr = f.read(14)
                    if len(hdr) == 14:
                        bmp_size = struct.unpack("<I", hdr[2:6])[0]
                        if 64 <= bmp_size <= sig.max_size:
                            return bmp_size, True, "تم حساب الحجم من ترويسة BMP"

                # 3) ملفات MP4/MOV/M4A: تتبع سلسلة الذرات (Boxes/Atoms)
                if sig.ext == "mp4":
                    pos = 0
                    valid_atoms = 0
                    while pos < sig.max_size:
                        f.seek(offset + pos)
                        atom_hdr = f.read(8)
                        if len(atom_hdr) < 8:
                            break
                        atom_size, atom_type = struct.unpack(">I4s", atom_hdr)
                        if atom_size == 1:
                            ext_hdr = f.read(8)
                            if len(ext_hdr) < 8:
                                break
                            atom_size = struct.unpack(">Q", ext_hdr)[0]
                        if atom_size < 8 or atom_size > sig.max_size:
                            break
                        # التحقق أن نوع الذرة حروف قابلة للطباعة
                        if not all(32 <= b <= 126 for b in atom_type):
                            break
                        valid_atoms += 1
                        pos += atom_size
                        if atom_type in (b"mdat", b"moov") and valid_atoms >= 2:
                            # نستمر لالتقاط بقية الذرات إن وجدت
                            continue
                    if pos > 64 and valid_atoms >= 2:
                        return min(pos, sig.max_size), True, f"بنية MP4 متماسكة ({valid_atoms} ذرات)"

                # 4) قواعد بيانات SQLite: عدد الصفحات * حجم الصفحة
                if sig.ext == "db":
                    hdr = f.read(100)
                    if len(hdr) == 100 and hdr[:16] == b"SQLite format 3\x00":
                        page_size = struct.unpack(">H", hdr[16:18])[0]
                        if page_size == 1:
                            page_size = 65536
                        page_count = struct.unpack(">I", hdr[28:32])[0]
                        db_size = page_size * page_count
                        if 1024 <= db_size <= sig.max_size:
                            return db_size, True, f"SQLite ({page_count} صفحة)"

                # 5) ملفات ذات علامة نهاية معروفة (JPEG, PNG, GIF, PDF, ZIP)
                if sig.footer:
                    read_chunk = 512 * 1024
                    scanned = 0
                    tail_overlap = len(sig.footer)
                    prev_tail = b""
                    while scanned < sig.max_size:
                        to_read = min(read_chunk, sig.max_size - scanned)
                        data = f.read(to_read)
                        if not data:
                            break
                        combined = prev_tail + data
                        end_idx = combined.find(sig.footer)
                        if end_idx != -1:
                            total = scanned - len(prev_tail) + end_idx + len(sig.footer)
                            # في ZIP توجد 18 بايت بعد EOCD على الأقل + طول التعليق
                            if sig.ext == "zip" and len(combined) >= end_idx + 22:
                                comment_len = struct.unpack("<H", combined[end_idx + 20: end_idx + 22])[0]
                                total += 18 + comment_len
                            return total, True, "تم العثور على علامة نهاية سليمة"
                        scanned += len(data)
                        prev_tail = combined[-tail_overlap:]
                    return min(scanned, 2 * 1024 * 1024), False, "لم يُعثر على علامة النهاية (قد يكون مبتورًا)"

                # 6) صيغ بدون علامة نهاية صريحة (OGG, MP3, FLAC, MKV)
                # نقدر الحجم بالبحث عن بداية بصمة ملف آخر أو سقف معقول
                sample = f.read(min(sig.max_size, 5 * 1024 * 1024))
                return len(sample), False, "حجم تقديري (صيغة بدون علامة نهاية ثابتة)"
        except OSError:
            return 0, False, "تعذر قراءة القطاع"

    def _score_probability(
        self,
        source_path: str,
        offset: int,
        size: int,
        sig: FileSignature,
        has_clean_end: bool,
    ) -> Tuple[str, int, str]:
        """تقييم احتمالية نجاح الاسترجاع (عالية / متوسطة / منخفضة)."""
        score = 45
        notes = []

        if has_clean_end:
            score += 35
            notes.append("البنية/النهاية مكتملة")
        else:
            notes.append("نهاية تقديرية")

        if 512 <= size <= sig.max_size:
            score += 10

        # التحقق الفعلي من الصور عبر Pillow عند توفرها
        if HAS_PIL and sig.category == "صور" and size <= 15 * 1024 * 1024:
            try:
                raw = self.read_carved_bytes(source_path, offset, size)
                with Image.open(io.BytesIO(raw)) as img:
                    img.verify()
                score = max(score, 95)
                notes.append("تم التحقق من سلامة الصورة بنجاح")
            except Exception:
                score = min(score, 55)
                notes.append("تلف جزئي في بيانات الصورة")

        score = max(10, min(99, score))
        if score >= 80:
            return "عالية", score, " - ".join(notes)
        if score >= 55:
            return "متوسطة", score, " - ".join(notes)
        return "منخفضة", score, " - ".join(notes)

    def _evaluate_file_integrity(
        self, file_path: str, offset: int, size: int, sig: FileSignature
    ) -> Tuple[str, int, str]:
        """تقييم ملف مباشر داخل مجلد."""
        return self._score_probability(file_path, offset, size, sig, True)

    def read_carved_bytes(
        self, source_path: str, offset: int, size: int, max_bytes: Optional[int] = None
    ) -> bytes:
        """قراءة بايتات ملف مستخرج للمعاينة أو الحفظ بوضع القراءة فقط."""
        to_read = min(size, max_bytes) if max_bytes else size
        with open(source_path, "rb") as f:
            f.seek(offset)
            return f.read(to_read)

    def recover_file(self, carved: CarvedFile, dest_dir: str) -> str:
        """
        حفظ الملف المستخرج في مجلد آمن مع التأكد من عدم الكتابة على نفس القرص المصدر.
        """
        if is_same_storage_root(carved.source_path, dest_dir):
            raise ValueError(
                "تحذير أمان: لا يجوز حفظ الملفات المسترجعة على نفس القرص الجاري فحصه! اختر قرصًا آخر."
            )

        os.makedirs(dest_dir, exist_ok=True)
        out_path = os.path.join(dest_dir, carved.name)
        base, ext = os.path.splitext(out_path)
        counter = 1
        while os.path.exists(out_path):
            out_path = f"{base}_{counter}{ext}"
            counter += 1

        remaining = carved.size
        buf_size = 1024 * 1024
        with open(carved.source_path, "rb") as src, open(out_path, "wb") as dst:
            src.seek(carved.offset)
            while remaining > 0:
                chunk = src.read(min(buf_size, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)

        return out_path
