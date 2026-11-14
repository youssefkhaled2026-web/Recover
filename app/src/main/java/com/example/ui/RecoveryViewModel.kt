package com.example.ui

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.export.ChatExportManager
import com.example.forensics.ForensicArtifactItem
import com.example.forensics.SqliteForensicScanner
import com.example.recovery.CarvedFileItem
import com.example.recovery.FileCarverEngine
import com.example.whatsapp.WhatsAppCryptoEngine
import com.example.whatsapp.WaChat
import com.example.whatsapp.WaMessage
import com.example.whatsapp.WhatsAppDatabaseReader
import java.io.File
import java.io.FileOutputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class RecoveryUiState(
    // حالة تبويب واتساب
    val backupUri: Uri? = null,
    val backupLabel: String = "",
    val keyFileUri: Uri? = null,
    val keyFileLabel: String = "",
    val hexKey64: String = "",
    val waDbUri: Uri? = null,
    val waDbLabel: String = "",
    val mediaTreeUri: Uri? = null,
    val mediaTreeLabel: String = "",
    val waStatusText: String = "اختر ملف msgstore.db.crypt14 أو crypt15 للبدء (قراءة فقط).",
    val waBusy: Boolean = false,
    val decryptedDbFile: File? = null,
    val chats: List<WaChat> = emptyList(),
    val chatSearch: String = "",
    val selectedChat: WaChat? = null,
    val messages: List<WaMessage> = emptyList(),
    val msgSearch: String = "",
    val dateFrom: String = "",
    val dateTo: String = "",

    // حالة تبويب File Carving
    val selectedCategories: Set<String> = setOf("صور", "فيديو", "مستندات", "صوتيات", "قواعد بيانات"),
    val carvingBusy: Boolean = false,
    val carvingStatus: String = "اختر صورة قرص (.img/.dd) أو مجلد USB/SD للفحص.",
    val carvedItems: List<CarvedFileItem> = emptyList(),
    val selectedCarved: CarvedFileItem? = null,
    val previewBitmap: Bitmap? = null,
    val previewHex: String = "",

    // حالة تبويب الفحص العميق SQLite
    val forensicBusy: Boolean = false,
    val walUri: Uri? = null,
    val walLabel: String = "",
    val customDbUri: Uri? = null,
    val customDbLabel: String = "",
    val forensicQuery: String = "",
    val forensicItems: List<ForensicArtifactItem> = emptyList(),
    val forensicStatus: String = "يفحص الصفحات الحرة (Freelist) والمساحات غير المخصصة وملفات WAL.",

    val toastMessage: String? = null,
)

class RecoveryViewModel(application: Application) : AndroidViewModel(application) {

    private val _uiState = MutableStateFlow(RecoveryUiState())
    val uiState: StateFlow<RecoveryUiState> = _uiState.asStateFlow()

    private var contactsMap: Map<String, String> = emptyMap()
    private var mediaIndex: Map<String, Uri> = emptyMap()
    private var carvingJob: Job? = null

    fun clearToast() {
        _uiState.update { it.copy(toastMessage = null) }
    }

    fun setBackupFile(uri: Uri, label: String) {
        _uiState.update { it.copy(backupUri = uri, backupLabel = label) }
    }

    fun setKeyFile(uri: Uri, label: String) {
        _uiState.update { it.copy(keyFileUri = uri, keyFileLabel = label) }
    }

    fun setHexKey64(value: String) {
        _uiState.update { it.copy(hexKey64 = value) }
    }

    fun setWaDbFile(uri: Uri, label: String) {
        _uiState.update { it.copy(waDbUri = uri, waDbLabel = label) }
    }

    fun setMediaFolder(uri: Uri, label: String) {
        _uiState.update { it.copy(mediaTreeUri = uri, mediaTreeLabel = label) }
    }

    fun updateChatSearch(q: String) {
        _uiState.update { it.copy(chatSearch = q) }
    }

    fun updateMessageFilters(search: String, from: String, to: String) {
        _uiState.update { it.copy(msgSearch = search, dateFrom = from, dateTo = to) }
    }

