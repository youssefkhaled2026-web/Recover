# -*- coding: utf-8 -*-
"""
نقطة الدخول الرئيسية لتشغيل برنامج استرجاع الملفات ومحادثات واتساب.
"""

import tkinter as tk
from gui_app import RecoverySuiteApp


def main() -> None:
    root = tk.Tk()
    RecoverySuiteApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
