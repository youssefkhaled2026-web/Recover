package com.example.recovery

import android.content.Context
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.DocumentsContract
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlinx.coroutines.isActive
import kotlin.coroutines.coroutineContext

data class FileSignature(
    val ext: String,
    val category: String,
    val header: ByteArray,
    val footer: ByteArray? = null,
    val headerOffset: Int = 0,
    val maxSize: Int = 20 * 1024 * 1024,
    val description: String = "",
)

data class CarvedFileItem(
    val id: Int,
    val name: String,
    val ext: String,
    val category: String,
    val offset: Long,
    val size: Int,
    val probability: String,
    val probabilityPct: Int,
    val sourceUri: Uri,
    val isDirectFile: Boolean = false,
    val notes: String = "",
)

object FileCarverEngine {

    val SIGNATURES: List<FileSignature> = listOf(
        FileSignature("jpg", "صور", byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte()), byteArrayOf(0xFF.toByte(), 0xD9.toByte()), description = "صورة JPEG"),
        FileSignature("png", "صور", byteArrayOf(0x89.toByte(), 'P'.code.toByte(), 'N'.code.toByte(), 'G'.code.toByte(), 0x0D, 0x0A, 0x1A, 0x0A), byteArrayOf('I'.code.toByte(), 'E'.code.toByte(), 'N'.code.toByte(), 'D'.code.toByte(), 0xAE.toByte(), 0x42, 0x60, 0x82.toByte()), description = "صورة PNG"),
        FileSignature("gif", "صور", "GIF89a".toByteArray(), byteArrayOf(0x00, ';'.code.toByte()), description = "صورة GIF"),
        FileSignature("webp", "صور", "RIFF".toByteArray(), null, description = "صورة WebP"),
        FileSignature("bmp", "صور", "BM".toByteArray(), null, description = "صورة BMP"),
        FileSignature("mp4", "فيديو", "ftyp".toByteArray(), null, headerOffset = 4, maxSize = 60 * 1024 * 1024, description = "فيديو MP4/MOV"),
        FileSignature("ogg", "صوتيات", "OggS".toByteArray(), null, description = "صوت OGG / WhatsApp Opus"),
        FileSignature("mp3", "صوتيات", "ID3".toByteArray(), null, description = "ملف صوتي MP3"),
        FileSignature("wav", "صوتيات", "RIFF".toByteArray(), null, description = "ملف صوتي WAV"),
        FileSignature("flac", "صوتيات", "fLaC".toByteArray(), null, description = "ملف صوتي FLAC"),
        FileSignature("pdf", "مستندات", "%PDF-".toByteArray(), "%%EOF".toByteArray(), description = "مستند PDF"),
        FileSignature("zip", "مستندات", byteArrayOf('P'.code.toByte(), 'K'.code.toByte(), 0x03, 0x04), byteArrayOf('P'.code.toByte(), 'K'.code.toByte(), 0x05, 0x06), description = "أرشيف ZIP / Office"),
        FileSignature("db", "قواعد بيانات", "SQLite format 3\u0000".toByteArray(), null, description = "قاعدة بيانات SQLite"),
    )

    /**
     * فحص مجلد اختاره المستخدم عبر SAF (مثل USB OTG أو كارت SD أو WhatsApp/Media) بوضع القراءة فقط.
     */
    suspend fun scanDocumentTreeReadOnly(
        context: Context,
        treeUri: Uri,
        allowedCategories: Set<String>,
        onProgress: (String) -> Unit,
    ): List<CarvedFileItem> {
        val results = mutableListOf<CarvedFileItem>()
        val activeSigs = SIGNATURES.filter { it.category in allowedCategories }
        if (activeSigs.isEmpty()) return results

        val rootDocId = DocumentsContract.getTreeDocumentId(treeUri)
        val queue = ArrayDeque<String>()
        queue.add(rootDocId)
        var scannedCount = 0

        while (queue.isNotEmpty() && coroutineContext.isActive) {
            val parentId = queue.removeFirst()
            val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentId)
            context.contentResolver.query(
                childrenUri,
                arrayOf(
                    DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                    DocumentsContract.Document.COLUMN_MIME_TYPE,
                    DocumentsContract.Document.COLUMN_SIZE,
                ),
                null,
                null,
                null,
            )?.use { cursor ->
                while (cursor.moveToNext() && coroutineContext.isActive) {
                    val docId = cursor.getString(0)
                    val name = cursor.getString(1) ?: "file"
                    val mime = cursor.getString(2) ?: ""
                    val size = cursor.getLong(3).toInt().coerceAtLeast(0)

                    if (mime == DocumentsContract.Document.MIME_TYPE_DIR) {
                        queue.add(docId)
                    } else {
                        scannedCount++
                        if (scannedCount % 20 == 0) {
                            onProgress("تم فحص $scannedCount ملف | النتائج: ${results.size}")
                        }
                        val docUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, docId)
                        val head = ByteArray(32)
                        val readLen = try {
                            context.contentResolver.openInputStream(docUri)?.use { it.read(head) } ?: 0
                        } catch (_: Exception) {
                            0
                        }
                        val matched = matchHeader(head.copyOf(readLen.coerceAtLeast(0)), activeSigs)
                            ?: activeSigs.firstOrNull { name.lowercase().endsWith(".${it.ext}") }
                        if (matched != null && size > 0) {
                            val (prob, pct, note) = evaluateDirectFile(context, docUri, matched, size)
                            results.add(
                                CarvedFileItem(
                                    id = results.size + 1,
                                    name = name,
                                    ext = matched.ext,
                                    category = matched.category,
                                    offset = 0L,
                                    size = size,
                                    probability = prob,
                                    probabilityPct = pct,
                                    sourceUri = docUri,
                                    isDirectFile = true,
                                    notes = note,
                                )
                            )
                        }
                    }
                }
            }
        }
        onProgress("اكتمل فحص المجلد: ${results.size} ملف")
        return results
    }

    /**
     * فحص صورة قرص أو ملف خام (.img / .dd / .raw) بطريقة File Carving بالبصمات بوضع القراءة فقط.
     */
    suspend fun carveDiskImage(
        context: Context,
        imageUri: Uri,
        allowedCategories: Set<String>,
        onProgress: (String) -> Unit,
    ): List<CarvedFileItem> {
        val activeSigs = SIGNATURES.filter { it.category in allowedCategories }
        if (activeSigs.isEmpty()) return emptyList()

        // نسخ متدفق أو قراءة مباشرة عبر ملف مؤقت للقراءة العشوائية السريعة (حتى 64 ميجا للقطع المحمولة أو عبر التدفق)
        val tempFile = File(context.cacheDir, "carve_scan_ro.bin")
        context.contentResolver.openInputStream(imageUri)?.use { input ->
            FileOutputStream(tempFile).use { output ->
                val buf = ByteArray(64 * 1024)
                var totalCopied = 0L
                while (coroutineContext.isActive) {
                    val r = input.read(buf)
                    if (r <= 0) break
                    output.write(buf, 0, r)
                    totalCopied += r
                    if (totalCopied % (2 * 1024 * 1024) < 64 * 1024) {
                        onProgress("جاري تجهيز نسخة القراءة فقط: ${totalCopied / (1024 * 1024)} MB...")
                    }
                }
            }
        } ?: return emptyList()

        return try {
            carveLocalFile(tempFile, imageUri, activeSigs, onProgress)
        } finally {
            // نُبقي الملف فقط أثناء الجلسة عند المعاينة أو الحفظ إذا لزم؛ هنا نحفظه بالاسم carve_scan_ro.bin للمعاينة السريعة
        }
    }

    private suspend fun carveLocalFile(
        file: File,
        sourceUri: Uri,
        signatures: List<FileSignature>,
        onProgress: (String) -> Unit,
    ): List<CarvedFileItem> {
        val results = mutableListOf<CarvedFileItem>()
        val seenOffsets = HashSet<Long>()
        val fileLen = file.length()
        val chunkSize = 1024 * 1024
        val overlap = 64
        var offset = 0L

        java.io.RandomAccessFile(file, "r").use { raf ->
            val buf = ByteArray(chunkSize + overlap)
            while (offset < fileLen && coroutineContext.isActive) {
                raf.seek(offset)
                val toRead = minOf(buf.size.toLong(), fileLen - offset).toInt()
                val read = raf.read(buf, 0, toRead)
                if (read <= 0) break
                val scanLimit = minOf(read, chunkSize)

                for (sig in signatures) {
                    var pos = 0
                    while (pos < scanLimit && coroutineContext.isActive) {
                        val idx = indexOfBytes(buf, sig.header, pos, minOf(read, scanLimit + sig.header.size))
                        if (idx == -1 || idx >= scanLimit) break
                        val actualStart = offset + idx - sig.headerOffset
                        pos = idx + 1
                        if (actualStart < 0 || actualStart in seenOffsets) continue

                        if (sig.header.contentEquals("RIFF".toByteArray())) {
                            if (idx + 12 > read) continue
                            val tag = String(buf, idx + 8, 4, Charsets.US_ASCII)
                            val expected = when (sig.ext) {
                                "webp" -> "WEBP"
                                "wav" -> "WAVE"
                                else -> ""
                            }
                            if (tag != expected) continue
                        }
                        if (sig.ext == "mp4") {
                            if (idx < 4 || idx + 8 > read) continue
                            val atomSize = ByteBuffer.wrap(buf, idx - 4, 4).order(ByteOrder.BIG_ENDIAN).int
                            if (atomSize !in 16..1024) continue
                        }

                        seenOffsets.add(actualStart)
                        val (estSize, cleanEnd, note) = determineBounds(raf, fileLen, actualStart, sig)
                        if (estSize <= 16) continue

                        val (prob, pct, fullNote) = scoreCarved(raf, actualStart, estSize, sig, cleanEnd, note)
                        val name = "recovered_%04d_0x%X.%s".format(results.size + 1, actualStart, sig.ext)
                        results.add(
                            CarvedFileItem(
                                id = results.size + 1,
                                name = name,
                                ext = sig.ext,
                                category = sig.category,
                                offset = actualStart,
                                size = estSize,
                                probability = prob,
                                probabilityPct = pct,
                                sourceUri = sourceUri,
                                isDirectFile = false,
                                notes = fullNote,
                            )
                        )
                    }
                }

                offset += if (read < chunkSize) read else chunkSize
                val pctDone = ((offset * 100) / fileLen.coerceAtLeast(1L)).toInt()
                onProgress("جاري الفحص: $pctDone% | تم العثور على ${results.size} ملف")
            }
        }
        onProgress("انتهى الفحص: ${results.size} ملف")
        return results
    }

    private fun determineBounds(
        raf: java.io.RandomAccessFile,
        fileLen: Long,
        startOffset: Long,
        sig: FileSignature,
    ): Triple<Int, Boolean, String> {
        raf.seek(startOffset)
        if (sig.header.contentEquals("RIFF".toByteArray())) {
            val hdr = ByteArray(12)
            if (raf.read(hdr) == 12) {
                val len = ByteBuffer.wrap(hdr, 4, 4).order(ByteOrder.LITTLE_ENDIAN).int + 8
                if (len in 32..sig.maxSize) return Triple(len, true, "حجم دقيق من ترويسة RIFF")
            }
        }
        if (sig.ext == "bmp") {
            val hdr = ByteArray(14)
            if (raf.read(hdr) == 14) {
                val len = ByteBuffer.wrap(hdr, 2, 4).order(ByteOrder.LITTLE_ENDIAN).int
                if (len in 64..sig.maxSize) return Triple(len, true, "حجم دقيق من ترويسة BMP")
            }
        }
        if (sig.ext == "db") {
            val hdr = ByteArray(100)
            if (raf.read(hdr) == 100) {
                var pgSize = ByteBuffer.wrap(hdr, 16, 2).order(ByteOrder.BIG_ENDIAN).short.toInt() and 0xFFFF
                if (pgSize == 1) pgSize = 65536
                val pgCount = ByteBuffer.wrap(hdr, 28, 4).order(ByteOrder.BIG_ENDIAN).int
                val total = pgSize * pgCount
                if (total in 1024..sig.maxSize) return Triple(total, true, "SQLite ($pgCount صفحة)")
            }
        }
        val footer = sig.footer
        if (footer != null) {
            val searchLimit = minOf(sig.maxSize.toLong(), fileLen - startOffset).toInt()
            val buf = ByteArray(minOf(searchLimit, 4 * 1024 * 1024))
            val read = raf.read(buf)
            if (read > 0) {
                val endIdx = indexOfBytes(buf, footer, 0, read)
                if (endIdx != -1) {
                    var total = endIdx + footer.size
                    if (sig.ext == "zip" && endIdx + 22 <= read) {
                        val cLen = ByteBuffer.wrap(buf, endIdx + 20, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt() and 0xFFFF
                        total += 18 + cLen
                    }
                    return Triple(minOf(total, read), true, "علامة نهاية مكتملة")
                }
            }
            return Triple(minOf(read, 1024 * 1024), false, "لم يُعثر على علامة النهاية")
        }
        val avail = minOf((fileLen - startOffset).toInt(), 2 * 1024 * 1024)
        return Triple(avail, false, "حجم تقديري")
    }

    private fun scoreCarved(
        raf: java.io.RandomAccessFile,
        offset: Long,
        size: Int,
        sig: FileSignature,
        cleanEnd: Boolean,
        note: String,
    ): Triple<String, Int, String> {
        var score = if (cleanEnd) 82 else 48
        var finalNote = note
        if (sig.category == "صور" && size <= 8 * 1024 * 1024) {
            try {
                val bytes = ByteArray(size)
                raf.seek(offset)
                raf.readFully(bytes)
                val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
                if (opts.outWidth > 0 && opts.outHeight > 0) {
                    score = 96
                    finalNote = "صورة سليمة (${opts.outWidth}×${opts.outHeight})"
                } else {
                    score = 52
                    finalNote = "تلف جزئي في بيانات الصورة"
                }
            } catch (_: Exception) {
                score = 50
            }
        }
        val label = when {
            score >= 80 -> "عالية"
            score >= 55 -> "متوسطة"
            else -> "منخفضة"
        }
        return Triple(label, score, finalNote)
    }

    private fun evaluateDirectFile(
        context: Context,
        uri: Uri,
        sig: FileSignature,
        size: Int,
    ): Triple<String, Int, String> {
        if (sig.category == "صور" && size <= 12 * 1024 * 1024) {
            try {
                context.contentResolver.openInputStream(uri)?.use { input ->
                    val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    BitmapFactory.decodeStream(input, null, opts)
                    if (opts.outWidth > 0 && opts.outHeight > 0) {
                        return Triple("عالية", 98, "صورة سليمة (${opts.outWidth}×${opts.outHeight})")
                    }
                }
            } catch (_: Exception) {
            }
        }
        return Triple("عالية", 90, "ملف موجود في المسار المختار")
    }

    fun readPreviewBytes(context: Context, item: CarvedFileItem, maxBytes: Int = 4 * 1024 * 1024): ByteArray {
        val toRead = minOf(item.size, maxBytes)
        if (item.isDirectFile) {
            return context.contentResolver.openInputStream(item.sourceUri)?.use { input ->
                readUpTo(input, toRead)
            } ?: ByteArray(0)
        }
        val cacheFile = File(context.cacheDir, "carve_scan_ro.bin")
        if (!cacheFile.exists()) return ByteArray(0)
        val out = ByteArray(toRead)
        java.io.RandomAccessFile(cacheFile, "r").use { raf ->
            raf.seek(item.offset)
            raf.readFully(out)
        }
        return out
    }

    fun saveRecoveredItemToUri(context: Context, item: CarvedFileItem, destUri: Uri) {
        context.contentResolver.openOutputStream(destUri)?.use { out ->
            if (item.isDirectFile) {
                context.contentResolver.openInputStream(item.sourceUri)?.use { input ->
                    input.copyTo(out)
                }
            } else {
                val cacheFile = File(context.cacheDir, "carve_scan_ro.bin")
                java.io.RandomAccessFile(cacheFile, "r").use { raf ->
                    raf.seek(item.offset)
                    val buf = ByteArray(64 * 1024)
                    var remaining = item.size
                    while (remaining > 0) {
                        val r = raf.read(buf, 0, minOf(buf.size, remaining))
                        if (r <= 0) break
                        out.write(buf, 0, r)
                        remaining -= r
                    }
                }
            }
        }
    }

    private fun readUpTo(input: InputStream, limit: Int): ByteArray {
        val baos = ByteArrayOutputStream()
        val buf = ByteArray(16 * 1024)
        var total = 0
        while (total < limit) {
            val r = input.read(buf, 0, minOf(buf.size, limit - total))
            if (r <= 0) break
            baos.write(buf, 0, r)
            total += r
        }
        return baos.toByteArray()
    }

    private fun matchHeader(head: ByteArray, signatures: List<FileSignature>): FileSignature? {
        for (sig in signatures) {
            val off = sig.headerOffset
            if (head.size >= off + sig.header.size) {
                var eq = true
                for (i in sig.header.indices) {
                    if (head[off + i] != sig.header[i]) {
                        eq = false
                        break
                    }
                }
                if (eq) {
                    if (sig.header.contentEquals("RIFF".toByteArray()) && head.size >= 12) {
                        val tag = String(head, 8, 4, Charsets.US_ASCII)
                        if (sig.ext == "webp" && tag != "WEBP") continue
                        if (sig.ext == "wav" && tag != "WAVE") continue
                    }
                    return sig
                }
            }
        }
        return null
    }

    private fun indexOfBytes(haystack: ByteArray, needle: ByteArray, from: Int, limit: Int): Int {
        val end = limit - needle.size
        if (from > end) return -1
        for (i in from..end) {
            var matched = true
            for (j in needle.indices) {
                if (haystack[i + j] != needle[j]) {
                    matched = false
                    break
                }
            }
            if (matched) return i
        }
        return -1
    }
}