    fun decryptAndLoadWhatsApp() {
        val state = _uiState.value
        val backupUri = state.backupUri ?: run {
            _uiState.update { it.copy(toastMessage = "يرجى اختيار ملف النسخة الاحتياطية أولاً.") }
            return
        }

        viewModelScope.launch(Dispatchers.IO) {
            _uiState.update { it.copy(waBusy = true, waStatusText = "جاري فك التشفير وفهرسة البيانات...") }
            val ctx = getApplication<Application>()
            val outcome = WhatsAppCryptoEngine.decryptBackupUri(
                context = ctx,
                backupUri = backupUri,
                keyFileUri = state.keyFileUri,
                hexKey64 = state.hexKey64.trim().ifBlank { null },
            )

            if (!outcome.success || outcome.decryptedDbFile == null) {
                _uiState.update {
                    it.copy(
                        waBusy = false,
                        waStatusText = outcome.message,
                        toastMessage = outcome.message,
                    )
                }
                return@launch
            }

            try {
                contactsMap = WhatsAppDatabaseReader.loadContactsFromWaDb(ctx, state.waDbUri)
                mediaIndex = WhatsAppDatabaseReader.indexMediaTree(ctx, state.mediaTreeUri)
                val loadedChats = WhatsAppDatabaseReader.loadChats(outcome.decryptedDbFile, contactsMap)
                _uiState.update {
                    it.copy(
                        waBusy = false,
                        decryptedDbFile = outcome.decryptedDbFile,
                        chats = loadedChats,
                        selectedChat = null,
                        messages = emptyList(),
                        waStatusText = "${outcome.message} | المحادثات: ${loadedChats.size}",
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        waBusy = false,
                        waStatusText = "تعذر تحليل قاعدة البيانات: ${e.message}",
                        toastMessage = "خطأ: ${e.message}",
                    )
                }
            }
        }
    }

    fun selectChat(chat: WaChat) {
        val dbFile = _uiState.value.decryptedDbFile ?: return
        viewModelScope.launch(Dispatchers.IO) {
            val msgs = WhatsAppDatabaseReader.loadMessages(dbFile, chat, contactsMap, mediaIndex)
            _uiState.update { it.copy(selectedChat = chat, messages = msgs) }
        }
    }

    fun closeSelectedChat() {
        _uiState.update { it.copy(selectedChat = null, messages = emptyList()) }
    }

    fun filteredMessages(): List<WaMessage> {
        val s = _uiState.value
        val q = s.msgSearch.trim().lowercase()
        val dFrom = s.dateFrom.trim()
        val dTo = s.dateTo.trim()
        return s.messages.filter { m ->
            val matchQ = q.isBlank() ||
                m.text.lowercase().contains(q) ||
                m.senderName.lowercase().contains(q) ||
                m.quotedText.lowercase().contains(q)
            val matchFrom = dFrom.isBlank() || m.dateStr.isBlank() || m.dateStr >= dFrom
            val matchTo = dTo.isBlank() || m.dateStr.isBlank() || m.dateStr <= dTo
            matchQ && matchFrom && matchTo
        }
    }

