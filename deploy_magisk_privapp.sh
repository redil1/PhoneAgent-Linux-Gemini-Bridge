#!/bin/bash
set -euo pipefail

SERIAL="rgr8r8zxmv9txgi7"
BASE_DIR="/Users/aziz/Desktop/phone-agent-linux"
APK="$BASE_DIR/android_service_apk/PhoneAgentGateway.apk"
PERMS="$BASE_DIR/android_service_apk/privapp-permissions-com.phoneagent.gateway.xml"

echo "[*] Waiting for device..."
adb -s "$SERIAL" wait-for-device

echo "[*] Checking root access..."
ROOT_CHECK=$(adb -s "$SERIAL" shell "su -c id" 2>/dev/null || true)
if ! echo "$ROOT_CHECK" | grep -q "uid=0"; then
    echo "[x] Root access not available yet. Please complete Phase 1 & 2 first."
    exit 1
fi
echo "[✓] Root confirmed: $ROOT_CHECK"

echo "[*] Removing any user-space copy from /data/app..."
adb -s "$SERIAL" uninstall com.phoneagent.gateway >/dev/null 2>&1 || true

echo "[*] Creating Magisk module directories..."
adb -s "$SERIAL" shell "su -c '
mkdir -p /data/adb/modules/phoneagent/system/priv-app/PhoneAgentGateway
mkdir -p /data/adb/modules/phoneagent/system/etc/permissions

cat << \"MODULE_PROP\" > /data/adb/modules/phoneagent/module.prop
id=phoneagent
name=PhoneAgent Gateway Priv-App
version=1.0
versionCode=1
author=PhoneAgent
description=Privileged Telephony Gateway for AI digital audio bridge
MODULE_PROP

touch /data/adb/modules/phoneagent/auto_mount
'"

echo "[*] Pushing PhoneAgentGateway.apk and permission allowlist..."
adb -s "$SERIAL" push "$APK" /data/local/tmp/PhoneAgentGateway.apk
adb -s "$SERIAL" push "$PERMS" /data/local/tmp/privapp-permissions-com.phoneagent.gateway.xml

adb -s "$SERIAL" shell "su -c '
cp /data/local/tmp/PhoneAgentGateway.apk /data/adb/modules/phoneagent/system/priv-app/PhoneAgentGateway/PhoneAgentGateway.apk
cp /data/local/tmp/privapp-permissions-com.phoneagent.gateway.xml /data/adb/modules/phoneagent/system/etc/permissions/privapp-permissions-com.phoneagent.gateway.xml
chmod 644 /data/adb/modules/phoneagent/system/priv-app/PhoneAgentGateway/PhoneAgentGateway.apk
chmod 644 /data/adb/modules/phoneagent/system/etc/permissions/privapp-permissions-com.phoneagent.gateway.xml
chown -R root:root /data/adb/modules/phoneagent
rm -f /data/local/tmp/PhoneAgentGateway.apk /data/local/tmp/privapp-permissions-com.phoneagent.gateway.xml
'"

echo "[*] Rebooting phone to activate Magisk Priv-App overlay..."
adb -s "$SERIAL" reboot
echo "[*] Waiting for Android to boot..."
adb -s "$SERIAL" wait-for-device
while [ "$(adb -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]; do
    sleep 2
done

echo "[*] Checking privileged package installation..."
for i in $(seq 1 30); do
    if adb -s "$SERIAL" shell "pm list packages" | grep -q "com.phoneagent.gateway"; then
        break
    fi
    sleep 1
done

echo "[*] Setting default dialer and granting runtime permissions..."
adb -s "$SERIAL" shell telecom set-default-dialer com.phoneagent.gateway || true
for perm in \
    android.permission.RECORD_AUDIO \
    android.permission.CALL_PHONE \
    android.permission.READ_PHONE_STATE \
    android.permission.READ_CALL_LOG \
    android.permission.WRITE_CALL_LOG \
    android.permission.POST_NOTIFICATIONS; do
    adb -s "$SERIAL" shell pm grant com.phoneagent.gateway "$perm" 2>/dev/null || true
done

echo "[*] Verifying privileged permissions..."
PACKAGE_DUMP=$(adb -s "$SERIAL" shell dumpsys package com.phoneagent.gateway)
for perm in MODIFY_PHONE_STATE MODIFY_AUDIO_ROUTING CAPTURE_AUDIO_OUTPUT; do
    if echo "$PACKAGE_DUMP" | grep -E "android.permission.$perm: granted=true" >/dev/null; then
        echo "[✓] android.permission.$perm: GRANTED"
    else
        echo "[!] android.permission.$perm: NOT YET GRANTED"
    fi
done

echo "=== DEPLOYMENT COMPLETE! ==="
