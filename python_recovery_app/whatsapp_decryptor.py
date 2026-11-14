# -*- coding: utf-8 -*-
"""
وحدة فك تشفير قواعد بيانات واتساب المشفرة (crypt14 و crypt15).
تدعم:
- فك تشفير msgstore.db.crypt14 باستخدام ملف المفتاح (key).
- فك تشفير msgstore.db.crypt15 باستخدام مفتاح التشفير الطرفي المكون من 64 رقمًا سادس عشريًا
  أو ملف encrypted_backup.key.
- فك تشفير ترويسة Protobuf الحديثة واستخراج الـ IV تلقائيًا دون الحاجة لأدوات خارجية.
"""

import hashlib
import hmac
import os
import tempfile
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


SQLITE_MAGIC = b"SQLite format 3\x00"


@dataclass
class DecryptionResult:
    """نتيجة عملية فك تشفير قاعدة البيانات."""
    success: bool
    output_db_path: Optional[str]
    format_detected: str
    message: str


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """قراءة عدد صحيح متغير الطول (Protobuf Varint)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 64:
            raise ValueError("Varint غير صالح في ترويسة الملف.")
    raise ValueError("انتهت البيانات أثناء قراءة ترويسة Protobuf.")


def _parse_protobuf_fields(data: bytes) -> Dict[int, List[Tuple[int, bytes | int]]]:
    """
    محلل بسيط ومستقل لبنية Protobuf Wire Format لاستخراج حقول الترويسة (IV وغيرها).
    يعيد قاموسًا: {field_number: [(wire_type, value), ...]}.
    """
    fields: Dict[int, List[Tuple[int, bytes | int]]] = {}
    pos = 0
    while pos < len(data):
        tag, pos = _read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # Varint
            val, pos = _read_varint(data, pos)
            fields.setdefault(field_num, []).append((wire_type, val))
        elif wire_type == 2:  # Length-delimited (bytes / string / nested message)
            length, pos = _read_varint(data, pos)
            if pos + length > len(data):
                raise ValueError("تجاوز طول الحقل حدود الترويسة.")
            val_bytes = data[pos: pos + length]
            pos += length
            fields.setdefault(field_num, []).append((wire_type, val_bytes))
        elif wire_type == 5:  # 32-bit
            if pos + 4 > len(data):
                raise ValueError("تجاوز 32-bit حدود الترويسة.")
            fields.setdefault(field_num, []).append((wire_type, data[pos: pos + 4]))
            pos += 4
        elif wire_type == 1:  # 64-bit
            if pos + 8 > len(data):
                raise ValueError("تجاوز 64-bit حدود الترويسة.")
            fields.setdefault(field_num, []).append((wire_type, data[pos: pos + 8]))
            pos += 8
        else:
            raise ValueError(f"نوع سلك Protobuf غير مدعوم: {wire_type}")
    return fields


def _hkdf_sha256_32(ikm: bytes, info: bytes, salt: bytes = b"\x00" * 32) -> bytes:
    """
    اشتقاق مفتاح AES-256 (32 بايت) باستخدام HKDF-SHA256 المطابق لتطبيق واتساب.
    """
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
    return okm[:32]


def normalize_64_hex_key(hex_key: str) -> bytes:
    """
    تنظيف والتحقق من مفتاح الـ 64 رقمًا سادس عشريًا الخاص بالنسخ المشفرة طرفيًا (crypt15).
    """
    cleaned = "".join(ch for ch in hex_key if ch in "0123456789abcdefABCDEF")
    if len(cleaned) != 64:
        raise ValueError(
            f"يجب أن يتكون المفتاح من 64 خانة سادس عشرية (0-9, a-f). الطول الحالي: {len(cleaned)}."
        )
    return bytes.fromhex(cleaned)


def extract_key_from_file(key_file_path: str, is_crypt15: bool = False) -> bytes:
    """
    استخراج مفتاح AES (32 بايت) من ملف 'key' الخاص بواتساب أو 'encrypted_backup.key'.
    - ملف key القياسي لواتساب يكون عادة 158 بايت (مفتاح التشفير في آخر 32 بايت: [126:158]).
    - ملف encrypted_backup.key لـ crypt15 قد يحتوي على المفتاح الجذري مباشرة (32 بايت) أو مغلفًا بـ Protobuf.
    """
    with open(key_file_path, "rb") as f:
        raw = f.read()

    if is_crypt15:
        if len(raw) == 32:
            return _hkdf_sha256_32(raw, b"backup encryption")
        # محاولة استخراج حقل 32 بايت من ملف Protobuf (encrypted_backup.key)
        try:
            fields = _parse_protobuf_fields(raw)
            for _, items in fields.items():
                for wtype, val in items:
                    if wtype == 2 and isinstance(val, bytes) and len(val) == 32:
                        return _hkdf_sha256_32(val, b"backup encryption")
        except Exception:
            pass
        if len(raw) >= 32:
            return _hkdf_sha256_32(raw[-32:], b"backup encryption")
        raise ValueError("صيغة ملف مفتاح crypt15 غير صالحة.")

    # لنسخ crypt14: ملف key القياسي بطول 158 بايت (أو 32 بايت مفتاح خام)
    if len(raw) == 158:
        return raw[126:158]
    if len(raw) == 131:
        return raw[99:131]
    if len(raw) == 32:
        return raw
    if len(raw) > 32:
        return raw[-32:]
    raise ValueError(f"حجم ملف المفتاح غير متوقع ({len(raw)} بايت).")


class WhatsAppDecryptor:
    """فئة فك تشفير نسخ واتساب الاحتياطية الحديثة (crypt14 / crypt15)."""

    def _parse_header_and_payload(self, blob: bytes) -> Tuple[str, bytes, bytes]:
        """
        تحليل ترويسة ملف crypt14/crypt15 واستخراج:
        (نوع التشفير، المتجه الابتدائي IV بطول 16 بايت، البيانات المشفرة).
        """
        if len(blob) < 64:
            raise ValueError("الملف صغير جدًا ولا يمثل نسخة واتساب صالحة.")

        # أول بايت (أو varint) يحدد طول ترويسة Protobuf
        header_len, pos = _read_varint(blob, 0)
        if header_len <= 0 or header_len > 2048:
            raise ValueError(f"طول ترويسة غير منطقي: {header_len}")

        # في بعض نسخ crypt14/crypt15 يوجد بايت إضافي (0x00 أو 0x01) يمثل نوع المفتاح/الميزة
        # قبل رسالة Protobuf مباشرةً إذا لم يكن البايت التالي علامة حقل Protobuf صالحة.
        possible_flags = (0x00, 0x01)
        header_bytes: bytes
        payload_start: int

        def _try_parse_iv(candidate_hdr: bytes) -> Optional[Tuple[str, bytes]]:
            try:
                fields = _parse_protobuf_fields(candidate_hdr)
            except Exception:
                return None

            # crypt15: الحقل رقم 3 يحتوي على submessage بداخله الحقل رقم 1 = IV (16 بايت)
            if 3 in fields:
                for wtype, val in fields[3]:
                    if wtype == 2 and isinstance(val, bytes):
                        sub = _parse_protobuf_fields(val)
                        if 1 in sub:
                            for swt, sval in sub[1]:
                                if swt == 2 and isinstance(sval, bytes) and len(sval) == 16:
                                    return "crypt15", sval

            # crypt14: الحقل رقم 2 يحتوي على submessage بداخله الحقل رقم 5 = IV (16 بايت)
            if 2 in fields:
                for wtype, val in fields[2]:
                    if wtype == 2 and isinstance(val, bytes):
                        sub = _parse_protobuf_fields(val)
                        for sub_idx in (5, 4, 3, 1):
                            if sub_idx in sub:
                                for swt, sval in sub[sub_idx]:
                                    if swt == 2 and isinstance(sval, bytes) and len(sval) == 16:
                                        return "crypt14", sval
            return None

        # المحاولة الأولى: وجود بايت علم (flag byte) بعد الطول
        if pos < len(blob) and blob[pos] in possible_flags and pos + 1 + header_len <= len(blob):
            candidate = blob[pos + 1: pos + 1 + header_len]
            parsed = _try_parse_iv(candidate)
            if parsed:
                fmt, iv = parsed
                return fmt, iv, blob[pos + 1 + header_len:]

        # المحاولة الثانية: الترويسة تبدأ مباشرة بعد الطول
        if pos + header_len <= len(blob):
            candidate = blob[pos: pos + header_len]
            parsed = _try_parse_iv(candidate)
            if parsed:
                fmt, iv = parsed
                return fmt, iv, blob[pos + header_len:]

        raise ValueError(
            "تعذر قراءة ترويسة Protobuf أو استخراج الـ IV من الملف. "
            "تأكد من أن الملف هو msgstore.db.crypt14 أو msgstore.db.crypt15 سليم."
        )

    def decrypt_backup(
        self,
        backup_path: str,
        key_file_path: Optional[str] = None,
        hex_key_64: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> DecryptionResult:
        """
        فك تشفير النسخة الاحتياطية بوضع القراءة فقط وإخراج ملف SQLite صالح.
        """
        if not os.path.isfile(backup_path):
            return DecryptionResult(False, None, "غير معروف", "ملف النسخة الاحتياطية غير موجود.")

        # إذا كان الملف غير مشفر بالفعل (msgstore.db)
        with open(backup_path, "rb") as f:
            head = f.read(16)
        if head == SQLITE_MAGIC:
            return DecryptionResult(
                True,
                backup_path,
                "SQLite (غير مشفر)",
                "الملف عبارة عن قاعدة بيانات SQLite جاهزة للقراءة مباشرة.",
            )

        with open(backup_path, "rb") as f:
            blob = f.read()

        try:
            detected_fmt, iv, ciphertext = self._parse_header_and_payload(blob)
        except Exception as exc:
            return DecryptionResult(False, None, "غير معروف", str(exc))

        # تحديد مفتاح التشفير المستخدم
        aes_keys_to_try: List[Tuple[str, bytes]] = []
        try:
            if hex_key_64 and hex_key_64.strip():
                root_key = normalize_64_hex_key(hex_key_64.strip())
                derived = _hkdf_sha256_32(root_key, b"backup encryption")
                aes_keys_to_try.append(("مفتاح 64 خانة (crypt15)", derived))
            if key_file_path and os.path.isfile(key_file_path):
                if detected_fmt == "crypt15":
                    aes_keys_to_try.append(
                        ("ملف مفتاح crypt15", extract_key_from_file(key_file_path, is_crypt15=True))
                    )
                    # تجربة استخراج مباشر كخيار احتياطي
                    aes_keys_to_try.append(
                        ("ملف مفتاح (مباشر)", extract_key_from_file(key_file_path, is_crypt15=False))
                    )
                else:
                    aes_keys_to_try.append(
                        ("ملف مفتاح crypt14", extract_key_from_file(key_file_path, is_crypt15=False))
                    )
        except Exception as exc:
            return DecryptionResult(False, None, detected_fmt, f"خطأ في قراءة المفتاح: {exc}")

        if not aes_keys_to_try:
            return DecryptionResult(
                False,
                None,
                detected_fmt,
                "يرجى تحديد ملف المفتاح (key) أو إدخال مفتاح الـ 64 رقمًا أولاً.",
            )

        # تجربة فك التشفير (مع معالجة احتمال وجود تذييل checksum بطول 32 بايت في بعض نسخ crypt14)
        payload_candidates = [ciphertext]
        if len(ciphertext) > 48:
            payload_candidates.append(ciphertext[:-32])

        decrypted_raw: Optional[bytes] = None
        used_label = ""

        for key_label, aes_key in aes_keys_to_try:
            aesgcm = AESGCM(aes_key)
            for candidate_ct in payload_candidates:
                try:
                    decrypted_raw = aesgcm.decrypt(iv, candidate_ct, None)
                    used_label = key_label
                    break
                except Exception:
                    continue
            if decrypted_raw is not None:
                break

        if decrypted_raw is None:
            return DecryptionResult(
                False,
                None,
                detected_fmt,
                "فشل فك التشفير (Authentication Tag mismatch). "
                "تحقق من صحة ملف المفتاح أو مفتاح الـ 64 رقمًا وأنه يخص نفس الحساب والهاتف.",
            )

        # فك ضغط zlib إذا كانت البيانات مضغوطة (وهو الوضع القياسي في واتساب)
        db_bytes = decrypted_raw
        if not db_bytes.startswith(SQLITE_MAGIC):
            try:
                db_bytes = zlib.decompress(decrypted_raw)
            except zlib.error:
                # تجربة إزاحة أو wbits أخرى في حال وجود ترويسة zlib خاصة
                try:
                    db_bytes = zlib.decompress(decrypted_raw, 15 + 32)
                except zlib.error as zerr:
                    return DecryptionResult(
                        False,
                        None,
                        detected_fmt,
                        f"نجح فك التشفير لكن تعذر فك ضغط البيانات (zlib): {zerr}",
                    )

        if not db_bytes.startswith(SQLITE_MAGIC):
            return DecryptionResult(
                False,
                None,
                detected_fmt,
                "تم فك التشفير لكن الناتج لا يحمل ترويسة SQLite format 3 الصالحة.",
            )

        if not output_path:
            fd, output_path = tempfile.mkstemp(prefix="wa_decrypted_", suffix=".db")
            os.close(fd)

        with open(output_path, "wb") as out_f:
            out_f.write(db_bytes)

        return DecryptionResult(
            True,
            output_path,
            detected_fmt,
            f"تم فك التشفير بنجاح ({detected_fmt}) باستخدام {used_label}.",
        )
