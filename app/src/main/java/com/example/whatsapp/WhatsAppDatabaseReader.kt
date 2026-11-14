package com.example.whatsapp

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.net.Uri
import android.provider.DocumentsContract
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class WaChat(
    val chatId: String,
    val rowId: Long,
    val displayName: String,
    val phoneOrJid: String,
    val isGroup: Boolean,
    val messageCount: Int,
    val lastTimestampMs: Long,
)

data class WaMessage(
    val rowId: Long,
    val chatId: String,
    val senderName: String,
    val senderPhone: String,
    val fromMe: Boolean,
    val timestampMs: Long,
    val timestampStr: String,
    val dateStr: String,
    val timeStr: String,
    val text: String,
    val messageType: Int,
    val typeLabel: String,
    val statusCode: Int,
    val statusIcon: String,
    val statusLabel: String,
    val isDeleted: Boolean,
    val isEdited: Boolean = false,
    val quotedSender: String = "",
    val quotedText: String = "",
    val mediaRelPath: String = "",
    val mediaUri: Uri? = null,
)

object WhatsAppDatabaseReader {

    private val MESSAGE_TYPES = mapOf(
        0 to "نص",
        1 to "صورة",
        2 to "رسالة صوتية",
        3 to "فيديو",
        4 to "جهة اتصال",
        5 to "موقع جغرافي",
        7 to "رسالة نظام",
        9 to "مستند",
        10 to "مكالمة فائتة",
        13 to "GIF",
        15 to "رسالة محذوفة",
        20 to "ملصق",
        42 to "صورة للعرض مرة واحدة",
        66 to "استطلاع رأي",
    )

    private val STATUS_MAP = mapOf(
        0 to ("⏳" to "في الانتظار"),
        4 to ("✓" to "تم الإرسال"),
        5 to ("✓✓" to "تم التسليم"),
        13 to ("✓✓" to "تمت القراءة"),
        6 to ("ℹ️" to "نظام"),
        7 to ("⚠️" to "فشل"),
    )

    fun loadContactsFromWaDb(context: Context, waDbUri: Uri?): Map<String, String> {
        if (waDbUri == null) return emptyMap()
        val tempWa = File(context.cacheDir, "wa_contacts_ro.db")
        context.contentResolver.openInputStream(waDbUri)?.use { input ->
            FileOutputStream(tempWa).use { out -> input.copyTo(out) }
        } ?: return emptyMap()

        val map = mutableMapOf<String, String>()
        var db: SQLiteDatabase? = null
        try {
            db = SQLiteDatabase.openDatabase(tempWa.absolutePath, null, SQLiteDatabase.OPEN_READONLY)
            val tables = getTableNames(db)
            if ("wa_contacts" in tables) {
                val cols = getColumnNames(db, "wa_contacts")
                val expr = when {
                    "display_name" in cols && "wa_name" in cols ->
                        "COALESCE(NULLIF(display_name,''), NULLIF(wa_name,''), NULLIF(number,''), jid)"
                    "display_name" in cols -> "COALESCE(NULLIF(display_name,''), jid)"
                    else -> "jid"
                }
                db.rawQuery("SELECT jid, $expr FROM wa_contacts", null).use { c ->
                    while (c.moveToNext()) {
                        val jid = c.getString(0)
                        val name = c.getString(1)
                        if (!jid.isNullOrBlank() && !name.isNullOrBlank()) {
                            map[jid] = name
                        }
                    }
                }
            }
        } catch (_: Exception) {
        } finally {
            db?.close()
        }
        return map
    }

    fun indexMediaTree(context: Context, mediaTreeUri: Uri?): Map<String, Uri> {
        if (mediaTreeUri == null) return emptyMap()
        val index = mutableMapOf<String, Uri>()
        val rootDocId = DocumentsContract.getTreeDocumentId(mediaTreeUri)
        val queue = ArrayDeque<String>()
        queue.add(rootDocId)
        var guard = 0
        while (queue.isNotEmpty() && guard < 300) {
            guard++
            val parentId = queue.removeFirst()
            val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(mediaTreeUri, parentId)
            try {
                context.contentResolver.query(
                    childrenUri,
                    arrayOf(
                        DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                        DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                        DocumentsContract.Document.COLUMN_MIME_TYPE,
                    ),
                    null,
                    null,
                    null,
                )?.use { c ->
                    while (c.moveToNext()) {
                        val docId = c.getString(0)
                        val name = c.getString(1) ?: ""
                        val mime = c.getString(2) ?: ""
                        if (mime == DocumentsContract.Document.MIME_TYPE_DIR) {
                            queue.add(docId)
                        } else if (name.isNotBlank()) {
                            val docUri = DocumentsContract.buildDocumentUriUsingTree(mediaTreeUri, docId)
                            index[name.lowercase(Locale.US)] = docUri
                        }
                    }
                }
            } catch (_: Exception) {
            }
        }
        return index
    }

