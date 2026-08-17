#!/usr/bin/env python3
"""InterGenOS GTK Installer - Main Application"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, GLib
import sys
import os

# Add screens to path
sys.path.insert(0, os.path.dirname(__file__))

from screens.welcome import WelcomeScreen
from screens.disk import DiskScreen
from screens.user import UserScreen
from screens.progress import ProgressScreen

class InterGenInstaller(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("InterGenOS Installer")
        self.set_default_size(900, 650)
        self.set_resizable(True)
        
        # Main stack for screens
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(300)
        self.set_child(self.stack)
        
        # Store user selections
        self.selections = {
            "archive_dir": "/path/to/archives",  # TODO: Set correct path
            "package_dir": "/path/to/packages",  # TODO: Set correct path
        }
        
        # Create screens
        self.welcome_screen = WelcomeScreen(self)
        self.disk_screen = DiskScreen(self)
        self.user_screen = UserScreen(self)
        self.progress_screen = ProgressScreen(self)
        
        self.stack.add_named(self.welcome_screen, "welcome")
        self.stack.add_named(self.disk_screen, "disk")
        self.stack.add_named(self.user_screen, "user")
        self.stack.add_named(self.progress_screen, "progress")
        
        self.stack.set_visible_child_name("welcome")
    
    def next_screen(self):
        """Navigate to the next screen."""
        current = self.stack.get_visible_child_name()
        
        if current == "welcome":
            self.selections["locale"] = self.welcome_screen.get_selected_locale()
            self.stack.set_visible_child_name("disk")
            
        elif current == "disk":
            disk = self.disk_screen.get_selected_disk()
            if disk:
                self.selections["disk"] = disk
                
                # Actually partition the disk using backend
                from backend.disks import partition_disk, is_efi
                self.selections["efi"] = is_efi()
                self.selections["partitions"] = partition_disk(disk, efi=is_efi())
                
                self.stack.set_visible_child_name("user")
            else:
                # Show error
                self.show_error("No disk selected", "Please select a disk to install InterGenOS.")
            
        elif current == "user":
            user_data = self.user_screen.get_user_data()
            self.selections.update(user_data)
            self.stack.set_visible_child_name("progress")
            self.progress_screen.start_installation(self.selections)
    
    def prev_screen(self):
        """Navigate to the previous screen."""
        current = self.stack.get_visible_child_name()
        
        if current == "disk":
            self.stack.set_visible_child_name("welcome")
        elif current == "user":
            self.stack.set_visible_child_name("disk")
    
    def show_error(self, title, message):
        """Display an error dialog."""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title
        )
        dialog.format_secondary_text(message)
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.show()

class InterGenApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.intergenos.installer")
    
    def do_activate(self):
        win = InterGenInstaller(self)
        win.present()

def main():
    app = InterGenApp()
    return app.run(sys.argv)

if __name__ == "__main__":
    sys.exit(main())