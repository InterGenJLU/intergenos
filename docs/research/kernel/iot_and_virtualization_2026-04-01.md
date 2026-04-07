# IoT, Smart Home, and Virtualization Kernel Support

**Date:** April 1, 2026
**Context:** Ensuring InterGenOS kernel covers IoT and virtualization use cases

---

## Smart Home Protocol Support

| Protocol | Kernel Support | Implementation | Kernel Config Needed |
|----------|---------------|----------------|---------------------|
| Zigbee | Yes (IEEE 802.15.4) | 6LoWPAN + userspace (zigbee2mqtt) | IEEE802154, 6LOWPAN, MAC802154 |
| Z-Wave | No | Userspace only (libopenzwave) | USB_SERIAL_CP210X only |
| Matter/Thread | No | Userspace (OpenThread/Matter SDK) | None — WiFi/BT sufficient |
| BLE/Bluetooth | Yes | Standard BT stack | Already in 25-bluetooth.config |

## USB Serial Adapters (Critical for IoT)

Almost all IoT USB devices use one of these chips:
- FTDI FT232 — CONFIG_USB_SERIAL_FTDI_SIO
- Silicon Labs CP210x — CONFIG_USB_SERIAL_CP210X
- WinChipHead CH340/341 — CONFIG_USB_SERIAL_CH341
- Prolific PL2303 — CONFIG_USB_SERIAL_PL2303
- USB CDC ACM (Arduino) — CONFIG_USB_ACM

## Sensor Bus Interfaces

- I2C: CONFIG_I2C + CONFIG_I2C_CHARDEV (temperature, humidity, displays)
- SPI: CONFIG_SPI + CONFIG_SPI_SPIDEV (high-speed sensors, LoRa modules)
- GPIO: CONFIG_GPIOLIB + CONFIG_GPIO_CHARDEV (relays, LEDs, buttons)
- 1-Wire: CONFIG_W1 + CONFIG_W1_THERM (DS18B20 temperature sensors)
- CAN Bus: CONFIG_CAN + CONFIG_CAN_RAW (automotive/industrial)

## Virtualization (Host + Guest)

### Guest (40-kvm-guest.config)
- Virtio drivers: BLK, NET, SCSI, console, balloon, input, FS, GPU
- Paravirt guest support

### Host (41-kvm-host.config)
- KVM core: Intel VT-x + AMD-V
- VFIO: GPU/device passthrough
- IOMMU: Device isolation (Intel VT-d, AMD-Vi)
- TUN/TAP, bridge, macvlan: VM networking
- vhost: High-performance virtio hosting
- Hugepages: KVM memory performance
- AMD SEV: Encrypted VM support

## Container Support (for Home Assistant, Docker, Podman)

- OverlayFS (image layers)
- Bridge + VXLAN + VETH (container networking)
- Cgroups v2 + namespaces (already in systemd fragment)
- Netfilter conntrack + NAT masquerade

## Home Assistant Requirements

- Cgroups v2 (required)
- USB serial drivers (for Zigbee/Z-Wave sticks)
- AppArmor (recommended for Supervisor)
- OverlayFS (for HAOS)
- All covered by 10-systemd.config + 50-iot.config

## Sources

- Linux IEEE 802.15.4 docs: docs.kernel.org/networking/ieee802154.html
- SocketCAN docs: docs.kernel.org/networking/can.html
- Home Assistant installation docs: home-assistant.io/installation/linux/
- Linux NFC subsystem: docs.kernel.org/networking/nfc.html