    fun loadChats(
        dbFile: File,
        contacts: Map<String, String>,
    ): List<WaChat> {
        val db = SQLiteDatabase.openDatabase(dbFile.absolutePath, null, SQLiteDatabase.OPEN_READONLY)
        try {
            val tables = getTableNames(db)
            return when {
                "message" in tables && "chat" in tables && "jid" in tables ->
                    loadChatsModern(db, contacts)
                "messages" in tables ->
                    loadChatsLegacy(db, contacts, tables)
                else -> throw IllegalArgumentException("بنية قاعدة البيانات لا تطابق جداول واتساب المعروفة.")
            }
        } finally {
            db.close()
        }
    }

    private fun loadChatsModern(db: SQLiteDatabase, contacts: Map<String, String>): List<WaChat> {
        val chatCols = getColumnNames(db, "chat")
        val sortCol = if ("sort_timestamp" in chatCols) "sort_timestamp" else "0"
        val subjCol = if ("subject" in chatCols) "subject" else "''"
        val sql = """
            SELECT
                c._id,
                COALESCE(j.raw_string, COALESCE(j.user,'') || '@' || COALESCE(j.server,'')),
                COALESCE(c.$subjCol, ''),
                COALESCE(c.$sortCol, 0),
                (SELECT COUNT(*) FROM message m WHERE m.chat_row_id = c._id) AS cnt
            FROM chat c
            LEFT JOIN jid j ON c.jid_row_id = j._id
            WHERE cnt > 0
            ORDER BY COALESCE(c.$sortCol, 0) DESC
        """.trimIndent()

        val chats = mutableListOf<WaChat>()
        db.rawQuery(sql, null).use { c ->
            while (c.moveToNext()) {
                val rowId = c.getLong(0)
                val rawJid = c.getString(1) ?: "chat_$rowId"
                val subject = c.getString(2) ?: ""
                val sortTs = c.getLong(3)
                val count = c.getInt(4)
                val isGroup = rawJid.contains("@g.us") || subject.isNotBlank()
                val name = resolveDisplayName(rawJid, subject, contacts)
                chats.add(
                    WaChat(
                        chatId = rawJid,
                        rowId = rowId,
                        displayName = name,
                        phoneOrJid = extractPhone(rawJid),
                        isGroup = isGroup,
                        messageCount = count,
                        lastTimestampMs = sortTs,
                    )
                )
            }
        }
        return chats
    }

    private fun loadChatsLegacy(
        db: SQLiteDatabase,
        contacts: Map<String, String>,
        tables: Set<String>,
    ): List<WaChat> {
        val subjMap = mutableMapOf<String, String>()
        if ("chat_list" in tables) {
            val cols = getColumnNames(db, "chat_list")
            if ("key_remote_jid" in cols && "subject" in cols) {
                db.rawQuery("SELECT key_remote_jid, subject FROM chat_list", null).use { c ->
                    while (c.moveToNext()) {
                        val j = c.getString(0)
                        val s = c.getString(1)
                        if (!j.isNullOrBlank() && !s.isNullOrBlank()) subjMap[j] = s
                    }
                }
            }
        }
        val chats = mutableListOf<WaChat>()
        val sql = """
            SELECT key_remote_jid, COUNT(*), MAX(timestamp)
            FROM messages
            WHERE key_remote_jid IS NOT NULL AND key_remote_jid != ''
            GROUP BY key_remote_jid
            ORDER BY MAX(timestamp) DESC
        """.trimIndent()
        var idx = 1L
        db.rawQuery(sql, null).use { c ->
            while (c.moveToNext()) {
                val jid = c.getString(0)
                val cnt = c.getInt(1)
                val ts = c.getLong(2)
                val subj = subjMap[jid] ?: ""
                chats.add(
                    WaChat(
                        chatId = jid,
                        rowId = idx++,
                        displayName = resolveDisplayName(jid, subj, contacts),
                        phoneOrJid = extractPhone(jid),
                        isGroup = jid.contains("@g.us") || subj.isNotBlank(),
                        messageCount = cnt,
                        lastTimestampMs = ts,
                    )
                )
            }
        }
        return chats
    }

