package com.example.forensics

import android.content.Context
import android.net.Uri
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

data class ForensicArtifactItem(
    val source: String,
    val pageOrFrame: Int,
    val offset: Long,
    val extractedText: String,
    val relatedJid: String,
    val confidence: String,
)

object SqliteForensicScanner {

    private val READABLE_RE = Regex("[\\u0600-\\u06FF\\u0750-\\u077FA-Za-z0-9@#\\+:/\\.\\-_,\\?!\\s]{5,}")
    private val JID_RE = Regex("(\\d{8,15}@(?:s\\.whatsapp\\.net|g\\.us|lid)|\\+\\d{8,15})")

    fun scanReadOnly(
        context: Context,
        dbFile: File?,
        walUri: Uri? = null,
        maxArtifacts: Int = 1500,
    ): List<ForensicArtifactItem> {
        val artifacts = mutableListOf<ForensicArtifactItem>()
        val seen = HashSet<String>()

        if (dbFile != null && dbFile.exists()) {
            scanMainDb(dbFile, artifacts, seen, maxArtifacts)
        }

        if (walUri != null && artifacts.size < maxArtifacts) {
            val walBytes = context.contentResolver.openInputStream(walUri)?.use { it.readBytes() }
            if (walBytes != null) {
                scanWalBytes(walBytes, artifacts, seen, maxArtifacts)
            }
        }
        return artifacts
    }

    private fun scanMainDb(
        dbFile: File,
        out: MutableList<ForensicArtifactItem>,
        seen: MutableSet<String>,
        maxArtifacts: Int,
    ) {
        val fileLen = dbFile.length()
        if (fileLen < 100L) return

        RandomAccessFile(dbFile, "r").use { raf ->
            val hdr = ByteArray(100)
            raf.readFully(hdr)
            if (!String(hdr, 0, 16, Charsets.US_ASCII).startsWith("SQLite format 3")) return

            var pageSize = ByteBuffer.wrap(hdr, 16, 2).order(ByteOrder.BIG_ENDIAN).short.toInt() and 0xFFFF
            if (pageSize == 1) pageSize = 65536
            if (pageSize < 512) return

            val firstTrunk = ByteBuffer.wrap(hdr, 32, 4).order(ByteOrder.BIG_ENDIAN).int
            val totalFreelist = ByteBuffer.wrap(hdr, 36, 4).order(ByteOrder.BIG_ENDIAN).int

            val freelistPages = linkedSetOf<Int>()
            val visitedTrunks = mutableSetOf<Int>()
            var trunk = firstTrunk
            while (trunk > 0 && trunk !in visitedTrunks && freelistPages.size < totalFreelist + 100) {
                visitedTrunks.add(trunk)
                freelistPages.add(trunk)
                val off = (trunk.toLong() - 1L) * pageSize
                if (off + pageSize > fileLen) break
                val pg = ByteArray(pageSize)
                raf.seek(off)
                raf.readFully(pg)
                val nextTrunk = ByteBuffer.wrap(pg, 0, 4).order(ByteOrder.BIG_ENDIAN).int
                val leafCount = ByteBuffer.wrap(pg, 4, 4).order(ByteOrder.BIG_ENDIAN).int
                val maxEntries = minOf(leafCount, (pageSize - 8) / 4).coerceAtLeast(0)
                for (i in 0 until maxEntries) {
                    val leafPg = ByteBuffer.wrap(pg, 8 + i * 4, 4).order(ByteOrder.BIG_ENDIAN).int
                    if (leafPg > 0) freelistPages.add(leafPg)
                }
                trunk = nextTrunk
            }

            for (pgNum in freelistPages) {
                if (out.size >= maxArtifacts) break
                val off = (pgNum.toLong() - 1L) * pageSize
                if (off + pageSize > fileLen) continue
                val pg = ByteArray(pageSize)
                raf.seek(off)
                raf.readFully(pg)
                extractStrings(pg).forEach { txt ->
                    if (seen.add(txt) && out.size < maxArtifacts) {
                        out.add(
                            ForensicArtifactItem(
                                source = "صفحة محذوفة (Freelist)",
                                pageOrFrame = pgNum,
                                offset = off,
                                extractedText = txt,
                                relatedJid = findJid(txt),
                                confidence = if (txt.length >= 12) "عالية" else "متوسطة",
                            )
                        )
                    }
                }
            }

            val totalPages = (fileLen / pageSize).toInt()
            for (pgNum in 2..totalPages) {
                if (out.size >= maxArtifacts) break
                if (pgNum in freelistPages) continue
                val off = (pgNum.toLong() - 1L) * pageSize
                val pg = ByteArray(pageSize)
                raf.seek(off)
                raf.readFully(pg)
                if (pg[0] != 0x0D.toByte()) continue

                val firstFb = ByteBuffer.wrap(pg, 1, 2).order(ByteOrder.BIG_ENDIAN).short.toInt() and 0xFFFF
                val cellCount = ByteBuffer.wrap(pg, 3, 2).order(ByteOrder.BIG_ENDIAN).short.toInt() and 0xFFFF
                var contentStart = ByteBuffer.wrap(pg, 5, 2).order(ByteOrder.BIG_ENDIAN).short.toInt() and 0xFFFF
                if (contentStart == 0) contentStart = 65536

                val ptrEnd = 8 + cellCount * 2
                if (ptrEnd < contentStart && contentStart <= pageSize) {
                    val slice = pg.copyOfRange(ptrEnd, contentStart)
                    extractStrings(slice).forEach { txt ->
                        if (seen.add(txt) && out.size < maxArtifacts) {
                            out.add(
                                ForensicArtifactItem(
                                    source = "مساحة غير مخصصة (B-Tree)",
                                    pageOrFrame = pgNum,
                                    offset = off + ptrEnd,
                                    extractedText = txt,
                                    relatedJid = findJid(txt),
                                    confidence = if (txt.length >= 12) "عالية" else "متوسطة",
                                )
                            )
                        }
                    }
                }

                var fb = firstFb
                val visitedFb = mutableSetOf<Int>()
                while (fb in 8 until (pageSize - 4) && visitedFb.add(fb) && out.size < maxArtifacts) {
                    val nextFb = ByteBuffer.wrap(pg, fb, 2).order(ByteOrder.BIG_ENDIAN).short.toInt() and 0xFFFF
                    val fbSize = ByteBuffer.wrap(pg, fb + 2, 2).order(ByteOrder.BIG_ENDIAN).short.toInt() and 0xFFFF
                    if (fbSize <= 4 || fb + fbSize > pageSize) break
                    val payload = pg.copyOfRange(fb + 4, fb + fbSize)
                    extractStrings(payload).forEach { txt ->
                        if (seen.add(txt) && out.size < maxArtifacts) {
                            out.add(
                                ForensicArtifactItem(
                                    source = "كتلة حرة (Freeblock)",
                                    pageOrFrame = pgNum,
                                    offset = off + fb,
                                    extractedText = txt,
                                    relatedJid = findJid(txt),
                                    confidence = "عالية",
                                )
                            )
                        }
                    }
                    fb = nextFb
                }
            }
        }
    }

