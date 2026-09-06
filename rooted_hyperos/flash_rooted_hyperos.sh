#!/bin/bash
set -eo pipefail

SERIAL="rgr8r8zxmv9txgi7"
FASTBOOT="fastboot"
BASE_DIR="/Users/aziz/Desktop/phone-agent-linux"
STOCK_IMAGES="$BASE_DIR/stock_rom/earth_global_images_OS1.0.16.0.UCVMIXM_14.0/images"
MAGISK_BOOT="$BASE_DIR/stock_rom/magisk_patched_boot.img"

log() {
    echo -e "\033[1;34m[$(date '+%Y-%m-%d %H:%M:%S')]\033[0m $*"
}

log_ok() {
    echo -e "\033[1;32m[✓]\033[0m $*"
}

log_warn() {
    echo -e "\033[1;33m[!]\033[0m $*"
}

log_err() {
    echo -e "\033[1;31m[✗]\033[0m $*"
}

log "================================================================="
log "   FLASHING ROOTED STOCK HYPEROS (1.0.16.0) WITH NATIVE VOLTE   "
log "================================================================="

# Check input files
if [ ! -f "$MAGISK_BOOT" ]; then
    log_err "Magisk boot image not found: $MAGISK_BOOT"
    exit 1
fi

if [ ! -f "$STOCK_IMAGES/super.img" ]; then
    log_err "Stock super.img not found: $STOCK_IMAGES/super.img"
    exit 1
fi

log_ok "All required images verified."

# Check device connection
log "Checking device state..."
if "$FASTBOOT" -s "$SERIAL" devices 2>&1 | grep -q "$SERIAL"; then
    log_ok "Device is already in FASTBOOT mode."
elif adb -s "$SERIAL" devices 2>&1 | grep -q "$SERIAL"; then
    log "Device is currently running in Android. Rebooting to bootloader..."
    adb -s "$SERIAL" reboot bootloader
    log "Waiting for FASTBOOT mode..."
    for i in $(seq 1 30); do
        if "$FASTBOOT" -s "$SERIAL" devices 2>&1 | grep -q "$SERIAL"; then
            log_ok "Device detected in FASTBOOT mode."
            break
        fi
        sleep 1
    done
else
    log_warn "Device not found yet. Please connect phone in FASTBOOT mode (Vol Down + Power)."
    while ! "$FASTBOOT" -s "$SERIAL" devices 2>&1 | grep -q "$SERIAL"; do
        sleep 1
    done
    log_ok "Device detected in FASTBOOT mode."
fi

# Verify product and Kaeru unlock status
DEV_PRODUCT=$("$FASTBOOT" -s "$SERIAL" getvar product 2>&1 | grep "product:" | awk '{print $2}' || true)
if [ "$DEV_PRODUCT" != "earth" ]; then
    log_err "Device product is '$DEV_PRODUCT', expected 'earth'! Aborting."
    exit 1
fi
log_ok "Product verified: $DEV_PRODUCT"

IS_UNLOCKED=$("$FASTBOOT" -s "$SERIAL" getvar unlocked 2>&1 | grep "unlocked:" | awk '{print $2}' || true)
if [ "$IS_UNLOCKED" != "yes" ]; then
    log_err "Bootloader reports unlocked=$IS_UNLOCKED (expected 'yes'). Kaeru must be installed."
    exit 1
fi
log_ok "Bootloader is UNLOCKED: $IS_UNLOCKED"

KAERU_VER=$("$FASTBOOT" -s "$SERIAL" getvar kaeru-version 2>&1 | grep "kaeru-version:" | awk '{print $2}' || true)
log_ok "Kaeru LK Version: ${KAERU_VER:-active}"

