"""Welcome screen with language selection and introduction."""

from gi.repository import Gtk
from .base import BrandedScreen

class WelcomeScreen(BrandedScreen):
    def __init__(self, installer):
        super().__init__(
            installer,
            title="Welcome to InterGenOS",
            description="This installer will guide you through setting up InterGenOS on your computer.\n\n"
                       "InterGenOS is a Linux distribution built from scratch with a focus on transparency, "
                       "modifiability, and trust. You understand it, you can modify it, and you can trust it."
        )
        
        # Disable back button on first screen
        self.set_back_button_sensitive(False)
        
        # Language selection
        lang_frame = Gtk.Frame()
        lang_frame.set_label("Select Language")
        lang_frame.set_margin_top(20)
        lang_frame.set_margin_bottom(20)
        
        lang_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        lang_box.set_margin_start(12)
        lang_box.set_margin_end(12)
        lang_box.set_margin_top(12)
        lang_box.set_margin_bottom(12)
        
        self.lang_combo = Gtk.ComboBoxText()
        languages = [
            ("en_US", "English (US)"),
            ("zh_CN", "简体中文"),
            ("es_ES", "Español"),
            ("de_DE", "Deutsch"),
            ("fr_FR", "Français"),
        ]
        for code, name in languages:
            self.lang_combo.append(code, name)
        self.lang_combo.set_active(0)
        
        lang_box.append(self.lang_combo)
        lang_frame.set_child(lang_box)
        self.content_area.append(lang_frame)
        
        # System requirements info
        req_frame = Gtk.Frame()
        req_frame.set_label("System Requirements")
        
        req_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        req_box.set_margin_start(12)
        req_box.set_margin_end(12)
        req_box.set_margin_top(12)
        req_box.set_margin_bottom(12)
        
        requirements = [
            "✓ 64-bit x86 processor",
            "✓ 4 GB RAM (8 GB recommended)",
            "✓ 20 GB free disk space",
            "✓ Internet connection (recommended)",
            "✓ UEFI or legacy BIOS support"
        ]
        
        for req in requirements:
            label = Gtk.Label(label=req, xalign=0)
            req_box.append(label)
        
        req_frame.set_child(req_box)
        self.content_area.append(req_frame)
    
    def get_selected_locale(self):
        """Return the selected locale code."""
        return self.lang_combo.get_active_id()