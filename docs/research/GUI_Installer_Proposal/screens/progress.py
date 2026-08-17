"""Installation progress screen with real-time updates."""

from gi.repository import Gtk, GLib
import threading
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))

class ProgressScreen(Gtk.Box):
    def __init__(self, installer):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.installer = installer
        self.installation_thread = None
        self.should_cancel = False
        
        self.set_margin_top(40)
        self.set_margin_bottom(40)
        self.set_margin_start(60)
        self.set_margin_end(60)
        
        # Title
        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold'>Installing InterGenOS</span>")
        title.get_style_context().add_class("screen-title")
        self.append(title)
        
        # Status label
        self.status_label = Gtk.Label(label="Preparing...")
        self.status_label.set_margin_top(20)
        self.status_label.get_style_context().add_class("screen-description")
        self.append(self.status_label)
        
        # Progress bar
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.get_style_context().add_class("progress-bar")
        self.progress_bar.set_margin_top(10)
        self.progress_bar.set_margin_bottom(10)
        self.append(self.progress_bar)
        
        # Detail log (scrolled window)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(200)
        
        self.log_textview = Gtk.TextView()
        self.log_textview.set_editable(False)
        self.log_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_textview.get_style_context().add_class("form-input")
        
        scrolled.set_child(self.log_textview)
        self.append(scrolled)
        
        # Cancel button
        self.cancel_btn = Gtk.Button(label="Cancel Installation")
        self.cancel_btn.get_style_context().add_class("button-secondary")
        self.cancel_btn.connect("clicked", self.on_cancel)
        self.append(self.cancel_btn)
        
        # Initially disable cancel until installation starts
        self.cancel_btn.set_sensitive(False)
    
    def start_installation(self, selections):
        """Start the installation in a background thread."""
        self.cancel_btn.set_sensitive(True)
        self.installation_thread = threading.Thread(
            target=self._run_installation,
            args=(selections,)
        )
        self.installation_thread.daemon = True
        self.installation_thread.start()
    
    def _run_installation(self, selections):
        """Run the actual installation using backend modules."""
        try:
            from disks import mount_target, unmount_target
            from packages import install_packages
            from config import generate_all
            from users import create_user, set_root_password, enable_services
            from bootloader import install_grub
            
            target = "/mnt/target"
            partitions = selections["partitions"]
            disk = selections["disk"]
            username = selections["username"]
            password = selections["password"]
            hostname = selections["hostname"]
            locale = selections.get("locale", "en_US.UTF-8")
            
            # Step 1: Create mount point
            self.update_status("Creating mount point...", 0.05)
            os.makedirs(target, exist_ok=True)
            
            # Step 2: Mount partitions
            self.update_status("Mounting partitions...", 0.1)
            mount_target(partitions, target)
            
            # Step 3: Install packages
            self.update_status("Installing core packages...", 0.2)
            archive_dir = selections.get("archive_dir", "/path/to/archives")
            package_dir = selections.get("package_dir", None)
            
            success, failed_count, failed = install_packages(
                target, archive_dir, ["core", "base"], package_dir,
                progress_callback=self.package_progress
            )
            
            if failed_count > 0:
                self.log_message(f"Warning: {failed_count} packages failed to install")
            
            # Step 4: Generate configuration
            self.update_status("Generating system configuration...", 0.5)
            generate_all(target, partitions, hostname, locale)
            
            # Step 5: Set up users
            self.update_status("Setting up user accounts...", 0.7)
            set_root_password(target, password)
            create_user(target, username, password)
            enable_services(target)
            
            # Step 6: Install bootloader
            self.update_status("Installing bootloader...", 0.9)
            install_grub(target, disk, partitions)
            
            # Step 7: Cleanup
            self.update_status("Finishing up...", 0.95)
            unmount_target(target)
            
            # Success
            GLib.idle_add(self.installation_finished, True, "")
            
        except Exception as e:
            error_msg = str(e)
            self.log_message(f"ERROR: {error_msg}")
            GLib.idle_add(self.installation_finished, False, error_msg)
    
    def update_status(self, message, fraction):
        """Update status label and progress bar from background thread."""
        GLib.idle_add(self._update_status_gui, message, fraction)
        time.sleep(0.1)  # Small delay for UI responsiveness
    
    def _update_status_gui(self, message, fraction):
        """GUI update for status."""
        self.status_label.set_text(message)
        self.progress_bar.set_fraction(fraction)
        self.log_message(message)
        return False
    
    def package_progress(self, current, total, name):
        """Callback for package installation progress."""
        fraction = 0.2 + (0.5 * (current / total))
        self.update_status(f"Installing packages: {name} ({current}/{total})", fraction)
        self.log_message(f"  → Installing {name}")
    
    def log_message(self, message):
        """Add a message to the log text view."""
        buffer = self.log_textview.get_buffer()
        end_iter = buffer.get_end_iter()
        buffer.insert(end_iter, f"{message}\n")
        
        # Auto-scroll to bottom
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        self.log_textview.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
    
    def on_cancel(self, button):
        """Handle cancellation request."""
        dialog = Gtk.MessageDialog(
            transient_for=self.get_root(),
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Cancel Installation?"
        )
        dialog.format_secondary_text(
            "Canceling the installation may leave your system in an incomplete state. "
            "Are you sure you want to cancel?"
        )
        
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.YES:
            self.should_cancel = True
            self.cancel_btn.set_sensitive(False)
            self.status_label.set_text("Canceling installation...")
    
    def installation_finished(self, success, error_msg):
        """Show completion dialog."""
        self.cancel_btn.set_sensitive(False)
        
        if success:
            dialog = Gtk.MessageDialog(
                transient_for=self.get_root(),
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text="Installation Complete!"
            )
            dialog.format_secondary_text(
                "InterGenOS has been successfully installed. "
                "You can now restart your computer and boot into your new system."
            )
            dialog.connect("response", lambda d, r: self.get_root().close())
            dialog.show()
        else:
            dialog = Gtk.MessageDialog(
                transient_for=self.get_root(),
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Installation Failed"
            )
            dialog.format_secondary_text(f"An error occurred during installation:\n\n{error_msg}")
            dialog.connect("response", lambda d, r: self.get_root().close())
            dialog.show()