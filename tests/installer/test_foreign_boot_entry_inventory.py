"""Count all retained foreign entries separately from the installed-OS set."""

from unittest import mock

from installer.backend import bootloader


INVENTORY = """BootOrder: 0000,0001,0002,0003,0004
Boot0000* InterGenOS\tHD(1,GPT,aaaa,0x800,0x200000)/File(\\EFI\\InterGenOS\\shimx64.efi)
Boot0001* Prior loader\tVenHw(aaaa)
Boot0002* Prior system\tVenHw(bbbb)
Boot0003* UEFI OS\tHD(1,GPT,aaaa,0x800,0x200000)/File(\\EFI\\BOOT\\BOOTX64.EFI)
Boot0004* USB device\tUSB(0,0)/HD(1,GPT,bbbb,0x800,0x200000)
"""


def test_record_distinguishes_all_foreign_entries_from_disk_os(tmp_path):
    _, entries = bootloader._parse_efibootmgr_output(INVENTORY)
    os_entries = bootloader._foreign_os_bootnums(entries, "InterGenOS")
    foreign = [b for b, (_, label, _) in entries.items() if label != "InterGenOS"]
    with mock.patch.object(bootloader.trace, "trace_event") as event:
        bootloader._write_boot_default_intent(tmp_path, True, os_entries, foreign)
    record = (tmp_path / bootloader.BOOT_DEFAULT_INTENT_REL).read_text()
    assert "foreign_os_entries_at_install=1\n" in record
    assert "foreign_boot_entries_at_install=4\n" in record
    assert event.call_args.kwargs["retained_foreign_bootnums"] == foreign


def test_cleanup_never_removes_foreign_entries():
    with mock.patch.object(bootloader.trace, "traced_run_chroot",
                           return_value=(0, INVENTORY, "")) as run, \
            mock.patch.object(bootloader.trace, "trace_event"):
        bootloader._cleanup_stale_efi_entries("/target", "InterGenOS")
    assert [c.args[1] for c in run.call_args_list] == ["efibootmgr", "efibootmgr -b 0000 -B"]


def test_unknown_inventory_is_not_reported_as_zero(tmp_path):
    with mock.patch.object(bootloader.trace, "trace_event") as event:
        bootloader._write_boot_default_intent(tmp_path, True, None)
    assert event.call_args.kwargs["foreign_boot_entries_at_install"] is None
    assert event.call_args.kwargs["foreign_os_entries_at_install"] is None
