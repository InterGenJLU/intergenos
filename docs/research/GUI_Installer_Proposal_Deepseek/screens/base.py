"""Base screen class with consistent header, footer, and branding."""

from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import os

class BrandedScreen(Gtk.Box):
    """Base class for all installer screens - enforces consistent branding."""
    
    def __init__(self, installer, title="", description=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.installer = installer
        
        # Set CSS provider for this screen
        self._apply_css()
        
        # Header
        self.header = self._create_header()
        self.append(self.header)
        
        # Content area (to be filled by child classes)
        self.content_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.content_area.get_style_context().add_class("content-area")
        
        # Add title if provided
        if title:
            title_label = Gtk.Label()
            title_label.set_markup(f"<span size='x-large' weight='bold'>{title}</span>")
            title_label.get_style_context().add_class("screen-title")
            title_label.set_halign(Gtk.Align.START)
            self.content_area.append(title_label)
        
        if description:
            desc_label = Gtk.Label(label=description)
            desc_label.set_wrap(True)
            desc_label.set_halign(Gtk.Align.START)
            desc_label.get_style_context().add_class("screen-description")
            self.content_area.append(desc_label)
        
        self.append(self.content_area)
        
        # Footer with navigation buttons
        self.footer = self._create_footer()
        self.append(self.footer)
    
    def _apply_css(self):
        """Load and apply CSS for consistent theming."""
        css_provider = Gtk.CssProvider()
        css_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "intergenos.css")
        
        if os.path.exists(css_file):
            css_provider.load_from_path(css_file)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
    
    def _create_header(self):
        """Create branded header bar."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.get_style_context().add_class("header-bar")
        box.set_margin_start(0)
        box.set_margin_top(0)
        box.set_margin_end(0)
        
        # Try to load logo if exists
        logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png")
        if os.path.exists(logo_path):
            try:
                logo = Gtk.Image.new_from_file(logo_path)
                logo.set_pixel_size(32)
                box.append(logo)
            except:
                pass
        
        # Title
        title = Gtk.Label()
        title.set_markup("<span weight='bold' size='large'>InterGenOS</span>")
        title.get_style_context().add_class("header-title")
        box.append(title)
        
        # Spacer
        spacer = Gtk.Label()
        spacer.set_hexpand(True)
        box.append(spacer)
        
        # Version
        version = Gtk.Label(label="Installer v1.0")
        version.get_style_context().add_class("header-subtitle")
        box.append(version)
        
        return box
    
    def _create_footer(self):
        """Create footer with navigation buttons."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(40)
        box.set_margin_bottom(20)
        box.set_margin_end(40)
        box.set_margin_top(20)
        
        # Spacer to push buttons to the right
        spacer = Gtk.Label()
        spacer.set_hexpand(True)
        box.append(spacer)
        
        # Back button
        self.back_btn = Gtk.Button(label="Back")
        self.back_btn.get_style_context().add_class("button-secondary")
        self.back_btn.connect("clicked", lambda x: self.installer.prev_screen())
        box.append(self.back_btn)
        
        # Next/Install button
        self.next_btn = Gtk.Button(label="Continue")
        self.next_btn.get_style_context().add_class("button-primary")
        self.next_btn.connect("clicked", lambda x: self.installer.next_screen())
        box.append(self.next_btn)
        
        return box
    
    def set_next_button_text(self, text):
        """Change the next button text (e.g., 'Install' on final screen)."""
        self.next_btn.set_label(text)
    
    def set_back_button_sensitive(self, sensitive):
        """Enable/disable back button."""
        self.back_btn.set_sensitive(sensitive)
    
    def set_next_button_sensitive(self, sensitive):
        """Enable/disable next button."""
        self.next_btn.set_sensitive(sensitive)