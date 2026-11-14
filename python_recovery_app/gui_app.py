# -*- coding: utf-8 -*-
"""
الواجهة الرسومية الرئيسية للبرنامج (Tkinter + ttk).
تضم ثلاثة أقسام:
1. استرجاع الملفات العامة (File Carving) مع معاينة وحفظ في مجلد آمن.
2. استرجاع وعرض محادثات واتساب (crypt14 / crypt15 / db) بشكل فقاعات مع ربط الوسائط والتصدير.
3. الفحص العميق لـ SQLite (Freelist & WAL) لمحاولة استرجاع السجلات المحذوفة.
"""

import io
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from exporter import ChatExporter
from file_carver import CarvedFile, FileCarver, list_available_drives
from sqlite_forensics import ForensicArtifact, SqliteForensics
from whatsapp_decryptor import WhatsAppDecryptor
from whatsapp_parser import ChatInfo, ChatMessage, WhatsAppParser

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _format_size(num_bytes: int) -> str:
    """تنسيق حجم الملف بوحدات مقروءة."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def _open_path_with_os(path: str) -> None:
    """فتح ملف وسائط بالبرنامج الافتراضي في نظام التشغيل."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class RecoverySuiteApp:
    """التطبيق الرئيسي لبرنامج استرجاع الملفات ومحادثات واتساب."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("أداة استرجاع الملفات ومحادثات واتساب الشخصية (وضع القراءة فقط)")
        self.root.geometry("1240x780")
        self.root.minsize(1024, 680)

        self._setup_styles()

        # محركات العمل
        self.carver = FileCarver()
        self.decryptor = WhatsAppDecryptor()
        self.forensics = SqliteForensics()
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()

        # حالة التبويب الأول (File Carving)
        self.carved_items: List[CarvedFile] = []
        self.carved_by_iid: Dict[str, CarvedFile] = {}
        self.preview_photo: Optional["ImageTk.PhotoImage"] = None
        self.scan_thread: Optional[threading.Thread] = None

        # حالة التبويب الثاني (WhatsApp)
        self.wa_parser: Optional[WhatsAppParser] = None
        self.wa_chats: List[ChatInfo] = []
        self.wa_filtered_chats: List[ChatInfo] = []
        self.current_chat: Optional[ChatInfo] = None
        self.current_messages: List[ChatMessage] = []
        self.decrypted_db_path: Optional[str] = None
        self.bubble_images: List["ImageTk.PhotoImage"] = []

        # حالة التبويب الثالث (SQLite Deep Forensics)
        self.forensic_artifacts: List[ForensicArtifact] = []

        self._build_ui()
        self.root.after(100, self._process_ui_queue)

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook.Tab", padding=[14, 7], font=("Tahoma", 10, "bold"))
        style.configure("Header.TLabel", font=("Tahoma", 12, "bold"), foreground="#008069")
        style.configure("Treeview", rowheight=26, font=("Tahoma", 9))
        style.configure("Treeview.Heading", font=("Tahoma", 9, "bold"))

    def _build_ui(self) -> None:
        # شريط علوي يوضح وضع الخصوصية والقراءة فقط
        banner = tk.Frame(self.root, bg="#075E54", height=42)
        banner.pack(fill=tk.X)
        tk.Label(
            banner,
            text="🔒 وضع القراءة فقط (Read-Only) | معالجة محلية 100% على جهازك بدون إنترنت",
            bg="#075E54",
            fg="#FFFFFF",
            font=("Tahoma", 10, "bold"),
            padx=14,
            pady=8,
        ).pack(side=tk.RIGHT)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tab_carve = ttk.Frame(self.notebook)
        self.tab_wa = ttk.Frame(self.notebook)
        self.tab_forensic = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_carve, text="1. استرجاع الملفات العامة (File Carving)")
        self.notebook.add(self.tab_wa, text="2. استرجاع محادثات واتساب")
        self.notebook.add(self.tab_forensic, text="3. فحص SQLite العميق (WAL & Freelist)")

        self._build_carving_tab()
        self._build_whatsapp_tab()
        self._build_forensics_tab()

    # =========================================================================
    # التبويب 1: استرجاع الملفات العامة
    # =========================================================================
    def _build_carving_tab(self) -> None:
        top = ttk.LabelFrame(self.tab_carve, text="مصدر الفحص (قرص / كارت ذاكرة / صورة قرص / مجلد USB)", padding=10)
        top.pack(fill=tk.X, padx=8, pady=6)

        row1 = ttk.Frame(top)
        row1.pack(fill=tk.X, pady=3)

        self.drive_combo_var = tk.StringVar()
        self.drive_combo = ttk.Combobox(row1, textvariable=self.drive_combo_var, width=48, state="readonly")
        self.drive_combo.pack(side=tk.RIGHT, padx=4)
        self._refresh_drives()

        ttk.Button(row1, text="تحديث الأقراص", command=self._refresh_drives).pack(side=tk.RIGHT, padx=4)
        ttk.Button(row1, text="اختيار صورة قرص (.img/.dd)", command=self._pick_disk_image).pack(side=tk.RIGHT, padx=4)
        ttk.Button(row1, text="اختيار مجلد (USB/Android)", command=self._pick_scan_folder).pack(side=tk.RIGHT, padx=4)

        row2 = ttk.Frame(top)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="المسار المحدد:").pack(side=tk.RIGHT, padx=4)
        self.scan_source_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.scan_source_var, width=65).pack(side=tk.RIGHT, padx=4, fill=tk.X, expand=True)

        # فلاتر الأنواع
        cats_frame = ttk.Frame(top)
        cats_frame.pack(fill=tk.X, pady=4)
        ttk.Label(cats_frame, text="أنواع الملفات المطلوبة:").pack(side=tk.RIGHT, padx=6)
        self.cat_vars: Dict[str, tk.BooleanVar] = {}
        for cat in ("صور", "فيديو", "مستندات", "صوتيات", "قواعد بيانات"):
            var = tk.BooleanVar(value=True)
            self.cat_vars[cat] = var
            ttk.Checkbutton(cats_frame, text=cat, variable=var).pack(side=tk.RIGHT, padx=8)

        # أزرار التحكم والتقدم
        ctrl = ttk.Frame(top)
        ctrl.pack(fill=tk.X, pady=4)
        self.btn_start_carve = ttk.Button(ctrl, text="▶ بدء الفحص (قراءة فقط)", command=self._start_carving)
        self.btn_start_carve.pack(side=tk.RIGHT, padx=4)
        self.btn_stop_carve = ttk.Button(ctrl, text="⏹ إيقاف", command=self._stop_carving, state=tk.DISABLED)
        self.btn_stop_carve.pack(side=tk.RIGHT, padx=4)
        ttk.Button(ctrl, text="💾 حفظ الملفات المحددة في مجلد آمن", command=self._recover_selected_carved).pack(side=tk.LEFT, padx=4)

        self.carve_progress = ttk.Progressbar(ctrl, mode="determinate", length=280)
        self.carve_progress.pack(side=tk.LEFT, padx=8)
        self.carve_status_var = tk.StringVar(value="جاهز للفحص.")
        ttk.Label(ctrl, textvariable=self.carve_status_var).pack(side=tk.LEFT, padx=6)

        # جدول النتائج + لوحة المعاينة
        paned = ttk.PanedWindow(self.tab_carve, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        table_frame = ttk.Frame(paned)
        cols = ("name", "category", "type", "size", "probability", "notes")
        self.carve_tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        headings = {
            "name": ("الاسم", 210),
            "category": ("التصنيف", 80),
            "type": ("النوع", 60),
            "size": ("الحجم", 90),
            "probability": ("احتمالية الاسترجاع", 130),
            "notes": ("ملاحظات الفحص", 220),
        }
        for k, (title, w) in headings.items():
            self.carve_tree.heading(k, text=title)
            self.carve_tree.column(k, width=w, anchor=tk.CENTER if k != "notes" else tk.E)

        sb_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.carve_tree.yview)
        self.carve_tree.configure(yscrollcommand=sb_y.set)
        self.carve_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.carve_tree.bind("<<TreeviewSelect>>", self._on_carve_select)

        preview_frame = ttk.LabelFrame(paned, text="معاينة الملف", padding=8)
        self.preview_image_label = ttk.Label(preview_frame, text="اختر ملفًا للمعاينة", anchor=tk.CENTER)
        self.preview_image_label.pack(fill=tk.BOTH, expand=True)
        self.preview_text = tk.Text(preview_frame, height=10, width=36, font=("Consolas", 9))
        self.preview_text.pack(fill=tk.X, pady=4)

        paned.add(table_frame, weight=3)
        paned.add(preview_frame, weight=1)

    def _refresh_drives(self) -> None:
        drives = list_available_drives()
        self.drives_map = {label: path for path, label in drives}
        values = [label for _, label in drives]
        self.drive_combo["values"] = values
        if values:
            self.drive_combo.current(0)
            self.drive_combo.bind("<<ComboboxSelected>>", self._on_drive_combo_pick)
            self.scan_source_var.set(self.drives_map[values[0]])

    def _on_drive_combo_pick(self, _event=None) -> None:
        lbl = self.drive_combo_var.get()
        if lbl in self.drives_map:
            self.scan_source_var.set(self.drives_map[lbl])

    def _pick_disk_image(self) -> None:
        path = filedialog.askopenfilename(
            title="اختر ملف صورة قرص",
            filetypes=[("Disk Images", "*.img *.dd *.raw *.bin"), ("All Files", "*.*")],
        )
        if path:
            self.scan_source_var.set(path)

    def _pick_scan_folder(self) -> None:
        path = filedialog.askdirectory(title="اختر مجلد الهاتف أو كارت الذاكرة")
        if path:
            self.scan_source_var.set(path)

    def _start_carving(self) -> None:
        source = self.scan_source_var.get().strip()
        if not source:
            messagebox.showwarning("تنبيه", "يرجى اختيار القرص أو المجلد أولاً.")
            return
        cats = {c for c, v in self.cat_vars.items() if v.get()}
        if not cats:
            messagebox.showwarning("تنبيه", "يرجى تحديد فئة ملفات واحدة على الأقل.")
            return

        self.carved_items.clear()
        self.carved_by_iid.clear()
        for item in self.carve_tree.get_children():
            self.carve_tree.delete(item)

        self.btn_start_carve.config(state=tk.DISABLED)
        self.btn_stop_carve.config(state=tk.NORMAL)
        self.carve_progress["value"] = 0
        self.carve_status_var.set("جاري الفحص بوضع القراءة فقط...")

        def worker():
            try:
                self.carver.scan(
                    source_path=source,
                    allowed_categories=cats,
                    progress_callback=lambda done, total, msg: self.ui_queue.put(("carve_prog", done, total, msg)),
                    found_callback=lambda item: self.ui_queue.put(("carve_item", item)),
                )
            except PermissionError:
                self.ui_queue.put((
                    "carve_err",
                    "رفض النظام الوصول المباشر للقرص الخام.\n"
                    "شغّل البرنامج كمسؤول (Run as Administrator) أو اختر صورة قرص (.img) أو مجلدًا.",
                ))
            except Exception as exc:
                self.ui_queue.put(("carve_err", str(exc)))
            finally:
                self.ui_queue.put(("carve_done",))

        self.scan_thread = threading.Thread(target=worker, daemon=True)
        self.scan_thread.start()

    def _stop_carving(self) -> None:
        self.carver.request_stop()
        self.carve_status_var.set("جاري إيقاف الفحص بأمان...")

    def _on_carve_select(self, _event=None) -> None:
        sel = self.carve_tree.selection()
        if not sel:
            return
        carved = self.carved_by_iid.get(sel[0])
        if not carved:
            return

        self.preview_text.delete("1.0", tk.END)
        self.preview_image_label.config(image="", text="جاري التحميل...")
        self.preview_photo = None

        try:
            sample = self.carver.read_carved_bytes(carved.source_path, carved.offset, carved.size, max_bytes=4 * 1024 * 1024)
            if HAS_PIL and carved.category == "صور":
                try:
                    img = Image.open(io.BytesIO(sample))
                    img.thumbnail((280, 280))
                    self.preview_photo = ImageTk.PhotoImage(img)
                    self.preview_image_label.config(image=self.preview_photo, text="")
                except Exception:
                    self.preview_image_label.config(image="", text="تعذر عرض الصورة (بيانات تالفة جزئيًا)")
            else:
                self.preview_image_label.config(
                    image="",
                    text=f"{carved.name}\nالنوع: {carved.ext.upper()}\nالحجم: {_format_size(carved.size)}",
                )

            hex_head = " ".join(f"{b:02X}" for b in sample[:96])
            self.preview_text.insert(
                tk.END,
                f"Offset: 0x{carved.offset:X}\nاحتمالية: {carved.probability} ({carved.probability_pct}%)\n"
                f"أول بايتات (Hex):\n{hex_head}",
            )
        except Exception as exc:
            self.preview_image_label.config(image="", text=f"خطأ في المعاينة: {exc}")

    def _recover_selected_carved(self) -> None:
        sel = self.carve_tree.selection()
        if not sel:
            messagebox.showinfo("تنبيه", "حدد ملفًا أو أكثر من الجدول للحفظ.")
            return
        dest_dir = filedialog.askdirectory(title="اختر مجلدًا آمنًا للحفظ (على قرص مختلف)")
        if not dest_dir:
            return

        saved = 0
        try:
            for iid in sel:
                carved = self.carved_by_iid.get(iid)
                if carved:
                    self.carver.recover_file(carved, dest_dir)
                    saved += 1
            messagebox.showinfo("تم الحفظ", f"تم استرجاع وحفظ {saved} ملف بنجاح في:\n{dest_dir}")
        except ValueError as safety_err:
            messagebox.showerror("تحذير أمان", str(safety_err))
        except Exception as exc:
            messagebox.showerror("خطأ", f"فشل الحفظ: {exc}")

    # =========================================================================
    # التبويب 2: استرجاع محادثات واتساب
    # =========================================================================
    def _build_whatsapp_tab(self) -> None:
        cfg = ttk.LabelFrame(self.tab_wa, text="إعدادات النسخة الاحتياطية والمفتاح", padding=8)
        cfg.pack(fill=tk.X, padx=8, pady=6)

        # الصف الأول: ملف النسخة + wa.db + مجلد Media
        r1 = ttk.Frame(cfg)
        r1.pack(fill=tk.X, pady=3)
        ttk.Label(r1, text="ملف النسخة (crypt14/crypt15/db):").pack(side=tk.RIGHT, padx=4)
        self.wa_backup_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.wa_backup_var, width=42).pack(side=tk.RIGHT, padx=4)
        ttk.Button(r1, text="استعراض...", command=self._pick_wa_backup).pack(side=tk.RIGHT, padx=4)

        ttk.Label(r1, text="مجلد WhatsApp/Media:").pack(side=tk.RIGHT, padx=(12, 4))
        self.wa_media_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.wa_media_var, width=32).pack(side=tk.RIGHT, padx=4)
        ttk.Button(r1, text="اختيار مجلد", command=self._pick_wa_media).pack(side=tk.RIGHT, padx=4)

        # الصف الثاني: طريقة فك التشفير (ملف key أو مفتاح 64 رقم) + wa.db اختياري
        r2 = ttk.Frame(cfg)
        r2.pack(fill=tk.X, pady=3)
        ttk.Label(r2, text="ملف المفتاح (key):").pack(side=tk.RIGHT, padx=4)
        self.wa_keyfile_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.wa_keyfile_var, width=30).pack(side=tk.RIGHT, padx=4)
        ttk.Button(r2, text="استعراض key", command=self._pick_wa_keyfile).pack(side=tk.RIGHT, padx=4)

        ttk.Label(r2, text="أو مفتاح الـ 64 رقم (crypt15):").pack(side=tk.RIGHT, padx=(10, 4))
        self.wa_hexkey_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.wa_hexkey_var, width=38).pack(side=tk.RIGHT, padx=4)

        # الصف الثالث: wa.db وزر التحميل
        r3 = ttk.Frame(cfg)
        r3.pack(fill=tk.X, pady=3)
        ttk.Label(r3, text="ملف جهات الاتصال wa.db (اختياري):").pack(side=tk.RIGHT, padx=4)
        self.wa_contacts_db_var = tk.StringVar()
        ttk.Entry(r3, textvariable=self.wa_contacts_db_var, width=36).pack(side=tk.RIGHT, padx=4)
        ttk.Button(r3, text="اختيار wa.db", command=self._pick_wa_contacts_db).pack(side=tk.RIGHT, padx=4)

        ttk.Button(r3, text="🔓 فك التشفير وتحليل المحادثات", command=self._load_whatsapp_backup).pack(side=tk.LEFT, padx=4)
        self.wa_status_var = tk.StringVar(value="بانتظار تحميل نسخة واتساب.")
        ttk.Label(r3, textvariable=self.wa_status_var, foreground="#008069").pack(side=tk.LEFT, padx=8)

        # المنطقة الرئيسية: قائمة المحادثات + عارض الفقاعات
        main_pane = ttk.PanedWindow(self.tab_wa, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # يمين: قائمة جهات الاتصال والمجموعات
        left_panel = ttk.Frame(main_pane)
        search_chat_row = ttk.Frame(left_panel)
        search_chat_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(search_chat_row, text="بحث محادثة:").pack(side=tk.RIGHT, padx=4)
        self.chat_filter_var = tk.StringVar()
        self.chat_filter_var.trace_add("write", lambda *_: self._filter_chat_list())
        ttk.Entry(search_chat_row, textvariable=self.chat_filter_var).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        self.chat_listbox = tk.Listbox(left_panel, font=("Tahoma", 10), width=34, activestyle="none")
        sb_chats = ttk.Scrollbar(left_panel, orient=tk.VERTICAL, command=self.chat_listbox.yview)
        self.chat_listbox.configure(yscrollcommand=sb_chats.set)
        self.chat_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_chats.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_listbox.bind("<<ListboxSelect>>", self._on_chat_selected)

        # يسار: أدوات الفلترة + التصدير + فقاعات الرسائل
        right_panel = ttk.Frame(main_pane)
        filter_bar = ttk.Frame(right_panel)
        filter_bar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(filter_bar, text="بحث بالرسائل:").pack(side=tk.RIGHT, padx=3)
        self.msg_search_var = tk.StringVar()
        ttk.Entry(filter_bar, textvariable=self.msg_search_var, width=20).pack(side=tk.RIGHT, padx=3)

        ttk.Label(filter_bar, text="من (YYYY-MM-DD):").pack(side=tk.RIGHT, padx=3)
        self.date_from_var = tk.StringVar()
        ttk.Entry(filter_bar, textvariable=self.date_from_var, width=11).pack(side=tk.RIGHT, padx=3)

        ttk.Label(filter_bar, text="إلى:").pack(side=tk.RIGHT, padx=3)
        self.date_to_var = tk.StringVar()
        ttk.Entry(filter_bar, textvariable=self.date_to_var, width=11).pack(side=tk.RIGHT, padx=3)

        ttk.Button(filter_bar, text="تطبيق", command=self._reload_current_chat_messages).pack(side=tk.RIGHT, padx=4)

        ttk.Button(filter_bar, text="CSV", command=lambda: self._export_chat("csv")).pack(side=tk.LEFT, padx=2)
        ttk.Button(filter_bar, text="PDF", command=lambda: self._export_chat("pdf")).pack(side=tk.LEFT, padx=2)
        ttk.Button(filter_bar, text="HTML", command=lambda: self._export_chat("html")).pack(side=tk.LEFT, padx=2)

        # لوحة Canvas قابلة للتمرير لعرض فقاعات المحادثة بشكل شبيه بواتساب
        canvas_wrap = ttk.Frame(right_panel)
        canvas_wrap.pack(fill=tk.BOTH, expand=True)

        self.chat_canvas = tk.Canvas(canvas_wrap, bg="#EFEAE2", highlightthickness=0)
        self.chat_scroll = ttk.Scrollbar(canvas_wrap, orient=tk.VERTICAL, command=self.chat_canvas.yview)
        self.chat_bubbles_frame = tk.Frame(self.chat_canvas, bg="#EFEAE2")

        self.chat_bubbles_frame.bind(
            "<Configure>",
            lambda e: self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all")),
        )
        self.chat_window_id = self.chat_canvas.create_window((0, 0), window=self.chat_bubbles_frame, anchor="nw")
        self.chat_canvas.bind(
            "<Configure>",
            lambda e: self.chat_canvas.itemconfig(self.chat_window_id, width=e.width),
        )
        self.chat_canvas.configure(yscrollcommand=self.chat_scroll.set)
        self.chat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        main_pane.add(left_panel, weight=1)
        main_pane.add(right_panel, weight=3)

    def _pick_wa_backup(self) -> None:
        path = filedialog.askopenfilename(
            title="اختر ملف نسخة واتساب الاحتياطية",
            filetypes=[("WhatsApp DB", "*.crypt14 *.crypt15 *.db"), ("All Files", "*.*")],
        )
        if path:
            self.wa_backup_var.set(path)

    def _pick_wa_media(self) -> None:
        path = filedialog.askdirectory(title="اختر مجلد WhatsApp/Media")
        if path:
            self.wa_media_var.set(path)

    def _pick_wa_keyfile(self) -> None:
        path = filedialog.askopenfilename(title="اختر ملف المفتاح (key أو encrypted_backup.key)")
        if path:
            self.wa_keyfile_var.set(path)

    def _pick_wa_contacts_db(self) -> None:
        path = filedialog.askopenfilename(
            title="اختر ملف wa.db (اختياري)",
            filetypes=[("SQLite DB", "*.db"), ("All Files", "*.*")],
        )
        if path:
            self.wa_contacts_db_var.set(path)

    def _load_whatsapp_backup(self) -> None:
        backup_path = self.wa_backup_var.get().strip()
        if not backup_path:
            messagebox.showwarning("تنبيه", "يرجى تحديد ملف msgstore.db.crypt14 أو crypt15 أو msgstore.db.")
            return

        key_path = self.wa_keyfile_var.get().strip() or None
        hex_key = self.wa_hexkey_var.get().strip() or None
        media_dir = self.wa_media_var.get().strip() or None
        wa_db = self.wa_contacts_db_var.get().strip() or None

        self.wa_status_var.set("جاري فك التشفير وقراءة القاعدة...")
        self.root.update_idletasks()

        res = self.decryptor.decrypt_backup(backup_path, key_file_path=key_path, hex_key_64=hex_key)
        if not res.success or not res.output_db_path:
            self.wa_status_var.set("فشل فك التشفير.")
            messagebox.showerror("خطأ في فك التشفير", res.message)
            return

        self.decrypted_db_path = res.output_db_path
        # تعبئة مسار الفحص العميق تلقائيًا لتسهيل العمل
        self.forensic_db_var.set(res.output_db_path)

        try:
            self.wa_parser = WhatsAppParser(
                msgstore_db_path=res.output_db_path,
                wa_db_path=wa_db,
                media_root_dir=media_dir,
            )
            self.wa_chats = self.wa_parser.load_chats()
            self._filter_chat_list()
            self.wa_status_var.set(f"{res.message} | عدد المحادثات: {len(self.wa_chats)}")
        except Exception as exc:
            self.wa_status_var.set("خطأ أثناء تحليل قاعدة البيانات.")
            messagebox.showerror("خطأ", f"تعذر تحليل قاعدة البيانات: {exc}")

    def _filter_chat_list(self) -> None:
        q = self.chat_filter_var.get().strip().lower()
        self.chat_listbox.delete(0, tk.END)
        self.wa_filtered_chats = [
            c for c in self.wa_chats
            if not q or q in c.display_name.lower() or q in c.phone_or_jid.lower()
        ]
        for c in self.wa_filtered_chats:
            prefix = "👥 " if c.is_group else "👤 "
            self.chat_listbox.insert(tk.END, f"{prefix}{c.display_name} ({c.message_count})")

    def _on_chat_selected(self, _event=None) -> None:
        sel = self.chat_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.wa_filtered_chats):
            self.current_chat = self.wa_filtered_chats[idx]
            self._reload_current_chat_messages()

    def _reload_current_chat_messages(self) -> None:
        if not self.wa_parser or not self.current_chat:
            return
        msgs = self.wa_parser.load_messages(
            self.current_chat,
            search_text=self.msg_search_var.get(),
            date_from=self.date_from_var.get(),
            date_to=self.date_to_var.get(),
        )
        self.current_messages = msgs
        self._render_chat_bubbles(msgs)

    def _render_chat_bubbles(self, messages: List[ChatMessage]) -> None:
        for child in self.chat_bubbles_frame.winfo_children():
            child.destroy()
        self.bubble_images.clear()

        if not messages:
            tk.Label(
                self.chat_bubbles_frame,
                text="لا توجد رسائل مطابقة للفلتر المحدد.",
                bg="#EFEAE2",
                fg="#555",
                font=("Tahoma", 11),
                pady=24,
            ).pack()
            return

        # عرض أحدث 400 رسالة لتسريع العرض عند المحادثات الضخمة مع إعلام المستخدم
        display_list = messages[-400:]
        if len(messages) > 400:
            tk.Label(
                self.chat_bubbles_frame,
                text=f"يتم عرض آخر 400 رسالة من أصل {len(messages)} رسالة (التصدير يشمل الجميع).",
                bg="#FFF3CD",
                fg="#856404",
                font=("Tahoma", 9),
                pady=4,
            ).pack(fill=tk.X, padx=12, pady=4)

        for m in display_list:
            row = tk.Frame(self.chat_bubbles_frame, bg="#EFEAE2")
            row.pack(fill=tk.X, padx=12, pady=4)

            bg_color = "#D9FDD3" if m.from_me else "#FFFFFF"
            if m.is_deleted:
                bg_color = "#FDECEC"

            anchor_side = tk.RIGHT if m.from_me else tk.LEFT
            bubble = tk.Frame(
                row,
                bg=bg_color,
                bd=1,
                relief=tk.SOLID,
                padx=10,
                pady=6,
            )
            bubble.pack(side=anchor_side, anchor="e" if m.from_me else "w")

            # اسم المرسل
            tk.Label(
                bubble,
                text=f"{m.sender_name} {f'({m.sender_phone})' if m.sender_phone and not m.from_me else ''}".strip(),
                bg=bg_color,
                fg="#008069",
                font=("Tahoma", 9, "bold"),
                anchor="e",
                justify=tk.RIGHT,
            ).pack(fill=tk.X)

            # اقتباس الرد إن وجد
            if m.quoted_text:
                q_frame = tk.Frame(bubble, bg="#E8F5E9" if m.from_me else "#F0F2F5", padx=6, pady=3)
                q_frame.pack(fill=tk.X, pady=(3, 4))
                tk.Label(
                    q_frame,
                    text=f"↩ {m.quoted_sender}: {m.quoted_text[:120]}",
                    bg=q_frame["bg"],
                    fg="#3B4A54",
                    font=("Tahoma", 8),
                    wraplength=420,
                    justify=tk.RIGHT,
                ).pack(anchor="e")

            # معاينة الوسائط أو رابط فتحها
            if m.media_full_path and os.path.isfile(m.media_full_path):
                ext = os.path.splitext(m.media_full_path)[1].lower()
                if HAS_PIL and ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                    try:
                        img = Image.open(m.media_full_path)
                        img.thumbnail((220, 220))
                        photo = ImageTk.PhotoImage(img)
                        self.bubble_images.append(photo)
                        lbl_img = tk.Label(bubble, image=photo, bg=bg_color, cursor="hand2")
                        lbl_img.pack(pady=4)
                        lbl_img.bind("<Button-1>", lambda _e, p=m.media_full_path: _open_path_with_os(p))
                    except Exception:
                        pass
                btn_media = tk.Button(
                    bubble,
                    text=f"📎 فتح الملف: {os.path.basename(m.media_full_path)}",
                    command=lambda p=m.media_full_path: _open_path_with_os(p),
                    bg="#E9EDEF",
                    relief=tk.FLAT,
                    font=("Tahoma", 8),
                    cursor="hand2",
                )
                btn_media.pack(fill=tk.X, pady=3)
            elif m.media_rel_path:
                tk.Label(
                    bubble,
                    text=f"📎 مرفق غير موجود محليًا: {os.path.basename(m.media_rel_path)}",
                    bg=bg_color,
                    fg="#888",
                    font=("Tahoma", 8),
                ).pack(anchor="e")

            # نص الرسالة
            tk.Label(
                bubble,
                text=m.text,
                bg=bg_color,
                fg="#B71C1C" if m.is_deleted else "#111B21",
                font=("Tahoma", 10),
                wraplength=460,
                justify=tk.RIGHT,
                anchor="e",
            ).pack(fill=tk.X, pady=(2, 2))

            # الوقت وحالة القراءة
            edited_tag = " (معدلة)" if m.is_edited else ""
            meta_txt = f"{m.timestamp_str}{edited_tag}  {m.status_icon if m.from_me else ''}"
            tk.Label(
                bubble,
                text=meta_txt,
                bg=bg_color,
                fg="#667781",
                font=("Tahoma", 8),
                anchor="w",
            ).pack(fill=tk.X)

        self.chat_canvas.yview_moveto(1.0)

    def _export_chat(self, fmt: str) -> None:
        if not self.current_chat or not self.current_messages:
            messagebox.showwarning("تنبيه", "اختر محادثة تحتوي على رسائل أولاً.")
            return
        ext_map = {"html": ".html", "pdf": ".pdf", "csv": ".csv"}
        out_path = filedialog.asksaveasfilename(
            title=f"تصدير المحادثة بصيغة {fmt.upper()}",
            defaultextension=ext_map[fmt],
            filetypes=[(fmt.upper(), f"*{ext_map[fmt]}")],
        )
        if not out_path:
            return
        try:
            if fmt == "html":
                ChatExporter.export_html(self.current_chat, self.current_messages, out_path)
            elif fmt == "csv":
                ChatExporter.export_csv(self.current_chat, self.current_messages, out_path)
            elif fmt == "pdf":
                ChatExporter.export_pdf(self.current_chat, self.current_messages, out_path)
            messagebox.showinfo("نجاح التصدير", f"تم تصدير المحادثة بنجاح إلى:\n{out_path}")
        except Exception as exc:
            messagebox.showerror("خطأ في التصدير", str(exc))

    # =========================================================================
    # التبويب 3: الفحص العميق لـ SQLite (Freelist & WAL)
    # =========================================================================
    def _build_forensics_tab(self) -> None:
        top = ttk.LabelFrame(self.tab_forensic, text="فحص السجلات المحذوفة داخل صفحات SQLite الحرة وملفات WAL", padding=10)
        top.pack(fill=tk.X, padx=8, pady=6)

        r1 = ttk.Frame(top)
        r1.pack(fill=tk.X, pady=3)
        ttk.Label(r1, text="ملف قاعدة البيانات (msgstore.db مفكوك التشفير):").pack(side=tk.RIGHT, padx=4)
        self.forensic_db_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.forensic_db_var, width=48).pack(side=tk.RIGHT, padx=4)
        ttk.Button(r1, text="اختيار .db", command=self._pick_forensic_db).pack(side=tk.RIGHT, padx=4)

        r2 = ttk.Frame(top)
        r2.pack(fill=tk.X, pady=3)
        ttk.Label(r2, text="ملف WAL (اختياري msgstore.db-wal):").pack(side=tk.RIGHT, padx=4)
        self.forensic_wal_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.forensic_wal_var, width=48).pack(side=tk.RIGHT, padx=4)
        ttk.Button(r2, text="اختيار -wal", command=self._pick_forensic_wal).pack(side=tk.RIGHT, padx=4)

        ttk.Button(r2, text="🔍 بدء الفحص العميق", command=self._run_sqlite_forensics).pack(side=tk.LEFT, padx=4)
        ttk.Button(r2, text="تصدير CSV", command=self._export_forensics_csv).pack(side=tk.LEFT, padx=4)

        cols = ("source", "page", "offset", "jid", "conf", "text")
        self.forensic_tree = ttk.Treeview(self.tab_forensic, columns=cols, show="headings")
        for k, (title, w) in {
            "source": ("المصدر", 210),
            "page": ("الصفحة/الإطار", 95),
            "offset": ("الإزاحة", 95),
            "jid": ("معرف/رقم", 160),
            "conf": ("الثقة", 75),
            "text": ("النص المستخرج من المساحات المحذوفة", 480),
        }.items():
            self.forensic_tree.heading(k, text=title)
            self.forensic_tree.column(k, width=w, anchor=tk.E if k == "text" else tk.CENTER)

        sb = ttk.Scrollbar(self.tab_forensic, orient=tk.VERTICAL, command=self.forensic_tree.yview)
        self.forensic_tree.configure(yscrollcommand=sb.set)
        self.forensic_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=6)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8), pady=6)

    def _pick_forensic_db(self) -> None:
        path = filedialog.askopenfilename(title="اختر ملف SQLite DB", filetypes=[("SQLite", "*.db"), ("All", "*.*")])
        if path:
            self.forensic_db_var.set(path)

    def _pick_forensic_wal(self) -> None:
        path = filedialog.askopenfilename(title="اختر ملف WAL", filetypes=[("SQLite WAL", "*-wal *.wal"), ("All", "*.*")])
        if path:
            self.forensic_wal_var.set(path)

    def _run_sqlite_forensics(self) -> None:
        db_path = self.forensic_db_var.get().strip()
        wal_path = self.forensic_wal_var.get().strip() or None
        if not db_path and not wal_path:
            messagebox.showwarning("تنبيه", "يرجى اختيار ملف .db أو -wal أولاً.")
            return

        for row in self.forensic_tree.get_children():
            self.forensic_tree.delete(row)

        try:
            self.forensic_artifacts = self.forensics.scan_database_deep(db_path, wal_path)
            for art in self.forensic_artifacts:
                self.forensic_tree.insert(
                    "",
                    tk.END,
                    values=(
                        art.source,
                        art.page_or_frame,
                        hex(art.offset),
                        art.related_jid or "-",
                        art.confidence,
                        art.extracted_text[:300],
                    ),
                )
            messagebox.showinfo(
                "انتهى الفحص العميق",
                f"تم العثور على {len(self.forensic_artifacts)} مقطعًا نصيًا في الصفحات الحرة وملفات WAL.",
            )
        except Exception as exc:
            messagebox.showerror("خطأ", f"فشل الفحص العميق: {exc}")

    def _export_forensics_csv(self) -> None:
        if not self.forensic_artifacts:
            messagebox.showinfo("تنبيه", "لا توجد نتائج لتصديرها.")
            return
        out = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if out:
            ChatExporter.export_forensics_csv(self.forensic_artifacts, out)
            messagebox.showinfo("تم", f"تم التصدير إلى:\n{out}")

    # =========================================================================
    # معالج رسائل الخيوط الخلفية إلى الواجهة
    # =========================================================================
    def _process_ui_queue(self) -> None:
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "carve_item":
                    item: CarvedFile = msg[1]
                    self.carved_items.append(item)
                    iid = self.carve_tree.insert(
                        "",
                        tk.END,
                        values=(
                            item.name,
                            item.category,
                            item.ext.upper(),
                            _format_size(item.size),
                            f"{item.probability} ({item.probability_pct}%)",
                            item.notes,
                        ),
                    )
                    self.carved_by_iid[iid] = item
                elif kind == "carve_prog":
                    done, total, text = msg[1], msg[2], msg[3]
                    self.carve_status_var.set(text)
                    if total and total > 0:
                        self.carve_progress["value"] = min(100, (done / total) * 100)
                elif kind == "carve_err":
                    messagebox.showerror("خطأ أثناء الفحص", msg[1])
                elif kind == "carve_done":
                    self.btn_start_carve.config(state=tk.NORMAL)
                    self.btn_stop_carve.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._process_ui_queue)