# Flash Stock HyperOS Partitions
log "--- STEP 1/6: Flashing Stock HyperOS Firmware ---"
"$FASTBOOT" -s "$SERIAL" flash logo_a "$STOCK_IMAGES/logo.bin"
"$FASTBOOT" -s "$SERIAL" flash logo_b "$STOCK_IMAGES/logo.bin"
"$FASTBOOT" -s "$SERIAL" flash tee_a "$STOCK_IMAGES/tee.img"
"$FASTBOOT" -s "$SERIAL" flash tee_b "$STOCK_IMAGES/tee.img"
"$FASTBOOT" -s "$SERIAL" flash scp_a "$STOCK_IMAGES/scp.img"
"$FASTBOOT" -s "$SERIAL" flash scp_b "$STOCK_IMAGES/scp.img"
"$FASTBOOT" -s "$SERIAL" flash sspm_a "$STOCK_IMAGES/sspm.img"
"$FASTBOOT" -s "$SERIAL" flash sspm_b "$STOCK_IMAGES/sspm.img"
"$FASTBOOT" -s "$SERIAL" flash gz_a "$STOCK_IMAGES/gz.img"
"$FASTBOOT" -s "$SERIAL" flash gz_b "$STOCK_IMAGES/gz.img"
"$FASTBOOT" -s "$SERIAL" flash dtbo_a "$STOCK_IMAGES/dtbo.img"
"$FASTBOOT" -s "$SERIAL" flash dtbo_b "$STOCK_IMAGES/dtbo.img"
"$FASTBOOT" -s "$SERIAL" flash spmfw_a "$STOCK_IMAGES/spmfw.img"
"$FASTBOOT" -s "$SERIAL" flash spmfw_b "$STOCK_IMAGES/spmfw.img"
"$FASTBOOT" -s "$SERIAL" flash md1img_a "$STOCK_IMAGES/md1img.img"
"$FASTBOOT" -s "$SERIAL" flash md1img_b "$STOCK_IMAGES/md1img.img"
"$FASTBOOT" -s "$SERIAL" flash rescue "$STOCK_IMAGES/rescue.img"
log_ok "Firmware partitions flashed successfully."

# Flash VBMeta with disabled verification
log "--- STEP 2/6: Flashing VBMeta (Verification & Verity Disabled) ---"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_a "$STOCK_IMAGES/vbmeta.img"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_b "$STOCK_IMAGES/vbmeta.img"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_system_a "$STOCK_IMAGES/vbmeta_system.img"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_system_b "$STOCK_IMAGES/vbmeta_system.img"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_vendor_a "$STOCK_IMAGES/vbmeta_vendor.img"
"$FASTBOOT" -s "$SERIAL" --disable-verity --disable-verification flash vbmeta_vendor_b "$STOCK_IMAGES/vbmeta_vendor.img"
log_ok "VBMeta flashed with verification disabled."

# Flash Rooted Boot
log "--- STEP 3/6: Flashing Magisk Rooted Kernel to Boot Slots ---"
"$FASTBOOT" -s "$SERIAL" flash boot_a "$MAGISK_BOOT"
"$FASTBOOT" -s "$SERIAL" flash boot_b "$MAGISK_BOOT"
log_ok "Magisk boot flashed to boot_a and boot_b."

# Flash Cust & Super (Stock HyperOS System/Vendor/Product)
log "--- STEP 4/6: Flashing Stock HyperOS System (Cust & Super) ---"
"$FASTBOOT" -s "$SERIAL" flash cust "$STOCK_IMAGES/cust.img"
log "Flashing super.img (5.1 GB, contains HyperOS + VoLTE MediaTek IMS stack)... Please wait 1-2 minutes."
"$FASTBOOT" -s "$SERIAL" flash super "$STOCK_IMAGES/super.img"
log_ok "Stock HyperOS Super partition flashed successfully."

# Format Userdata & Metadata for clean first boot
log "--- STEP 5/6: Formatting Userdata & Metadata ---"
"$FASTBOOT" -s "$SERIAL" flash userdata "$STOCK_IMAGES/userdata.img"
"$FASTBOOT" -s "$SERIAL" erase metadata
"$FASTBOOT" -s "$SERIAL" erase md_udc
"$FASTBOOT" -s "$SERIAL" set_active a
"$FASTBOOT" -s "$SERIAL" oem cdms || true
log_ok "Clean userdata and metadata prepared."

# Reboot to Rooted Stock HyperOS
log "--- STEP 6/6: Rebooting into Rooted Stock HyperOS ---"
"$FASTBOOT" -s "$SERIAL" reboot

log "================================================================="
log_ok "FLASHING COMPLETED! The phone is now booting into Stock HyperOS."
log "First boot may take 2-4 minutes."
log "================================================================="
