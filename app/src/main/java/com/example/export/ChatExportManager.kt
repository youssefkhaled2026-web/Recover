package com.example.export

import android.content.Context
import android.graphics.Color
import android.graphics.Paint
import android.graphics.pdf.PdfDocument
import android.net.Uri
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import com.example.forensics.ForensicArtifactItem
import com.example.whatsapp.WaChat
import com.example.whatsapp.WaMessage

object ChatExportManager {

    private fun escapeHtml(s: String): String = s
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")

    private fun csvCell(s: String): String {
        val escaped = s.replace("\"", "\"\"")
        return "\"$escaped\""
    }

    fun exportCsvToUri(context: Context, chat: WaChat, messages: List<WaMessage>, destUri: Uri) {
        context.contentResolver.openOutputStream(destUri)?.use { out ->
            // BOM للتوافق التام مع العربية في Excel
            out.write(byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte()))
            val sb = StringBuilder()
            sb.appendLine("المعرف,المحادثة,المرسل,رقم الهاتف,الاتجاه,التاريخ والوقت,النوع,الحالة,محذوفة؟,الرد على,النص,مسار الوسائط")
            for (m in messages) {
                val quote = if (m.quotedText.isNotBlank()) "${m.quotedSender}: ${m.quotedText}" else ""
                sb.appendLine(
                    listOf(
                        m.rowId.toString(),
                        csvCell(chat.displayName),
                        csvCell(m.senderName),
                        csvCell(m.senderPhone),
                        if (m.fromMe) "مرسلة" else "واردة",
                        csvCell(m.timestampStr),
                        csvCell(m.typeLabel),
                        csvCell(m.statusLabel),
                        if (m.isDeleted) "نعم" else "لا",
                        csvCell(quote),
                        csvCell(m.text),
                        csvCell(m.mediaRelPath),
                    ).joinToString(",")
                )
            }
            out.write(sb.toString().toByteArray(Charsets.UTF_8))
        }
    }

    fun exportHtmlToUri(context: Context, chat: WaChat, messages: List<WaMessage>, destUri: Uri) {
        val bubbles = StringBuilder()
        for (m in messages) {
            val side = if (m.fromMe) "sent" else "recv"
            val delCls = if (m.isDeleted) " deleted" else ""
            val quoteHtml = if (m.quotedText.isNotBlank()) {
                """<div class="quote"><strong>${escapeHtml(m.quotedSender)}</strong><div>${escapeHtml(m.quotedText)}</div></div>"""
            } else ""
            val mediaHtml = if (m.mediaRelPath.isNotBlank()) {
                """<div class="media-tag">📎 ${escapeHtml(m.mediaRelPath.substringAfterLast('/'))}</div>"""
            } else ""

            bubbles.append(
                """
                <div class="row $side">
                  <div class="bubble$delCls">
                    <div class="sender">${escapeHtml(m.senderName)}</div>
                    $quoteHtml
                    $mediaHtml
                    <div class="body">${escapeHtml(m.text).replace("\n", "<br>")}</div>
                    <div class="meta">${escapeHtml(m.timestampStr)} ${if (m.fromMe) escapeHtml(m.statusIcon) else ""}</div>
                  </div>
                </div>
                """.trimIndent()
            )
        }

        val html = """
            <!DOCTYPE html>
            <html lang="ar" dir="rtl">
            <head>
              <meta charset="utf-8">
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <title>${escapeHtml(chat.displayName)}</title>
              <style>
                body { font-family: sans-serif; background: #EFEAE2; margin: 0; color: #111B21; }
                .hdr { background: #075E54; color: #fff; padding: 16px 20px; }
                .hdr h1 { margin: 0; font-size: 18px; }
                .hdr p { margin: 4px 0 0; font-size: 12px; opacity: 0.9; }
                .wrap { max-width: 820px; margin: 16px auto; padding: 0 12px 32px; }
                .row { display: flex; margin-bottom: 10px; }
                .row.sent { justify-content: flex-start; }
                .row.recv { justify-content: flex-end; }
                .bubble { max-width: 78%; background: #fff; padding: 10px 14px; border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,.1); }
                .row.sent .bubble { background: #D9FDD3; }
                .bubble.deleted { background: #FDECEC; border: 1px dashed #B71C1C; }
                .sender { font-size: 12px; font-weight: bold; color: #008069; margin-bottom: 4px; }
                .quote { background: rgba(0,0,0,.06); border-right: 3px solid #00A884; padding: 4px 8px; border-radius: 4px; font-size: 12px; margin-bottom: 6px; }
                .media-tag { font-size: 12px; background: rgba(0,0,0,.05); padding: 4px 8px; border-radius: 6px; margin-bottom: 6px; }
                .body { font-size: 14px; line-height: 1.5; }
                .meta { font-size: 11px; color: #667781; text-align: left; margin-top: 4px; }
              </style>
            </head>
            <body>
              <div class="hdr">
                <h1>${escapeHtml(chat.displayName)} (${escapeHtml(chat.phoneOrJid)})</h1>
                <p>عدد الرسائل: ${messages.size}</p>
              </div>
              <div class="wrap">$bubbles</div>
            </body>
            </html>
        """.trimIndent()

        context.contentResolver.openOutputStream(destUri)?.use { out ->
            out.write(html.toByteArray(Charsets.UTF_8))
        }
    }

    fun exportPdfToUri(context: Context, chat: WaChat, messages: List<WaMessage>, destUri: Uri) {
        val pdf = PdfDocument()
        val pageWidth = 595
        val pageHeight = 842
        val margin = 36
        val contentWidth = pageWidth - margin * 2

        val titlePaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#075E54")
            textSize = 15f
            isFakeBoldText = true
        }
        val bodyPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#111B21")
            textSize = 10.5f
        }
        val metaPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor("#667781")
            textSize = 8.5f
        }
        val sentBg = Paint().apply { color = Color.parseColor("#E7FFDB") }
        val recvBg = Paint().apply { color = Color.parseColor("#F4F4F4") }

        var pageNum = 1
        var page = pdf.startPage(PdfDocument.PageInfo.Builder(pageWidth, pageHeight, pageNum).create())
        var canvas = page.canvas
        var y = margin.toFloat()

        val titleLayout = StaticLayout.Builder
            .obtain("محادثة واتساب: ${chat.displayName} (${messages.size} رسالة)", 0, "محادثة واتساب: ${chat.displayName} (${messages.size} رسالة)".length, titlePaint, contentWidth)
            .setAlignment(Layout.Alignment.ALIGN_OPPOSITE)
            .build()
        canvas.save()
        canvas.translate(margin.toFloat(), y)
        titleLayout.draw(canvas)
        canvas.restore()
        y += titleLayout.height + 16f

        for (m in messages) {
            val fullText = buildString {
                append("${m.senderName}  |  ${m.timestampStr} ${if (m.fromMe) m.statusIcon else ""}")
                if (m.quotedText.isNotBlank()) {
                    append("\n[رد على ${m.quotedSender}]: ${m.quotedText}")
                }
                append("\n${m.text}")
                if (m.mediaRelPath.isNotBlank()) {
                    append("\n[مرفق]: ${m.mediaRelPath.substringAfterLast('/')}")
                }
            }
            val layout = StaticLayout.Builder
                .obtain(fullText, 0, fullText.length, bodyPaint, contentWidth - 16)
                .setAlignment(Layout.Alignment.ALIGN_OPPOSITE)
                .build()

            val blockH = layout.height + 14f
            if (y + blockH > pageHeight - margin) {
                pdf.finishPage(page)
                pageNum++
                page = pdf.startPage(PdfDocument.PageInfo.Builder(pageWidth, pageHeight, pageNum).create())
                canvas = page.canvas
                y = margin.toFloat()
            }

            canvas.drawRoundRect(
                margin.toFloat(),
                y,
                (pageWidth - margin).toFloat(),
                y + blockH,
                8f,
                8f,
                if (m.fromMe) sentBg else recvBg,
            )
            canvas.save()
            canvas.translate((margin + 8).toFloat(), y + 7f)
            layout.draw(canvas)
            canvas.restore()
            y += blockH + 8f
        }

        // تذييل الصفحة الأخيرة
        canvas.drawText("تم التصدير محليًا بدون إنترنت", margin.toFloat(), (pageHeight - 16).toFloat(), metaPaint)
        pdf.finishPage(page)

        context.contentResolver.openOutputStream(destUri)?.use { out ->
            pdf.writeTo(out)
        }
        pdf.close()
    }

    fun exportForensicsCsvToUri(context: Context, items: List<ForensicArtifactItem>, destUri: Uri) {
        context.contentResolver.openOutputStream(destUri)?.use { out ->
            out.write(byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte()))
            val sb = StringBuilder()
            sb.appendLine("المصدر,الصفحة/الإطار,الإزاحة,المعرف المرتبط,الثقة,النص المستخرج")
            for (art in items) {
                sb.appendLine(
                    listOf(
                        csvCell(art.source),
                        art.pageOrFrame.toString(),
                        "0x${art.offset.toString(16).uppercase()}",
                        csvCell(art.relatedJid),
                        csvCell(art.confidence),
                        csvCell(art.extractedText),
                    ).joinToString(",")
                )
            }
            out.write(sb.toString().toByteArray(Charsets.UTF_8))
        }
    }
}
