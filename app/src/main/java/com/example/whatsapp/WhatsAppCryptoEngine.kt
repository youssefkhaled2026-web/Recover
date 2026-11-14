package com.example.whatsapp

import android.content.Context
import android.net.Uri
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.util.zip.Inflater
import java.util.zip.InflaterInputStream
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

data class DecryptionOutcome(
    val success: Boolean,
    val decryptedDbFile: File? = null,
    val detectedFormat: String = "",
    val message: String = "",
)

object WhatsAppCryptoEngine {

    private val SQLITE_MAGIC = "SQLite format 3\u0000".toByteArray(Charsets.US_ASCII)

    private fun readVarint(data: ByteArray, startPos: Int): Pair<Int, Int> {
        var result = 0
        var shift = 0
        var pos = startPos
        while (pos < data.size) {
            val b = data[pos++].toInt() and 0xFF
            result = result or ((b and 0x7F) shl shift)
            if ((b and 0x80) == 0) return result to pos
            shift += 7
            if (shift > 35) throw IllegalArgumentException("ترويسة Protobuf غير صالحة")
        }
        throw IllegalArgumentException("ترويسة ناقصة")
    }

    private fun parseProtobuf(data: ByteArray): Map<Int, List<Pair<Int, Any>>> {
        val fields = mutableMapOf<Int, MutableList<Pair<Int, Any>>>()
        var pos = 0
        while (pos < data.size) {
            val (tag, nextPos) = readVarint(data, pos)
            pos = nextPos
            val fieldNum = tag ushr 3
            val wireType = tag and 0x07
            when (wireType) {
                0 -> {
                    val (v, p2) = readVarint(data, pos)
                    pos = p2
                    fields.getOrPut(fieldNum) { mutableListOf() }.add(0 to v)
                }
                2 -> {
                    val (len, p2) = readVarint(data, pos)
                    pos = p2
                    if (len < 0 || pos + len > data.size) throw IllegalArgumentException("طول حقل غير صالح")
                    val slice = data.copyOfRange(pos, pos + len)
                    pos += len
                    fields.getOrPut(fieldNum) { mutableListOf() }.add(2 to slice)
                }
                5 -> {
                    if (pos + 4 > data.size) throw IllegalArgumentException("تجاوز 32-bit")
                    val slice = data.copyOfRange(pos, pos + 4)
                    pos += 4
                    fields.getOrPut(fieldNum) { mutableListOf() }.add(5 to slice)
                }
                1 -> {
                    if (pos + 8 > data.size) throw IllegalArgumentException("تجاوز 64-bit")
                    val slice = data.copyOfRange(pos, pos + 8)
                    pos += 8
                    fields.getOrPut(fieldNum) { mutableListOf() }.add(1 to slice)
                }
                else -> throw IllegalArgumentException("نوع حقل Protobuf غير مدعوم: $wireType")
            }
        }
        return fields
    }

    private fun hkdfSha25632(ikm: ByteArray, info: ByteArray): ByteArray {
        val mac1 = Mac.getInstance("HmacSHA256")
        mac1.init(SecretKeySpec(ByteArray(32), "HmacSHA256"))
        val prk = mac1.doFinal(ikm)

        val mac2 = Mac.getInstance("HmacSHA256")
        mac2.init(SecretKeySpec(prk, "HmacSHA256"))
        mac2.update(info)
        mac2.update(byteArrayOf(0x01))
        return mac2.doFinal().copyOf(32)
    }

