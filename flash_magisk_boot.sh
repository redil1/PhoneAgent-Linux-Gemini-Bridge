#!/bin/bash
set -eo pipefail

SERIAL="rgr8r8zxmv9txgi7"
FASTBOOT="/opt/homebrew/bin/fastboot"
BASE_DIR="/Users/aziz/Desktop/phone-agent-linux"
STOCK_IMAGES="$BASE_DIR/stock_rom/earth_global_images_OS1.0.16.0.UCVMIXM_14.0/images"
MAGISK_BOOT="$BASE_DIR/stock_rom/magisk_patched_boot.img"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "=== FLASHING MAGISK PATCHED BOOT FOR REDMI 12C (earth) ==="
log "Serial: $SERIAL"
log "Magisk Boot Image: $MAGISK_BOOT"

if [ ! -f "$MAGISK_BOOT" ]; then
    log "ERROR: $MAGISK_BOOT not found!"
    exit 1
fi

log "Checking if device is in fastboot mode..."
if ! "$FASTBOOT" -s "$SERIAL" devices | grep -q "$SERIAL"; then
    log "Device is not in fastboot mode. Checking adb..."
    if adb -s "$SERIAL" devices | grep -q "$SERIAL"; then
        log "Rebooting device to bootloader via adb..."
        adb -s "$SERIAL" reboot bootloader
        log "Waiting for device in fastboot mode..."
        for i in $(seq 1 30); do
            if "$FASTBOOT" -s "$SERIAL" devices | grep -q "$SERIAL"; then
                log "Device detected in fastboot mode."
                break
            fi
            sleep 1
        done
    else
        log "ERROR: Device not found via adb or fastboot!"
        exit 1
    fi
fi

# Verify device and product
DEV_PRODUCT=$("$FASTBOOT" -s "$SERIAL" getvar product 2>&1 | grep -o 'earth' || true)
if [ "$DEV_PRODUCT" != "earth" ]; then
    log "ERROR: Device product is not earth! Aborting."
    exit 1
fi
log "Device verified: product=$DEV_PRODUCT"

IS_UNLOCKED=$("$FASTBOOT" -s "$SERIAL" getvar unlocked 2>&1 | grep -o 'yes' || true)
if [ "$IS_UNLOCKED" != "yes" ]; then
    log "ERROR: Bootloader is locked! Aborting."
    exit 1
fi
log "Bootloader is unlocked: $IS_UNLOCKED"

# Flash vbmeta with disable-verity and disable-verification to permit Magisk kernel
log "Flashing vbmeta with --disable-verity --disable-verification..."
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_a "$STOCK_IMAGES/vbmeta.img"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_b "$STOCK_IMAGES/vbmeta.img"

log "Flashing vbmeta_system with --disable-verity --disable-verification..."
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_system_a "$STOCK_IMAGES/vbmeta_system.img"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_system_b "$STOCK_IMAGES/vbmeta_system.img"

log "Flashing vbmeta_vendor with --disable-verity --disable-verification..."
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_vendor_a "$STOCK_IMAGES/vbmeta_vendor.img"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_vendor_b "$STOCK_IMAGES/vbmeta_vendor.img"

# Flash Magisk patched boot image to both slots
log "Flashing Magisk patched boot to boot_a..."
"$FASTBOOT" -s "$SERIAL" flash boot_a "$MAGISK_BOOT"

log "Flashing Magisk patched boot to boot_b..."
"$FASTBOOT" -s "$SERIAL" flash boot_b "$MAGISK_BOOT"

log "Setting active slot to a..."
"$FASTBOOT" -s "$SERIAL" set_active a

log "Clearing dm-verity state (oem cdms)..."
"$FASTBOOT" -s "$SERIAL" oem cdms || true

log "Rebooting device into rooted HyperOS..."
"$FASTBOOT" -s "$SERIAL" reboot
log "Reboot command sent. Waiting for Android to boot..."
