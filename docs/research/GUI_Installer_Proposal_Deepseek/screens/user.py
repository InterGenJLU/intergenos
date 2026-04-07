"""User account creation screen."""

from gi.repository import Gtk
from .base import BrandedScreen

class UserScreen(BrandedScreen):
    def __init__(self, installer):
        super().__init__(
            installer,
            title="Create User Account",
            description="Create a user account for yourself. You'll use this to log in."
        )
        
        # Change next button text to "Install"
        self.set_next_button_text("Install")
        
        # Form container
        form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        form_box.set_margin_top(20)
        form_box.set_hexpand(False)
        form_box.set_halign(Gtk.Align.CENTER)
        form_box.set_width_request(400)
        
        # Name
        name_label = Gtk.Label()
        name_label.set_markup("<b>Your Name</b>")
        name_label.set_halign(Gtk.Align.START)
        form_box.append(name_label)
        
        self.name_entry = Gtk.Entry()
        self.name_entry.set_placeholder_text("e.g., John Doe")
        self.name_entry.set_hexpand(True)
        self.name_entry.connect("changed", self.on_input_changed)
        form_box.append(self.name_entry)
        
        # Username
        username_label = Gtk.Label()
        username_label.set_markup("<b>Username</b>")
        username_label.set_halign(Gtk.Align.START)
        username_label.set_margin_top(10)
        form_box.append(username_label)
        
        self.username_entry = Gtk.Entry()
        self.username_entry.set_placeholder_text("e.g., johndoe")
        self.username_entry.connect("changed", self.on_input_changed)
        form_box.append(self.username_entry)
        
        # Password
        password_label = Gtk.Label()
        password_label.set_markup("<b>Password</b>")
        password_label.set_halign(Gtk.Align.START)
        password_label.set_margin_top(10)
        form_box.append(password_label)
        
        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)
        self.password_entry.set_placeholder_text("Choose a strong password")
        self.password_entry.connect("changed", self.on_input_changed)
        form_box.append(self.password_entry)
        
        # Confirm password
        confirm_label = Gtk.Label()
        confirm_label.set_markup("<b>Confirm Password</b>")
        confirm_label.set_halign(Gtk.Align.START)
        confirm_label.set_margin_top(10)
        form_box.append(confirm_label)
        
        self.confirm_entry = Gtk.Entry()
        self.confirm_entry.set_visibility(False)
        self.confirm_entry.set_placeholder_text("Re-enter your password")
        self.confirm_entry.connect("changed", self.on_input_changed)
        form_box.append(self.confirm_entry)
        
        # Password strength indicator
        self.strength_label = Gtk.Label()
        self.strength_label.set_halign(Gtk.Align.START)
        self.strength_label.get_style_context().add_class("header-subtitle")
        form_box.append(self.strength_label)
        
        # Hostname
        hostname_label = Gtk.Label()
        hostname_label.set_markup("<b>Computer Name</b>")
        hostname_label.set_halign(Gtk.Align.START)
        hostname_label.set_margin_top(10)
        form_box.append(hostname_label)
        
        self.hostname_entry = Gtk.Entry()
        self.hostname_entry.set_placeholder_text("e.g., intergenos-pc")
        self.hostname_entry.set_text("intergenos-pc")
        self.hostname_entry.connect("changed", self.on_input_changed)
        form_box.append(self.hostname_entry)
        
        self.content_area.append(form_box)
        
        # Initially disable install button
        self.set_next_button_sensitive(False)
    
    def on_input_changed(self, widget):
        """Validate form inputs and enable/disable install button."""
        name = self.name_entry.get_text().strip()
        username = self.username_entry.get_text().strip()
        password = self.password_entry.get_text()
        confirm = self.confirm_entry.get_text()
        hostname = self.hostname_entry.get_text().strip()
        
        # Update password strength
        self.update_password_strength(password)
        
        # Check all fields are filled
        valid = all([name, username, password, confirm, hostname])
        
        # Check password match
        if valid and password != confirm:
            valid = False
            self.strength_label.set_markup("<span foreground='#ef4444'>✗ Passwords do not match</span>")
        elif valid and len(password) < 6:
            valid = False
            self.strength_label.set_markup("<span foreground='#f59e0b'>Password must be at least 6 characters</span>")
        
        self.set_next_button_sensitive(valid)
    
    def update_password_strength(self, password):
        """Show password strength indicator."""
        if not password:
            self.strength_label.set_text("")
            return
        
        strength = 0
        if len(password) >= 8:
            strength += 1
        if any(c.isupper() for c in password):
            strength += 1
        if any(c.isdigit() for c in password):
            strength += 1
        if any(c in "!@#$%^&*" for c in password):
            strength += 1
        
        if strength == 0:
            text = "Weak password"
            color = "#ef4444"
        elif strength <= 2:
            text = "Fair password"
            color = "#f59e0b"
        elif strength <= 3:
            text = "Good password"
            color = "#4ade80"
        else:
            text = "Strong password"
            color = "#22c55e"
        
        self.strength_label.set_markup(f"<span foreground='{color}'>Password strength: {text}</span>")
    
    def get_user_data(self):
        """Return user account data as a dict."""
        return {
            "fullname": self.name_entry.get_text().strip(),
            "username": self.username_entry.get_text().strip(),
            "password": self.password_entry.get_text(),
            "hostname": self.hostname_entry.get_text().strip()
        }