    fun loadMessages(
        dbFile: File,
        chat: WaChat,
        contacts: Map<String, String>,
        mediaIndex: Map<String, Uri>,
    ): List<WaMessage> {
        val db = SQLiteDatabase.openDatabase(dbFile.absolutePath, null, SQLiteDatabase.OPEN_READONLY)
        try {
            val tables = getTableNames(db)
            return when {
                "message" in tables && "chat" in tables && "jid" in tables ->
                    loadMessagesModern(db, chat, tables, contacts, mediaIndex)
                "messages" in tables ->
                    loadMessagesLegacy(db, chat, contacts, mediaIndex)
                else -> emptyList()
            }
        } finally {
            db.close()
        }
    }

    private fun loadMessagesModern(
        db: SQLiteDatabase,
        chat: WaChat,
        tables: Set<String>,
        contacts: Map<String, String>,
        mediaIndex: Map<String, Uri>,
    ): List<WaMessage> {
        val msgCols = getColumnNames(db, "message")
        val mediaMap = mutableMapOf<Long, Pair<String, String>>()
        if ("message_media" in tables) {
            val mmCols = getColumnNames(db, "message_media")
            val pCol = if ("file_path" in mmCols) "file_path" else "''"
            val cCol = if ("media_caption" in mmCols) "media_caption" else if ("media_name" in mmCols) "media_name" else "''"
            db.rawQuery(
                "SELECT mm.message_row_id, COALESCE(mm.$pCol,''), COALESCE(mm.$cCol,'') FROM message_media mm INNER JOIN message m ON m._id = mm.message_row_id WHERE m.chat_row_id = ?",
                arrayOf(chat.rowId.toString()),
            ).use { c ->
                while (c.moveToNext()) {
                    mediaMap[c.getLong(0)] = (c.getString(1) ?: "") to (c.getString(2) ?: "")
                }
            }
        }

        val quotedMap = mutableMapOf<Long, Pair<String, String>>()
        if ("message_quoted" in tables) {
            val mqCols = getColumnNames(db, "message_quoted")
            val qTextCol = if ("text_data" in mqCols) "text_data" else "''"
            val qSenderJoin = if ("sender_jid_row_id" in mqCols) "LEFT JOIN jid qj ON mq.sender_jid_row_id = qj._id" else ""
            val qSenderExpr = if ("sender_jid_row_id" in mqCols) "COALESCE(qj.raw_string, COALESCE(qj.user,'') || '@' || COALESCE(qj.server,''))" else "''"
            db.rawQuery(
                "SELECT mq.message_row_id, COALESCE(mq.from_me,0), $qSenderExpr, COALESCE(mq.$qTextCol,'') FROM message_quoted mq INNER JOIN message m ON m._id = mq.message_row_id $qSenderJoin WHERE m.chat_row_id = ?",
                arrayOf(chat.rowId.toString()),
            ).use { c ->
                while (c.moveToNext()) {
                    val qFromMe = c.getInt(1) != 0
                    val qJid = c.getString(2) ?: ""
                    val qTxt = c.getString(3) ?: ""
                    val qSender = if (qFromMe) "أنت" else resolveDisplayName(qJid.ifBlank { chat.chatId }, "", contacts)
                    quotedMap[c.getLong(0)] = qSender to (qTxt.ifBlank { "[وسائط مقتبسة]" })
                }
            }
        }

        val revokedIds = mutableSetOf<Long>()
        if ("message_revoked" in tables) {
            db.rawQuery(
                "SELECT mr.message_row_id FROM message_revoked mr INNER JOIN message m ON m._id = mr.message_row_id WHERE m.chat_row_id = ?",
                arrayOf(chat.rowId.toString()),
            ).use { c -> while (c.moveToNext()) revokedIds.add(c.getLong(0)) }
        }

        val editedIds = mutableSetOf<Long>()
        if ("message_edit_info" in tables) {
            db.rawQuery(
                "SELECT mei.message_row_id FROM message_edit_info mei INNER JOIN message m ON m._id = mei.message_row_id WHERE m.chat_row_id = ?",
                arrayOf(chat.rowId.toString()),
            ).use { c -> while (c.moveToNext()) editedIds.add(c.getLong(0)) }
        }

        val textCol = if ("text_data" in msgCols) "text_data" else if ("data" in msgCols) "data" else "''"
        val senderCol = if ("sender_jid_row_id" in msgCols) "sender_jid_row_id" else "NULL"
        val statusCol = if ("status" in msgCols) "status" else "0"
        val typeCol = if ("message_type" in msgCols) "message_type" else "0"

        val messages = mutableListOf<WaMessage>()
        val sql = """
            SELECT
                m._id,
                COALESCE(m.from_me, 0),
                COALESCE(m.timestamp, 0),
                COALESCE(m.$textCol, ''),
                COALESCE(m.$typeCol, 0),
                COALESCE(m.$statusCol, 0),
                COALESCE(sj.raw_string, COALESCE(sj.user,'') || '@' || COALESCE(sj.server,''))
            FROM message m
            LEFT JOIN jid sj ON m.$senderCol = sj._id
            WHERE m.chat_row_id = ?
            ORDER BY m.timestamp ASC, m._id ASC
        """.trimIndent()

        db.rawQuery(sql, arrayOf(chat.rowId.toString())).use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                val fromMe = c.getInt(1) != 0
                val tsMs = c.getLong(2)
                val rawText = c.getString(3) ?: ""
                val msgType = c.getInt(4)
                val status = c.getInt(5)
                val senderJid = c.getString(6) ?: ""

                val (tsStr, dStr, tStr) = formatTimestamp(tsMs)
                val effJid = if (senderJid.contains("@")) senderJid else chat.chatId
                val senderName = if (fromMe) "أنت" else resolveDisplayName(effJid, "", contacts)
                val senderPhone = if (fromMe) "" else extractPhone(effJid)
                val isDeleted = (msgType == 15) || (id in revokedIds)
                val typeLabel = MESSAGE_TYPES[msgType] ?: "نوع ($msgType)"
                val (stIcon, stLabel) = STATUS_MAP[status] ?: ("" to "غير محدد")
                val (mediaRel, mediaCap) = mediaMap[id] ?: ("" to "")
                val mediaUri = resolveMediaUri(mediaRel, mediaIndex)
                val (qSender, qText) = quotedMap[id] ?: ("" to "")

                val displayText = when {
                    isDeleted && rawText.isBlank() -> "🚫 تم حذف هذه الرسالة"
                    rawText.isNotBlank() -> rawText
                    mediaCap.isNotBlank() -> mediaCap
                    msgType != 0 -> "[$typeLabel]"
                    else -> ""
                }

                messages.add(
                    WaMessage(
                        rowId = id,
                        chatId = chat.chatId,
                        senderName = senderName,
                        senderPhone = senderPhone,
                        fromMe = fromMe,
                        timestampMs = tsMs,
                        timestampStr = tsStr,
                        dateStr = dStr,
                        timeStr = tStr,
                        text = displayText,
                        messageType = msgType,
                        typeLabel = typeLabel,
                        statusCode = status,
                        statusIcon = stIcon,
                        statusLabel = stLabel,
                        isDeleted = isDeleted,
                        isEdited = id in editedIds,
                        quotedSender = qSender,
                        quotedText = qText,
                        mediaRelPath = mediaRel,
                        mediaUri = mediaUri,
                    )
                )
            }
        }
        return messages
    }

    private fun loadMessagesLegacy(
        db: SQLiteDatabase,
        chat: WaChat,
        contacts: Map<String, String>,
        mediaIndex: Map<String, Uri>,
    ): List<WaMessage> {
        val cols = getColumnNames(db, "messages")
        val hasQuoted = "quoted_row_id" in cols
        val hasMediaName = "media_name" in cols
        val hasMediaCap = "media_caption" in cols

        val sql = """
            SELECT
                _id,
                COALESCE(key_from_me, 0),
                COALESCE(timestamp, 0),
                COALESCE(data, ''),
                COALESCE(media_wa_type, 0),
                COALESCE(status, 0),
                COALESCE(remote_resource, ''),
                ${if (hasMediaName) "COALESCE(media_name,'')" else "''"},
                ${if (hasMediaCap) "COALESCE(media_caption,'')" else "''"},
                ${if (hasQuoted) "COALESCE(quoted_row_id,0)" else "0"}
            FROM messages
            WHERE key_remote_jid = ?
            ORDER BY timestamp ASC, _id ASC
        """.trimIndent()

        val rows = mutableListOf<Array<Any>>()
        val lookup = mutableMapOf<Long, Pair<String, String>>()
        db.rawQuery(sql, arrayOf(chat.chatId)).use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                val fromMe = c.getInt(1) != 0
                val ts = c.getLong(2)
                val data = c.getString(3) ?: ""
                val wType = c.getInt(4)
                val status = c.getInt(5)
                val remoteRes = c.getString(6) ?: ""
                val mName = c.getString(7) ?: ""
                val mCap = c.getString(8) ?: ""
                val qId = c.getLong(9)
                val sName = if (fromMe) "أنت" else resolveDisplayName(remoteRes.ifBlank { chat.chatId }, "", contacts)
                lookup[id] = sName to (data.ifBlank { "[وسائط]" })
                rows.add(arrayOf(id, fromMe, ts, data, wType, status, remoteRes, mName, mCap, qId))
            }
        }

        return rows.map { r ->
            val id = r[0] as Long
            val fromMe = r[1] as Boolean
            val ts = r[2] as Long
            val data = r[3] as String
            val wType = r[4] as Int
            val status = r[5] as Int
            val remoteRes = r[6] as String
            val mName = r[7] as String
            val mCap = r[8] as String
            val qId = r[9] as Long
            val (tsStr, dStr, tStr) = formatTimestamp(ts)
            val effJid = remoteRes.ifBlank { chat.chatId }
            val senderName = if (fromMe) "أنت" else resolveDisplayName(effJid, "", contacts)
            val isDeleted = wType == 15
            val typeLabel = MESSAGE_TYPES[wType] ?: "نوع ($wType)"
            val (stIcon, stLabel) = STATUS_MAP[status] ?: ("" to "غير محدد")
            val (qSender, qText) = if (qId > 0L) (lookup[qId] ?: ("" to "")) else ("" to "")
            val displayText = when {
                isDeleted && data.isBlank() -> "🚫 تم حذف هذه الرسالة"
                data.isNotBlank() -> data
                mCap.isNotBlank() -> mCap
                wType != 0 -> "[$typeLabel]"
                else -> ""
            }
            WaMessage(
                rowId = id,
                chatId = chat.chatId,
                senderName = senderName,
                senderPhone = if (fromMe) "" else extractPhone(effJid),
                fromMe = fromMe,
                timestampMs = ts,
                timestampStr = tsStr,
                dateStr = dStr,
                timeStr = tStr,
                text = displayText,
                messageType = wType,
                typeLabel = typeLabel,
                statusCode = status,
                statusIcon = stIcon,
                statusLabel = stLabel,
                isDeleted = isDeleted,
                quotedSender = qSender,
                quotedText = qText,
                mediaRelPath = mName,
                mediaUri = resolveMediaUri(mName, mediaIndex),
            )
        }
    }

    private fun resolveMediaUri(relPath: String, mediaIndex: Map<String, Uri>): Uri? {
        if (relPath.isBlank() || mediaIndex.isEmpty()) return null
        val fileName = relPath.replace('\\', '/').substringAfterLast('/').lowercase(Locale.US)
        return mediaIndex[fileName]
    }

    private fun resolveDisplayName(jid: String, subject: String, contacts: Map<String, String>): String {
        if (subject.isNotBlank()) return subject
        contacts[jid]?.let { return it }
        return extractPhone(jid)
    }

    private fun extractPhone(jid: String): String {
        val user = jid.substringBefore('@')
        return if (user.length >= 7 && user.all { it.isDigit() }) "+$user" else user.ifBlank { jid }
    }

    private fun formatTimestamp(rawTs: Long): Triple<String, String, String> {
        if (rawTs <= 0L) return Triple("غير معروف", "", "")
        val ms = if (rawTs < 10_000_000_000L) rawTs * 1000L else rawTs
        return try {
            val d = Date(ms)
            val full = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(d)
            val date = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(d)
            val time = SimpleDateFormat("HH:mm", Locale.US).format(d)
            Triple(full, date, time)
        } catch (_: Exception) {
            Triple("غير معروف", "", "")
        }
    }

    private fun getTableNames(db: SQLiteDatabase): Set<String> {
        val set = mutableSetOf<String>()
        db.rawQuery("SELECT name FROM sqlite_master WHERE type='table'", null).use { c ->
            while (c.moveToNext()) set.add(c.getString(0))
        }
        return set
    }

    private fun getColumnNames(db: SQLiteDatabase, table: String): Set<String> {
        val set = mutableSetOf<String>()
        db.rawQuery("PRAGMA table_info($table)", null).use { c ->
            while (c.moveToNext()) set.add(c.getString(1))
        }
        return set
    }
}