    fun exportSelectedChat(format: String, destUri: Uri) {
        val chat = _uiState.value.selectedChat ?: return
        val msgs = filteredMessages()
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val ctx = getApplication<Application>()
                when (format) {
                    "html" -> ChatExportManager.exportHtmlToUri(ctx, chat, msgs, destUri)
                    "csv" -> ChatExportManager.exportCsvToUri(ctx, chat, msgs, destUri)
                    "pdf" -> ChatExportManager.exportPdfToUri(ctx, chat, msgs, destUri)
                }
                _uiState.update { it.copy(toastMessage = "تم تصدير المحادثة بصيغة ${format.uppercase()} بنجاح.") }
            } catch (e: Exception) {
                _uiState.update { it.copy(toastMessage = "فشل التصدير: ${e.message}") }
            }
        }
    }

    // --- File Carving ---
    fun toggleCategory(cat: String) {
        _uiState.update { s ->
            val next = s.selectedCategories.toMutableSet()
            if (!next.add(cat)) next.remove(cat)
            s.copy(selectedCategories = next)
        }
    }

    fun scanDiskImage(imageUri: Uri) {
        carvingJob?.cancel()
        carvingJob = viewModelScope.launch(Dispatchers.IO) {
            _uiState.update {
                it.copy(
                    carvingBusy = true,
                    carvedItems = emptyList(),
                    selectedCarved = null,
                    previewBitmap = null,
                    previewHex = "",
                    carvingStatus = "جاري فحص صورة القرص بوضع القراءة فقط...",
                )
            }
            val items = try {
                FileCarverEngine.carveDiskImage(
                    context = getApplication(),
                    imageUri = imageUri,
                    allowedCategories = _uiState.value.selectedCategories,
                ) { msg -> _uiState.update { it.copy(carvingStatus = msg) } }
            } catch (e: Exception) {
                _uiState.update { it.copy(toastMessage = "خطأ أثناء الفحص: ${e.message}") }
                emptyList()
            }
            _uiState.update { it.copy(carvingBusy = false, carvedItems = items) }
        }
    }

    fun scanSafFolder(treeUri: Uri) {
        carvingJob?.cancel()
        carvingJob = viewModelScope.launch(Dispatchers.IO) {
            _uiState.update {
                it.copy(
                    carvingBusy = true,
                    carvedItems = emptyList(),
                    selectedCarved = null,
                    previewBitmap = null,
                    previewHex = "",
                    carvingStatus = "جاري فحص المجلد المختار بوضع القراءة فقط...",
                )
            }
            val items = try {
                FileCarverEngine.scanDocumentTreeReadOnly(
                    context = getApplication(),
                    treeUri = treeUri,
                    allowedCategories = _uiState.value.selectedCategories,
                ) { msg -> _uiState.update { it.copy(carvingStatus = msg) } }
            } catch (e: Exception) {
                _uiState.update { it.copy(toastMessage = "خطأ أثناء فحص المجلد: ${e.message}") }
                emptyList()
            }
            _uiState.update { it.copy(carvingBusy = false, carvedItems = items) }
        }
    }

    fun stopCarving() {
        carvingJob?.cancel()
        _uiState.update { it.copy(carvingBusy = false, carvingStatus = "تم إيقاف الفحص بأمان.") }
    }

    fun selectCarvedItem(item: CarvedFileItem) {
        _uiState.update { it.copy(selectedCarved = item, previewBitmap = null, previewHex = "جاري التحميل...") }
        viewModelScope.launch(Dispatchers.IO) {
            val bytes = FileCarverEngine.readPreviewBytes(getApplication(), item)
            val bmp = if (item.category == "صور" && bytes.isNotEmpty()) {
                runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }.getOrNull()
            } else null
            val hex = bytes.take(64).joinToString(" ") { "%02X".format(it) }
            _uiState.update { it.copy(previewBitmap = bmp, previewHex = hex) }
        }
    }

    fun saveCarvedItem(item: CarvedFileItem, destUri: Uri) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                FileCarverEngine.saveRecoveredItemToUri(getApplication(), item, destUri)
                _uiState.update { it.copy(toastMessage = "تم حفظ الملف المسترجع بنجاح.") }
            } catch (e: Exception) {
                _uiState.update { it.copy(toastMessage = "فشل الحفظ: ${e.message}") }
            }
        }
    }

    // --- SQLite Deep Forensics ---
    fun setWalFile(uri: Uri, label: String) {
        _uiState.update { it.copy(walUri = uri, walLabel = label) }
    }

    fun setCustomForensicDb(uri: Uri, label: String) {
        _uiState.update { it.copy(customDbUri = uri, customDbLabel = label) }
    }

    fun updateForensicQuery(q: String) {
        _uiState.update { it.copy(forensicQuery = q) }
    }

    fun runDeepForensics() {
        val state = _uiState.value
        viewModelScope.launch(Dispatchers.IO) {
            _uiState.update { it.copy(forensicBusy = true, forensicStatus = "جاري فحص الصفحات الحرة وملفات WAL...") }
            val ctx = getApplication<Application>()
            var targetDb: File? = state.decryptedDbFile
            if (state.customDbUri != null) {
                val tmp = File(ctx.cacheDir, "forensic_custom_ro.db")
                ctx.contentResolver.openInputStream(state.customDbUri)?.use { input ->
                    FileOutputStream(tmp).use { out -> input.copyTo(out) }
                }
                if (tmp.exists()) targetDb = tmp
            }

            if (targetDb == null && state.walUri == null) {
                _uiState.update {
                    it.copy(
                        forensicBusy = false,
                        toastMessage = "فك تشفير نسخة واتساب أولاً أو اختر ملف .db / -wal للفحص.",
                    )
                }
                return@launch
            }

            val items = SqliteForensicScanner.scanReadOnly(ctx, targetDb, state.walUri)
            _uiState.update {
                it.copy(
                    forensicBusy = false,
                    forensicItems = items,
                    forensicStatus = "تم استخراج ${items.size} مقطعًا من المساحات المحذوفة و WAL.",
                )
            }
        }
    }

    fun exportForensicsCsv(destUri: Uri) {
        val items = _uiState.value.forensicItems
        viewModelScope.launch(Dispatchers.IO) {
            try {
                ChatExportManager.exportForensicsCsvToUri(getApplication(), items, destUri)
                _uiState.update { it.copy(toastMessage = "تم تصدير نتائج الفحص العميق إلى CSV.") }
            } catch (e: Exception) {
                _uiState.update { it.copy(toastMessage = "فشل التصدير: ${e.message}") }
            }
        }
    }
}