    fun normalize64HexKey(input: String): ByteArray {
        val hex = input.filter { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' }
        require(hex.length == 64) {
            "يجب أن يتكون مفتاح التشفير من 64 خانة سادس عشرية (الطول الحالي: ${hex.length})."
        }
        return ByteArray(32) { i ->
            hex.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
    }

    private fun extractKeyFromFileBytes(raw: ByteArray, isCrypt15: Boolean): ByteArray {
        if (isCrypt15) {
            if (raw.size == 32) return hkdfSha25632(raw, "backup encryption".toByteArray())
            runCatching {
                val fields = parseProtobuf(raw)
                fields.values.flatten().forEach { (wt, v) ->
                    if (wt == 2 && v is ByteArray && v.size == 32) {
                        return hkdfSha25632(v, "backup encryption".toByteArray())
                    }
                }
            }
            if (raw.size >= 32) {
                return hkdfSha25632(raw.copyOfRange(raw.size - 32, raw.size), "backup encryption".toByteArray())
            }
            throw IllegalArgumentException("ملف مفتاح crypt15 غير صالح")
        }

        return when {
            raw.size == 158 -> raw.copyOfRange(126, 158)
            raw.size == 131 -> raw.copyOfRange(99, 131)
            raw.size == 32 -> raw
            raw.size > 32 -> raw.copyOfRange(raw.size - 32, raw.size)
            else -> throw IllegalArgumentException("حجم ملف المفتاح غير صالح (${raw.size} بايت)")
        }
    }

    private fun parseHeaderAndPayload(blob: ByteArray): Triple<String, ByteArray, ByteArray> {
        if (blob.size < 64) throw IllegalArgumentException("الملف صغير جدًا ولا يمثل نسخة واتساب صالحة.")
        val (hdrLen, pos) = readVarint(blob, 0)
        if (hdrLen <= 0 || hdrLen > 2048) throw IllegalArgumentException("طول ترويسة غير منطقي: $hdrLen")

        fun tryExtractIv(hdrBytes: ByteArray): Pair<String, ByteArray>? {
            val fields = runCatching { parseProtobuf(hdrBytes) }.getOrNull() ?: return null
            fields[3]?.forEach { (wt, v) ->
                if (wt == 2 && v is ByteArray) {
                    val sub = runCatching { parseProtobuf(v) }.getOrNull()
                    sub?.get(1)?.forEach { (swt, sv) ->
                        if (swt == 2 && sv is ByteArray && sv.size == 16) return "crypt15" to sv
                    }
                }
            }
            fields[2]?.forEach { (wt, v) ->
                if (wt == 2 && v is ByteArray) {
                    val sub = runCatching { parseProtobuf(v) }.getOrNull()
                    for (idx in listOf(5, 4, 3, 1)) {
                        sub?.get(idx)?.forEach { (swt, sv) ->
                            if (swt == 2 && sv is ByteArray && sv.size == 16) return "crypt14" to sv
                        }
                    }
                }
            }
            return null
        }

        if (pos < blob.size && (blob[pos] == 0x00.toByte() || blob[pos] == 0x01.toByte()) && pos + 1 + hdrLen <= blob.size) {
            val cand = blob.copyOfRange(pos + 1, pos + 1 + hdrLen)
            tryExtractIv(cand)?.let { (fmt, iv) ->
                return Triple(fmt, iv, blob.copyOfRange(pos + 1 + hdrLen, blob.size))
            }
        }
        if (pos + hdrLen <= blob.size) {
            val cand = blob.copyOfRange(pos, pos + hdrLen)
            tryExtractIv(cand)?.let { (fmt, iv) ->
                return Triple(fmt, iv, blob.copyOfRange(pos + hdrLen, blob.size))
            }
        }
        throw IllegalArgumentException("تعذر قراءة ترويسة Protobuf من ملف النسخة الاحتياطية.")
    }

    fun decryptBackupUri(
        context: Context,
        backupUri: Uri,
        keyFileUri: Uri? = null,
        hexKey64: String? = null,
    ): DecryptionOutcome {
        val blob = context.contentResolver.openInputStream(backupUri)?.use { it.readBytes() }
            ?: return DecryptionOutcome(false, message = "تعذر فتح ملف النسخة الاحتياطية.")

        val outFile = File(context.cacheDir, "wa_msgstore_ro.db")
        if (blob.size >= 16 && blob.copyOfRange(0, 16).contentEquals(SQLITE_MAGIC)) {
            FileOutputStream(outFile).use { it.write(blob) }
            return DecryptionOutcome(true, outFile, "SQLite (غير مشفر)", "تم تحميل قاعدة البيانات الجاهزة بنجاح.")
        }

        val (fmt, iv, ciphertext) = try {
            parseHeaderAndPayload(blob)
        } catch (e: Exception) {
            return DecryptionOutcome(false, message = e.message ?: "ترويسة غير صالحة")
        }

        val candidateKeys = mutableListOf<Pair<String, ByteArray>>()
        try {
            if (!hexKey64.isNullOrBlank()) {
                val root = normalize64HexKey(hexKey64)
                candidateKeys.add("مفتاح 64 خانة (crypt15)" to hkdfSha25632(root, "backup encryption".toByteArray()))
            }
            if (keyFileUri != null) {
                val keyBytes = context.contentResolver.openInputStream(keyFileUri)?.use { it.readBytes() }
                if (keyBytes != null) {
                    if (fmt == "crypt15") {
                        candidateKeys.add("ملف مفتاح crypt15" to extractKeyFromFileBytes(keyBytes, true))
                        candidateKeys.add("ملف مفتاح مباشر" to extractKeyFromFileBytes(keyBytes, false))
                    } else {
                        candidateKeys.add("ملف مفتاح crypt14" to extractKeyFromFileBytes(keyBytes, false))
                    }
                }
            }
        } catch (e: Exception) {
            return DecryptionOutcome(false, detectedFormat = fmt, message = "خطأ في المفتاح: ${e.message}")
        }

        if (candidateKeys.isEmpty()) {
            return DecryptionOutcome(
                false,
                detectedFormat = fmt,
                message = "يرجى إرفاق ملف المفتاح (key) أو إدخال مفتاح الـ 64 رقمًا الخاص بالنسخة.",
            )
        }

        val payloadCandidates = mutableListOf(ciphertext)
        if (ciphertext.size > 48) {
            payloadCandidates.add(ciphertext.copyOfRange(0, ciphertext.size - 32))
        }

        var decrypted: ByteArray? = null
        var usedLabel = ""
        for ((label, aesKey) in candidateKeys) {
            for (ct in payloadCandidates) {
                try {
                    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                    cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(aesKey, "AES"), GCMParameterSpec(128, iv))
                    decrypted = cipher.doFinal(ct)
                    usedLabel = label
                    break
                } catch (_: Exception) {
                }
            }
            if (decrypted != null) break
        }

        if (decrypted == null) {
            return DecryptionOutcome(
                false,
                detectedFormat = fmt,
                message = "فشل فك التشفير: المفتاح غير مطابق لهذه النسخة الاحتياطية.",
            )
        }

        val dbBytes = if (decrypted.size >= 16 && decrypted.copyOfRange(0, 16).contentEquals(SQLITE_MAGIC)) {
            decrypted
        } else {
            try {
                InflaterInputStream(ByteArrayInputStream(decrypted), Inflater(false)).use { it.readBytes() }
            } catch (e: Exception) {
                return DecryptionOutcome(false, detectedFormat = fmt, message = "فشل فك الضغط zlib: ${e.message}")
            }
        }

        if (dbBytes.size < 16 || !dbBytes.copyOfRange(0, 16).contentEquals(SQLITE_MAGIC)) {
            return DecryptionOutcome(false, detectedFormat = fmt, message = "الناتج بعد فك التشفير ليس قاعدة بيانات SQLite صالحة.")
        }

        FileOutputStream(outFile).use { it.write(dbBytes) }
        return DecryptionOutcome(
            true,
            outFile,
            fmt,
            "تم فك التشفير بنجاح ($fmt) باستخدام $usedLabel.",
        )
    }
}
