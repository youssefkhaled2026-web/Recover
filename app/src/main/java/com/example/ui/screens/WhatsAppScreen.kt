package com.example.ui.screens

import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.Contacts
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Forum
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.ui.RecoveryUiState
import com.example.ui.RecoveryViewModel
import com.example.ui.theme.DarkChatCanvas
import com.example.ui.theme.DarkDeletedBubble
import com.example.ui.theme.DarkRecvBubble
import com.example.ui.theme.DarkSentBubble
import com.example.ui.theme.DeletedBubbleLight
import com.example.ui.theme.DeletedTextLight
import com.example.ui.theme.EmeraldDeep
import com.example.ui.theme.MintSurface
import com.example.ui.theme.WarmSandCanvas
import com.example.whatsapp.WaChat
import com.example.whatsapp.WaMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
fun WhatsAppScreen(
    state: RecoveryUiState,
    viewModel: RecoveryViewModel,
) {
    val context = LocalContext.current

    fun resolveName(uri: Uri): String {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) return c.getString(0) ?: uri.lastPathSegment.orEmpty()
        }
        return uri.lastPathSegment ?: "ملف محدد"
    }

    val backupPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) viewModel.setBackupFile(uri, resolveName(uri))
    }
    val keyPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) viewModel.setKeyFile(uri, resolveName(uri))
    }
    val waDbPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) viewModel.setWaDbFile(uri, resolveName(uri))
    }
    val mediaPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) viewModel.setMediaFolder(uri, "مجلد Media محدد")
    }

    val exportHtml = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/html")) { uri ->
        if (uri != null) viewModel.exportSelectedChat("html", uri)
    }
    val exportCsv = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { uri ->
        if (uri != null) viewModel.exportSelectedChat("csv", uri)
    }
    val exportPdf = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/pdf")) { uri ->
        if (uri != null) viewModel.exportSelectedChat("pdf", uri)
    }

    val selectedChat = state.selectedChat
    if (selectedChat != null) {
        BackHandler { viewModel.closeSelectedChat() }
        ChatBubbleDetailView(
            chat = selectedChat,
            messages = viewModel.filteredMessages(),
            state = state,
            onBack = { viewModel.closeSelectedChat() },
            onFilterChange = { q, from, to -> viewModel.updateMessageFilters(q, from, to) },
            onExportHtml = { exportHtml.launch("${selectedChat.displayName}.html") },
            onExportPdf = { exportPdf.launch("${selectedChat.displayName}.pdf") },
            onExportCsv = { exportCsv.launch("${selectedChat.displayName}.csv") },
        )
        return
    }

    var showSetupCard by remember { mutableStateOf(true) }
    val filteredChats = remember(state.chats, state.chatSearch) {
        val q = state.chatSearch.trim().lowercase()
        state.chats.filter {
            q.isBlank() || it.displayName.lowercase().contains(q) || it.phoneOrJid.lowercase().contains(q)
        }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
                shape = RoundedCornerShape(16.dp),
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { showSetupCard = !showSetupCard },
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = "فك تشفير نسخة واتساب (crypt14 / crypt15)",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Text(
                            text = if (showSetupCard) "إخفاء" else "إظهار",
                            style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.secondary,
                        )
                    }

                    AnimatedVisibility(visible = showSetupCard) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Spacer(modifier = Modifier.height(6.dp))
                            OutlinedButton(
                                onClick = { backupPicker.launch(arrayOf("*/*")) },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .testTag("pick_wa_backup_button"),
                            ) {
                                Icon(Icons.Filled.AttachFile, contentDescription = null)
                                Spacer(Modifier.width(8.dp))
                                Text(
                                    text = state.backupLabel.ifBlank { "1. اختيار msgstore.db.crypt14 / crypt15 / db" },
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }

                            OutlinedTextField(
                                value = state.hexKey64,
                                onValueChange = { viewModel.setHexKey64(it) },
                                label = { Text("مفتاح الـ 64 خانة السداسي عشري (لنسخ crypt15)") },
                                placeholder = { Text("أدخل مفتاح التشفير الطرفي المكون من 64 حرفًا ورقمًا") },
                                singleLine = true,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .testTag("hex_key_input"),
                            )

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                OutlinedButton(
                                    onClick = { keyPicker.launch(arrayOf("*/*")) },
                                    modifier = Modifier
                                        .weight(1f)
                                        .testTag("pick_key_file_button"),
                                ) {
                                    Icon(Icons.Filled.Key, contentDescription = null, modifier = Modifier.size(18.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text(
                                        text = state.keyFileLabel.ifBlank { "ملف key (crypt14)" },
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                                OutlinedButton(
                                    onClick = { mediaPicker.launch(null) },
                                    modifier = Modifier
                                        .weight(1f)
                                        .testTag("pick_media_tree_button"),
                                ) {
                                    Icon(Icons.Filled.FolderOpen, contentDescription = null, modifier = Modifier.size(18.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text(
                                        text = state.mediaTreeLabel.ifBlank { "مجلد Media (اختياري)" },
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }

                            OutlinedButton(
                                onClick = { waDbPicker.launch(arrayOf("*/*")) },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .testTag("pick_wadb_button"),
                            ) {
                                Icon(Icons.Filled.Contacts, contentDescription = null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(6.dp))
                                Text(
                                    text = state.waDbLabel.ifBlank { "ملف جهات الاتصال wa.db (اختياري)" },
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }

                            Button(
                                onClick = { viewModel.decryptAndLoadWhatsApp() },
                                enabled = !state.waBusy,
                                colors = ButtonDefaults.buttonColors(containerColor = EmeraldDeep),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(48.dp)
                                    .testTag("decrypt_whatsapp_button"),
                            ) {
                                if (state.waBusy) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(20.dp),
                                        color = Color.White,
                                        strokeWidth = 2.dp,
                                    )
                                    Spacer(Modifier.width(8.dp))
                                } else {
                                    Icon(Icons.Filled.LockOpen, contentDescription = null)
                                    Spacer(Modifier.width(8.dp))
                                }
                                Text("فك التشفير وتحليل المحادثات")
                            }
                        }
                    }

                    Spacer(Modifier.height(6.dp))
                    Text(
                        text = state.waStatusText,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.secondary,
                    )
                }
            }
        }

        if (state.chats.isNotEmpty()) {
            item {
                OutlinedTextField(
                    value = state.chatSearch,
                    onValueChange = { viewModel.updateChatSearch(it) },
                    leadingIcon = { Icon(Icons.Filled.Search, contentDescription = "بحث") },
                    label = { Text("بحث في جهات الاتصال والمجموعات") },
                    singleLine = true,
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("search_chats_input"),
                )
            }

            items(filteredChats, key = { "${it.rowId}_${it.chatId}" }) { chat ->
                ChatListRow(chat = chat, onClick = { viewModel.selectChat(chat) })
            }
        } else if (!state.waBusy) {
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)),
                    shape = RoundedCornerShape(16.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Icon(
                            Icons.Filled.Forum,
                            contentDescription = null,
                            modifier = Modifier.size(48.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                        Spacer(Modifier.height(8.dp))
                        Text(
                            text = "لا توجد محادثات محمّلة بعد",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = "اختر نسخة msgstore.db.crypt14 أو crypt15 من مجلد WhatsApp/Databases وأدخل المفتاح لعرض الرسائل.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ChatListRow(chat: WaChat, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .testTag("chat_row_${chat.rowId}"),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(12.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(44.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primaryContainer),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = if (chat.isGroup) Icons.Filled.Group else Icons.Filled.Person,
                    contentDescription = chat.displayName,
                    tint = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = chat.displayName,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = chat.phoneOrJid,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
            Surface(
                color = MaterialTheme.colorScheme.primaryContainer,
                shape = RoundedCornerShape(20.dp),
            ) {
                Text(
                    text = "${chat.messageCount}",
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun ChatBubbleDetailView(
    chat: WaChat,
    messages: List<WaMessage>,
    state: RecoveryUiState,
    onBack: () -> Unit,
    onFilterChange: (String, String, String) -> Unit,
    onExportHtml: () -> Unit,
    onExportPdf: () -> Unit,
    onExportCsv: () -> Unit,
) {
    val isDark = isSystemInDarkTheme()
    val canvasColor = if (isDark) DarkChatCanvas else WarmSandCanvas
    var showFilterBar by remember { mutableStateOf(false) }

    Column(modifier = Modifier.fillMaxSize()) {
        Surface(color = EmeraldDeep, contentColor = Color.White) {
            Column {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 8.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(
                        onClick = onBack,
                        modifier = Modifier.testTag("chat_back_button"),
                    ) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "رجوع")
                    }
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = chat.displayName,
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            text = "${messages.size} رسالة",
                            style = MaterialTheme.typography.labelSmall,
                            color = Color.White.copy(alpha = 0.85f),
                        )
                    }
                    IconButton(onClick = { showFilterBar = !showFilterBar }, modifier = Modifier.testTag("toggle_filter_button")) {
                        Icon(Icons.Filled.Search, contentDescription = "بحث وفلترة")
                    }
                    OutlinedButton(
                        onClick = onExportHtml,
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White),
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                        modifier = Modifier.testTag("export_html_button"),
                    ) { Text("HTML", style = MaterialTheme.typography.labelSmall) }
                    Spacer(Modifier.width(4.dp))
                    OutlinedButton(
                        onClick = onExportPdf,
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White),
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                        modifier = Modifier.testTag("export_pdf_button"),
                    ) { Text("PDF", style = MaterialTheme.typography.labelSmall) }
                    Spacer(Modifier.width(4.dp))
                    OutlinedButton(
                        onClick = onExportCsv,
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White),
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                        modifier = Modifier.testTag("export_csv_button"),
                    ) { Text("CSV", style = MaterialTheme.typography.labelSmall) }
                }

                AnimatedVisibility(visible = showFilterBar) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(MaterialTheme.colorScheme.surface)
                            .padding(10.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        OutlinedTextField(
                            value = state.msgSearch,
                            onValueChange = { onFilterChange(it, state.dateFrom, state.dateTo) },
                            label = { Text("بحث في نص الرسائل") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedTextField(
                                value = state.dateFrom,
                                onValueChange = { onFilterChange(state.msgSearch, it, state.dateTo) },
                                label = { Text("من (YYYY-MM-DD)") },
                                singleLine = true,
                                modifier = Modifier.weight(1f),
                            )
                            OutlinedTextField(
                                value = state.dateTo,
                                onValueChange = { onFilterChange(state.msgSearch, state.dateFrom, it) },
                                label = { Text("إلى (YYYY-MM-DD)") },
                                singleLine = true,
                                modifier = Modifier.weight(1f),
                            )
                        }
                    }
                }
            }
        }

        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .background(canvasColor),
            contentPadding = PaddingValues(10.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            items(messages, key = { it.rowId }) { msg ->
                MessageBubbleCard(msg = msg, isDark = isDark)
            }
        }
    }
}

