"""Disk selection and partitioning screen."""

from gi.repository import Gtk
from .base import BrandedScreen
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))

from disks import detect_disks, partition_disk, is_efi

class DiskScreen(BrandedScreen):
    def __init__(self, installer):
        super().__init__(
            installer,
            title="Installation Type",
            description="Choose the disk where you want to install InterGenOS. "
                       "All data on the selected disk will be erased."
        )
        
        self.selected_disk = None
        self.disk_buttons = []
        
        # Warning card
        warning_box = Gtk.Box()
        warning_box.get_style_context().add_class("warning-card")
        warning_label = Gtk.Label()
        warning_label.set_markup("<b>⚠ Warning</b>\nAll data on the selected disk will be permanently erased.")
        warning_label.set_wrap(True)
        warning_box.append(warning_label)
        self.content_area.append(warning_box)
        
        # Disk list
        disk_frame = Gtk.Frame()
        disk_frame.set_label("Available Disks")
        disk_frame.set_margin_top(10)
        disk_frame.set_margin_bottom(10)
        
        self.disk_list_box = Gtk.ListBox()
        self.disk_list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.disk_list_box.connect("row-selected", self.on_disk_selected)
        
        # Populate disks
        self.refresh_disks()
        
        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh Disks")
        refresh_btn.get_style_context().add_class("button-secondary")
        refresh_btn.connect("clicked", self.refresh_disks)
        
        disk_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        disk_vbox.append(self.disk_list_box)
        disk_vbox.append(refresh_btn)
        
        disk_frame.set_child(disk_vbox)
        self.content_area.append(disk_frame)
        
        # Boot mode info
        boot_mode = "EFI" if is_efi() else "BIOS/Legacy"
        boot_label = Gtk.Label()
        boot_label.set_markup(f"<b>Boot mode detected:</b> {boot_mode}")
        boot_label.set_halign(Gtk.Align.START)
        boot_label.set_margin_top(10)
        self.content_area.append(boot_label)
        
        # Initially disable next button until disk selected
        self.set_next_button_sensitive(False)
    
    def refresh_disks(self, *args):
        """Refresh the disk list."""
        # Clear existing rows
        while self.disk_list_box.get_row_at_index(0):
            self.disk_list_box.remove(self.disk_list_box.get_row_at_index(0))
        
        disks = detect_disks()
        
        if not disks:
            # No disks found
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label="No disks found. Please check your hardware.")
            row.set_child(label)
            self.disk_list_box.append(row)
            self.set_next_button_sensitive(False)
            return
        
        for disk in disks:
            row = Gtk.ListBoxRow()
            row.disk_path = disk.path
            
            # Create disk info box
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            
            # Disk name and size
            title = Gtk.Label()
            title.set_markup(f"<b>{disk.path}</b>  ({disk.size_human})")
            title.set_halign(Gtk.Align.START)
            box.append(title)
            
            # Model
            model_label = Gtk.Label(label=f"Model: {disk.model}")
            model_label.set_halign(Gtk.Align.START)
            model_label.get_style_context().add_class("header-subtitle")
            box.append(model_label)
            
            # Partitions info
            if disk.partitions:
                parts_text = f"Partitions: {len(disk.partitions)}"
                parts_label = Gtk.Label(label=parts_text)
                parts_label.set_halign(Gtk.Align.START)
                parts_label.get_style_context().add_class("header-subtitle")
                box.append(parts_label)
            
            row.set_child(box)
            self.disk_list_box.append(row)
    
    def on_disk_selected(self, listbox, row):
        """Handle disk selection."""
        if row and hasattr(row, 'disk_path'):
            self.selected_disk = row.disk_path
            self.set_next_button_sensitive(True)
    
    def get_selected_disk(self):
        """Return the selected disk path."""
        return self.selected_disk