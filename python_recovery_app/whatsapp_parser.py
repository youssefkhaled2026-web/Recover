# -*- coding: utf-8 -*-
"""
محلل قواعد بيانات واتساب (msgstore.db + wa.db اختياري).
متوافق مع:
1. النسخ الحديثة لواتساب (جداول message, chat, jid, message_media, message_quoted, message_revoked).
2. النسخ الأقدم (جدول messages و chat_list).
يعمل بوضع القراءة فقط دائمًا (SQLite URI mode=ro).
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from urllib.request import pathname2url

from config import WHATSAPP_MEDIA_SUBDIRS, WHATSAPP_MESSAGE_TYPES, WHATSAPP_STATUS_MAP


@dataclass
class ChatInfo:
    """معلومات محادثة فردية أو جماعية."""
    chat_id: str
    row_id: int
    display_name: str
    phone_or_jid: str
    is_group: bool
    message_count: int
    last_timestamp_ms: int


@dataclass
class ChatMessage:
    """رسالة مستخرجة مع كامل بياناتها وارتباطها بالوسائط والردود."""
    row_id: int
    chat_id: str
    sender_name: str
    sender_phone: str
    from_me: bool
    timestamp_ms: int
    timestamp_str: str
    date_str: str
    time_str: str
    text: str
    message_type: int
    type_label: str
    status_code: int
    status_icon: str
    status_label: str
    is_deleted: bool
    is_edited: bool = False
    quoted_sender: str = ""
    quoted_text: str = ""
    media_rel_path: str = ""
    media_full_path: Optional[str] = None
    media_mime: str = ""
    media_caption: str = ""


def _open_sqlite_readonly(db_path: str) -> sqlite3.Connection:
    """فتح قاعدة بيانات SQLite بوضع القراءة فقط دون تعديل الملف الأصلي."""
    abs_path = os.path.abspath(db_path)
    uri = f"file:{pathname2url(abs_path)}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        # في حال وجود ملف -wal أو قفل، نستخدم immutable=1 كبديل آمن للقراءة فقط
        uri_imm = f"file:{pathname2url(abs_path)}?mode=ro&immutable=1"
        return sqlite3.connect(uri_imm, uri=True)


def _format_ts(ts_ms: Optional[int]) -> Tuple[str, str, str]:
    """تحويل الطابع الزمني (بالميلي ثانية) إلى نص مقروء وتاريخ ووقت."""
    if not ts_ms or ts_ms <= 0:
        return "غير معروف", "", ""
    # أحيانًا تكون القيمة بالثواني في جداول قديمة
    if ts_ms < 10_000_000_000:
        ts_ms *= 1000
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0)
        return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except (ValueError, OSError, OverflowError):
        return "تاريخ غير صالح", "", ""


def _extract_phone_from_jid(jid_str: str) -> str:
    """استخراج رقم الهاتف أو المعرف النظيف من JID."""
    if not jid_str:
        return "غير معروف"
    user_part = jid_str.split("@")[0]
    if user_part.isdigit() and len(user_part) >= 7:
        return f"+{user_part}"
    return user_part or jid_str


class WhatsAppParser:
    """محلل ذكي لبنية قاعدة بيانات واتساب الحديثة والقديمة."""

    def __init__(
        self,
        msgstore_db_path: str,
        wa_db_path: Optional[str] = None,
        media_root_dir: Optional[str] = None,
    ):
        self.msgstore_db_path = msgstore_db_path
        self.wa_db_path = wa_db_path
        self.media_root_dir = media_root_dir
        self.contacts_map: Dict[str, str] = {}
        self.media_index: Dict[str, str] = {}

        if wa_db_path and os.path.isfile(wa_db_path):
            self._load_wa_contacts(wa_db_path)
        if media_root_dir and os.path.isdir(media_root_dir):
            self._build_media_index(media_root_dir)

    def _load_wa_contacts(self, wa_db_path: str) -> None:
        """قراءة أسماء جهات الاتصال من wa.db إن وُجد."""
        try:
            conn = _open_sqlite_readonly(wa_db_path)
            cur = conn.cursor()
            tables = {
                r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "wa_contacts" in tables:
                cols = {r[1] for r in cur.execute("PRAGMA table_info(wa_contacts)").fetchall()}
                name_expr = "COALESCE(display_name, wa_name, number, jid)"
                if "display_name" in cols and "wa_name" in cols:
                    name_expr = "COALESCE(NULLIF(display_name,''), NULLIF(wa_name,''), NULLIF(number,''), jid)"
                elif "display_name" in cols:
                    name_expr = "COALESCE(NULLIF(display_name,''), jid)"

                for jid, name in cur.execute(f"SELECT jid, {name_expr} FROM wa_contacts").fetchall():
                    if jid and name:
                        self.contacts_map[jid] = str(name)
            conn.close()
        except Exception:
            pass

    def _build_media_index(self, media_root: str) -> None:
        """فهرسة مجلد WhatsApp/Media لربط أسماء الملفات ومساراتها النسبية بسرعة."""
        search_roots = [media_root]
        for sub in WHATSAPP_MEDIA_SUBDIRS:
            sub_path = os.path.join(media_root, sub)
            if os.path.isdir(sub_path):
                search_roots.append(sub_path)

        visited: Set[str] = set()
        for root in search_roots:
            if root in visited:
                continue
            visited.add(root)
            for dirpath, _, filenames in os.walk(root):
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    rel = os.path.relpath(full, media_root).replace("\\", "/")
                    self.media_index[rel.lower()] = full
                    self.media_index[fname.lower()] = full

    def resolve_media_path(self, rel_path: Optional[str]) -> Optional[str]:
        """حل المسار الكامل لملف الوسائط انطلاقًا من المسار المحفوظ في قاعدة البيانات."""
        if not rel_path or not self.media_root_dir:
            return None
        clean = rel_path.replace("\\", "/").lstrip("/")
        # تجربة المسار المباشر
        candidate = os.path.join(self.media_root_dir, clean)
        if os.path.isfile(candidate):
            return candidate
        # إذا كان المسار يبدأ بـ Media/ بينما المستخدم اختار مجلد Media نفسه
        if clean.lower().startswith("media/"):
            trimmed = clean[6:]
            candidate2 = os.path.join(self.media_root_dir, trimmed)
            if os.path.isfile(candidate2):
                return candidate2
            if trimmed.lower() in self.media_index:
                return self.media_index[trimmed.lower()]
        # البحث بالاسم فقط في الفهرس
        base_name = os.path.basename(clean).lower()
        return self.media_index.get(clean.lower()) or self.media_index.get(base_name)

    def _resolve_display_name(self, jid_str: str, fallback_subject: str = "") -> str:
        """تحديد الاسم المعروض لجهة الاتصال أو المجموعة."""
        if fallback_subject:
            return fallback_subject
        if jid_str in self.contacts_map:
            return self.contacts_map[jid_str]
        return _extract_phone_from_jid(jid_str)

    def detect_schema_version(self) -> str:
        """تحديد هل القاعدة بالبنية الحديثة (message+chat+jid) أم القديمة (messages)."""
        conn = _open_sqlite_readonly(self.msgstore_db_path)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "message" in tables and "chat" in tables and "jid" in tables:
                return "modern"
            if "messages" in tables:
                return "legacy"
            return "unknown"
        finally:
            conn.close()

    def load_chats(self) -> List[ChatInfo]:
        """استخراج قائمة المحادثات والمجموعات مرتبة حسب آخر نشاط."""
        schema = self.detect_schema_version()
        conn = _open_sqlite_readonly(self.msgstore_db_path)
        try:
            if schema == "modern":
                return self._load_chats_modern(conn)
            if schema == "legacy":
                return self._load_chats_legacy(conn)
            raise ValueError("بنية قاعدة البيانات غير مدعومة أو لا تخص تطبيق واتساب.")
        finally:
            conn.close()

    def _load_chats_modern(self, conn: sqlite3.Connection) -> List[ChatInfo]:
        cur = conn.cursor()
        chat_cols = {r[1] for r in cur.execute("PRAGMA table_info(chat)").fetchall()}
        sort_col = "sort_timestamp" if "sort_timestamp" in chat_cols else "0"
        subj_col = "subject" if "subject" in chat_cols else "''"

        query = f"""
            SELECT
                c._id,
                COALESCE(j.raw_string, COALESCE(j.user,'') || '@' || COALESCE(j.server,'')),
                COALESCE(c.{subj_col}, ''),
                COALESCE(c.{sort_col}, 0),
                (SELECT COUNT(*) FROM message m WHERE m.chat_row_id = c._id) AS msg_cnt
            FROM chat c
            LEFT JOIN jid j ON c.jid_row_id = j._id
            WHERE msg_cnt > 0
            ORDER BY COALESCE(c.{sort_col}, 0) DESC
        """
        chats: List[ChatInfo] = []
        for row_id, raw_jid, subject, sort_ts, msg_cnt in cur.execute(query).fetchall():
            jid_str = raw_jid or f"chat_{row_id}"
            is_group = "@g.us" in jid_str or bool(subject)
            name = self._resolve_display_name(jid_str, subject)
            chats.append(
                ChatInfo(
                    chat_id=jid_str,
                    row_id=row_id,
                    display_name=name,
                    phone_or_jid=_extract_phone_from_jid(jid_str),
                    is_group=is_group,
                    message_count=msg_cnt,
                    last_timestamp_ms=sort_ts or 0,
                )
            )
        return chats

    def _load_chats_legacy(self, conn: sqlite3.Connection) -> List[ChatInfo]:
        cur = conn.cursor()
        tables = {
            r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        subj_lookup: Dict[str, str] = {}
        if "chat_list" in tables:
            cols = {r[1] for r in cur.execute("PRAGMA table_info(chat_list)").fetchall()}
            if "key_remote_jid" in cols and "subject" in cols:
                for jid, subj in cur.execute("SELECT key_remote_jid, subject FROM chat_list").fetchall():
                    if jid and subj:
                        subj_lookup[jid] = subj

        query = """
            SELECT
                key_remote_jid,
                COUNT(*) AS cnt,
                MAX(timestamp) AS max_ts
            FROM messages
            WHERE key_remote_jid IS NOT NULL AND key_remote_jid != ''
            GROUP BY key_remote_jid
            ORDER BY max_ts DESC
        """
        chats: List[ChatInfo] = []
        for idx, (jid_str, cnt, max_ts) in enumerate(cur.execute(query).fetchall(), start=1):
            subject = subj_lookup.get(jid_str, "")
            is_group = "@g.us" in jid_str or bool(subject)
            chats.append(
                ChatInfo(
                    chat_id=jid_str,
                    row_id=idx,
                    display_name=self._resolve_display_name(jid_str, subject),
                    phone_or_jid=_extract_phone_from_jid(jid_str),
                    is_group=is_group,
                    message_count=cnt,
                    last_timestamp_ms=max_ts or 0,
                )
            )
        return chats

    def load_messages(
        self,
        chat: ChatInfo,
        search_text: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> List[ChatMessage]:
        """استخراج رسائل محادثة محددة مع دعم البحث والفلترة بالتاريخ."""
        schema = self.detect_schema_version()
        conn = _open_sqlite_readonly(self.msgstore_db_path)
        try:
            if schema == "modern":
                messages = self._load_messages_modern(conn, chat)
            elif schema == "legacy":
                messages = self._load_messages_legacy(conn, chat)
            else:
                messages = []
        finally:
            conn.close()

        # تطبيق الفلاتر
        q = search_text.strip().lower()
        d_from = date_from.strip()
        d_to = date_to.strip()

        filtered: List[ChatMessage] = []
        for msg in messages:
            if q:
                haystack = f"{msg.text} {msg.sender_name} {msg.quoted_text} {msg.media_caption}".lower()
                if q not in haystack:
                    continue
            if d_from and msg.date_str and msg.date_str < d_from:
                continue
            if d_to and msg.date_str and msg.date_str > d_to:
                continue
            filtered.append(msg)

        return filtered

    def _load_messages_modern(self, conn: sqlite3.Connection, chat: ChatInfo) -> List[ChatMessage]:
        cur = conn.cursor()
        tables = {
            r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        msg_cols = {r[1] for r in cur.execute("PRAGMA table_info(message)").fetchall()}

        # قراءة تفاصيل الوسائط إن وجد جدول message_media
        media_map: Dict[int, Tuple[str, str, str]] = {}
        if "message_media" in tables:
            mm_cols = {r[1] for r in cur.execute("PRAGMA table_info(message_media)").fetchall()}
            path_col = "file_path" if "file_path" in mm_cols else "''"
            mime_col = "mime_type" if "mime_type" in mm_cols else "''"
            cap_col = "media_caption" if "media_caption" in mm_cols else ("media_name" if "media_name" in mm_cols else "''")
            for m_id, fpath, mime, cap in cur.execute(
                f"""
                SELECT mm.message_row_id, COALESCE(mm.{path_col},''), COALESCE(mm.{mime_col},''), COALESCE(mm.{cap_col},'')
                FROM message_media mm
                INNER JOIN message m ON m._id = mm.message_row_id
                WHERE m.chat_row_id = ?
                """,
                (chat.row_id,),
            ).fetchall():
                media_map[m_id] = (fpath, mime, cap)

        # قراءة الردود المقتبسة إن وجد جدول message_quoted
        quoted_map: Dict[int, Tuple[str, str]] = {}
        if "message_quoted" in tables:
            mq_cols = {r[1] for r in cur.execute("PRAGMA table_info(message_quoted)").fetchall()}
            q_text_col = "text_data" if "text_data" in mq_cols else "''"
            q_sender_join = ""
            q_sender_expr = "''"
            if "sender_jid_row_id" in mq_cols:
                q_sender_join = "LEFT JOIN jid qj ON mq.sender_jid_row_id = qj._id"
                q_sender_expr = "COALESCE(qj.raw_string, COALESCE(qj.user,'') || '@' || COALESCE(qj.server,''))"
            for m_id, q_from_me, q_sender_jid, q_text in cur.execute(
                f"""
                SELECT mq.message_row_id, COALESCE(mq.from_me, 0), {q_sender_expr}, COALESCE(mq.{q_text_col}, '')
                FROM message_quoted mq
                INNER JOIN message m ON m._id = mq.message_row_id
                {q_sender_join}
                WHERE m.chat_row_id = ?
                """,
                (chat.row_id,),
            ).fetchall():
                q_sender = "أنت" if q_from_me else self._resolve_display_name(q_sender_jid or chat.chat_id)
                quoted_map[m_id] = (q_sender, q_text or "[وسائط مقتبسة]")

        # الرسائل المحذوفة والمعدلة
        revoked_ids: Set[int] = set()
        if "message_revoked" in tables:
            for (r_id,) in cur.execute(
                """
                SELECT mr.message_row_id
                FROM message_revoked mr
                INNER JOIN message m ON m._id = mr.message_row_id
                WHERE m.chat_row_id = ?
                """,
                (chat.row_id,),
            ).fetchall():
                revoked_ids.add(r_id)

        edited_ids: Set[int] = set()
        if "message_edit_info" in tables:
            for (e_id,) in cur.execute(
                """
                SELECT mei.message_row_id
                FROM message_edit_info mei
                INNER JOIN message m ON m._id = mei.message_row_id
                WHERE m.chat_row_id = ?
                """,
                (chat.row_id,),
            ).fetchall():
                edited_ids.add(e_id)

        text_col = "text_data" if "text_data" in msg_cols else ("data" if "data" in msg_cols else "''")
        sender_col = "sender_jid_row_id" if "sender_jid_row_id" in msg_cols else "NULL"
        status_col = "status" if "status" in msg_cols else "0"
        type_col = "message_type" if "message_type" in msg_cols else "0"

        rows = cur.execute(
            f"""
            SELECT
                m._id,
                COALESCE(m.from_me, 0),
                COALESCE(m.timestamp, 0),
                COALESCE(m.{text_col}, ''),
                COALESCE(m.{type_col}, 0),
                COALESCE(m.{status_col}, 0),
                COALESCE(sj.raw_string, COALESCE(sj.user,'') || '@' || COALESCE(sj.server,''))
            FROM message m
            LEFT JOIN jid sj ON m.{sender_col} = sj._id
            WHERE m.chat_row_id = ?
            ORDER BY m.timestamp ASC, m._id ASC
            """,
            (chat.row_id,),
        ).fetchall()

        result: List[ChatMessage] = []
        for row_id, from_me, ts_ms, text_data, msg_type, status, sender_jid in rows:
            ts_str, d_str, t_str = _format_ts(ts_ms)
            is_from_me = bool(from_me)
            if is_from_me:
                sender_name = "أنت"
                sender_phone = ""
            else:
                eff_jid = sender_jid if (sender_jid and "@" in sender_jid) else chat.chat_id
                sender_name = self._resolve_display_name(eff_jid)
                sender_phone = _extract_phone_from_jid(eff_jid)

            is_deleted = (msg_type == 15) or (row_id in revoked_ids)
            type_label = WHATSAPP_MESSAGE_TYPES.get(msg_type, f"نوع ({msg_type})")
            status_icon, status_label = WHATSAPP_STATUS_MAP.get(status, ("", "غير محدد"))

            media_rel, media_mime, media_cap = media_map.get(row_id, ("", "", ""))
            media_full = self.resolve_media_path(media_rel) if media_rel else None
            q_sender, q_text = quoted_map.get(row_id, ("", ""))

            display_text = text_data
            if is_deleted and not display_text:
                display_text = "🚫 هذه الرسالة تم حذفها"
            elif not display_text and media_cap:
                display_text = media_cap
            elif not display_text and msg_type != 0:
                display_text = f"[{type_label}]"

            result.append(
                ChatMessage(
                    row_id=row_id,
                    chat_id=chat.chat_id,
                    sender_name=sender_name,
                    sender_phone=sender_phone,
                    from_me=is_from_me,
                    timestamp_ms=ts_ms,
                    timestamp_str=ts_str,
                    date_str=d_str,
                    time_str=t_str,
                    text=display_text,
                    message_type=msg_type,
                    type_label=type_label,
                    status_code=status,
                    status_icon=status_icon,
                    status_label=status_label,
                    is_deleted=is_deleted,
                    is_edited=(row_id in edited_ids),
                    quoted_sender=q_sender,
                    quoted_text=q_text,
                    media_rel_path=media_rel,
                    media_full_path=media_full,
                    media_mime=media_mime,
                    media_caption=media_cap,
                )
            )
        return result

    def _load_messages_legacy(self, conn: sqlite3.Connection, chat: ChatInfo) -> List[ChatMessage]:
        cur = conn.cursor()
        cols = {r[1] for r in cur.execute("PRAGMA table_info(messages)").fetchall()}
        has_quoted = "quoted_row_id" in cols
        has_media_name = "media_name" in cols
        has_media_mime = "media_mime_type" in cols
        has_media_caption = "media_caption" in cols

        rows = cur.execute(
            f"""
            SELECT
                _id,
                COALESCE(key_from_me, 0),
                COALESCE(timestamp, 0),
                COALESCE(data, ''),
                COALESCE(media_wa_type, 0),
                COALESCE(status, 0),
                COALESCE(remote_resource, ''),
                {'COALESCE(media_name, "")' if has_media_name else "''"},
                {'COALESCE(media_mime_type, "")' if has_media_mime else "''"},
                {'COALESCE(media_caption, "")' if has_media_caption else "''"},
                {'COALESCE(quoted_row_id, 0)' if has_quoted else "0"}
            FROM messages
            WHERE key_remote_jid = ?
            ORDER BY timestamp ASC, _id ASC
            """,
            (chat.chat_id,),
        ).fetchall()

        id_lookup: Dict[int, Tuple[str, str]] = {}
        for r in rows:
            s_name = "أنت" if r[1] else self._resolve_display_name(r[6] or chat.chat_id)
            id_lookup[r[0]] = (s_name, r[3] or "[وسائط]")

        result: List[ChatMessage] = []
        for row_id, from_me, ts_ms, text_data, msg_type, status, remote_res, m_name, m_mime, m_cap, q_id in rows:
            ts_str, d_str, t_str = _format_ts(ts_ms)
            is_from_me = bool(from_me)
            eff_jid = remote_res or chat.chat_id
            sender_name = "أنت" if is_from_me else self._resolve_display_name(eff_jid)
            sender_phone = "" if is_from_me else _extract_phone_from_jid(eff_jid)
            is_deleted = (msg_type == 15)
            type_label = WHATSAPP_MESSAGE_TYPES.get(msg_type, f"نوع ({msg_type})")
            status_icon, status_label = WHATSAPP_STATUS_MAP.get(status, ("", "غير محدد"))
            q_sender, q_text = id_lookup.get(q_id, ("", "")) if q_id else ("", "")
            media_full = self.resolve_media_path(m_name) if m_name else None

            display_text = text_data or m_cap or (
                "🚫 هذه الرسالة تم حذفها" if is_deleted else f"[{type_label}]"
            )
            result.append(
                ChatMessage(
                    row_id=row_id,
                    chat_id=chat.chat_id,
                    sender_name=sender_name,
                    sender_phone=sender_phone,
                    from_me=is_from_me,
                    timestamp_ms=ts_ms,
                    timestamp_str=ts_str,
                    date_str=d_str,
                    time_str=t_str,
                    text=display_text,
                    message_type=msg_type,
                    type_label=type_label,
                    status_code=status,
                    status_icon=status_icon,
                    status_label=status_label,
                    is_deleted=is_deleted,
                    quoted_sender=q_sender,
                    quoted_text=q_text,
                    media_rel_path=m_name,
                    media_full_path=media_full,
                    media_mime=m_mime,
                    media_caption=m_cap,
                )
            )
        return result