    private fun scanWalBytes(
        wal: ByteArray,
        out: MutableList<ForensicArtifactItem>,
        seen: MutableSet<String>,
        maxArtifacts: Int,
    ) {
        if (wal.size < 32) return
        val magic = ByteBuffer.wrap(wal, 0, 4).order(ByteOrder.BIG_ENDIAN).int
        if (magic != 0x377F0682 && magic != 0x377F0683) return
        val pageSize = ByteBuffer.wrap(wal, 8, 4).order(ByteOrder.BIG_ENDIAN).int
        if (pageSize !in 512..65536) return

        var off = 32
        var frameIdx = 0
        val frameSize = 24 + pageSize
        while (off + frameSize <= wal.size && out.size < maxArtifacts) {
            val pgNum = ByteBuffer.wrap(wal, off, 4).order(ByteOrder.BIG_ENDIAN).int
            val pgData = wal.copyOfRange(off + 24, off + frameSize)
            frameIdx++
            val tag = if (pgData[0] == 0x0D.toByte()) "إطار WAL (B-Tree #$pgNum)" else "إطار WAL (#$pgNum)"
            extractStrings(pgData).forEach { txt ->
                if (seen.add(txt) && out.size < maxArtifacts) {
                    out.add(
                        ForensicArtifactItem(
                            source = tag,
                            pageOrFrame = frameIdx,
                            offset = (off + 24).toLong(),
                            extractedText = txt,
                            relatedJid = findJid(txt),
                            confidence = if (pgData[0] == 0x0D.toByte()) "عالية" else "متوسطة",
                        )
                    )
                }
            }
            off += frameSize
        }
    }

    private fun extractStrings(bytes: ByteArray): List<String> {
        val text = String(bytes, Charsets.UTF_8)
        val res = mutableListOf<String>()
        for (m in READABLE_RE.findAll(text)) {
            val cleaned = m.value.trim().replace(Regex("\\s+"), " ")
            if (cleaned.length >= 5 && cleaned.toSet().size > 2) {
                val low = cleaned.lowercase()
                if (low.startsWith("create table") || low.startsWith("create index") || low.startsWith("sqlite_")) continue
                res.add(cleaned)
            }
        }
        return res
    }

    private fun findJid(txt: String): String = JID_RE.find(txt)?.value ?: ""
}
