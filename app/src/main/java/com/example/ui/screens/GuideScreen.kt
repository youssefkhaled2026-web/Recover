package com.example.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

@Composable
fun GuideScreen() {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            GuideSectionCard(
                icon = Icons.Filled.Lock,
                title = "الأمان والخصوصية (وضع القراءة فقط)",
                body = "• يعمل التطبيق محليًا 100% بدون أي إذن إنترنت أو رفع بيانات.\n" +
                    "• يقرأ ملفات النسخ الاحتياطية وصور الأقراص والمجلدات عبر إطار الوصول الآمن (SAF) دون تعديل الملفات الأصلية.\n" +
                    "• توجد أيضًا نسخة سطح مكتب بلغة Python (Tkinter) في مجلد python_recovery_app/ داخل المشروع لتشغيلها على الكمبيوتر.",
            )
        }
        item {
            GuideSectionCard(
                icon = Icons.Filled.Key,
                title = "كيف تحصل على مفتاح التشفير من هاتفك؟",
                body = "1) لنسخ crypt15 (الطريقة الرسمية بدون Root):\n" +
                    "• افتح واتساب ← الإعدادات ← الدردشات ← النسخ الاحتياطي للدردشات.\n" +
                    "• ادخل إلى «النسخ الاحتياطي المشفر تمامًا بين الطرفين» واختر «استخدام مفتاح تشفير مكون من 64 رقمًا».\n" +
                    "• احتفظ بالمفتاح السداسي عشري وأدخله في تبويب واتساب.\n\n" +
                    "2) لنسخ crypt14:\n" +
                    "• يُحفظ ملف المفتاح key في المسار المحمي /data/data/com.whatsapp/files/key (يتطلب صلاحيات Root على جهازك الشخصي، بينما يمنع أندرويد العادي الوصول إلى /data/data لحماية الخصوصية).",
            )
        }
        item {
            GuideSectionCard(
                icon = Icons.Filled.Security,
                title = "مسارات النسخ الاحتياطية على أندرويد",
                body = "• أندرويد 11 وما فوق:\n  Android/media/com.whatsapp/WhatsApp/Databases/\n  Android/media/com.whatsapp/WhatsApp/Media/\n\n" +
                    "• النسخ الأقدم:\n  WhatsApp/Databases/ و WhatsApp/Media/",
            )
        }
        item {
            GuideSectionCard(
                icon = Icons.Filled.WarningAmber,
                title = "حدود الاسترجاع (متى يكون مستحيلًا؟)",
                body = "• الكتابة فوق القطاعات: إذا حُفظت بيانات جديدة مكان الملفات المحذوفة، تضيع نهائيًا.\n" +
                    "• أمر TRIM وذاكرة UFS/SSD: وحدات التخزين الحديثة تمحو الكتل المحذوفة فيزيائيًا فورًا.\n" +
                    "• التشفير القائم على الملفات (Android FBE): منذ أندرويد 7+ يُتلف مفتاح كل ملف عند حذفه، لذا يتعذر استرجاع القطاعات الخام من الذاكرة الداخلية بدون نسخة احتياطية.\n" +
                    "• SQLite VACUUM و secure_delete: إذا أعاد واتساب ضغط القاعدة أو مسح الصفحات الحرة، تختفي بقايا الرسائل من Freelist.\n" +
                    "• فقدان المفتاح: يستحيل رياضيًا فك تشفير AES-256-GCM دون ملف key أو مفتاح الـ 64 رقمًا المطابق للنسخة.",
            )
        }
    }
}

@Composable
private fun GuideSectionCard(
    icon: ImageVector,
    title: String,
    body: String,
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(14.dp),
        elevation = CardDefaults.cardElevation(2.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(8.dp))
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            Text(
                text = body,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}