@Composable
private fun MessageBubbleCard(msg: WaMessage, isDark: Boolean) {
    val context = LocalContext.current
    val bubbleBg = when {
        msg.isDeleted -> if (isDark) DarkDeletedBubble else DeletedBubbleLight
        msg.fromMe -> if (isDark) DarkSentBubble else MintSurface
        else -> if (isDark) DarkRecvBubble else Color.White
    }
    val align = if (msg.fromMe) Alignment.End else Alignment.Start

    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = align,
    ) {
        Surface(
            color = bubbleBg,
            shape = RoundedCornerShape(
                topStart = 12.dp,
                topEnd = 12.dp,
                bottomStart = if (msg.fromMe) 12.dp else 2.dp,
                bottomEnd = if (msg.fromMe) 2.dp else 12.dp,
            ),
            shadowElevation = 1.dp,
            modifier = Modifier.widthIn(max = 320.dp),
        ) {
            Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 7.dp)) {
                Text(
                    text = if (msg.senderPhone.isNotBlank() && !msg.fromMe) {
                        "${msg.senderName} (${msg.senderPhone})"
                    } else msg.senderName,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.secondary,
                )

                if (msg.quotedText.isNotBlank()) {
                    Spacer(Modifier.height(4.dp))
                    Surface(
                        color = Color.Black.copy(alpha = 0.06f),
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(modifier = Modifier.padding(6.dp)) {
                            Text(
                                text = "↩ ${msg.quotedSender}",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                            )
                            Text(
                                text = msg.quotedText,
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }

                if (msg.mediaUri != null) {
                    Spacer(Modifier.height(6.dp))
                    val thumb by produceState<androidx.compose.ui.graphics.ImageBitmap?>(initialValue = null, msg.mediaUri) {
                        value = withContext(Dispatchers.IO) {
                            runCatching {
                                context.contentResolver.openInputStream(msg.mediaUri)?.use { stream ->
                                    val opts = BitmapFactory.Options().apply { inSampleSize = 2 }
                                    BitmapFactory.decodeStream(stream, null, opts)?.asImageBitmap()
                                }
                            }.getOrNull()
                        }
                    }
                    if (thumb != null) {
                        Image(
                            bitmap = thumb!!,
                            contentDescription = "صورة مرفقة",
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(160.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .clickable {
                                    val intent = Intent(Intent.ACTION_VIEW).apply {
                                        setDataAndType(msg.mediaUri, "*/*")
                                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                    }
                                    runCatching { context.startActivity(intent) }
                                },
                        )
                    } else {
                        OutlinedButton(
                            onClick = {
                                val intent = Intent(Intent.ACTION_VIEW).apply {
                                    setDataAndType(msg.mediaUri, "*/*")
                                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                }
                                runCatching { context.startActivity(intent) }
                            },
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                        ) {
                            Icon(Icons.Filled.Download, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text(
                                text = msg.mediaRelPath.substringAfterLast('/').ifBlank { "فتح الملف المرفق" },
                                style = MaterialTheme.typography.labelSmall,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                } else if (msg.mediaRelPath.isNotBlank()) {
                    Text(
                        text = "📎 ${msg.mediaRelPath.substringAfterLast('/')}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                if (msg.text.isNotBlank()) {
                    Spacer(Modifier.height(3.dp))
                    Text(
                        text = msg.text,
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (msg.isDeleted) DeletedTextLight else MaterialTheme.colorScheme.onSurface,
                    )
                }

                Spacer(Modifier.height(3.dp))
                val editedLabel = if (msg.isEdited) " (معدلة)" else ""
                Text(
                    text = "${msg.timestampStr}$editedLabel  ${if (msg.fromMe) msg.statusIcon else ""}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
