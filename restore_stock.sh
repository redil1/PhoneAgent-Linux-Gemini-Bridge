#!/bin/bash
set -e

cd /Users/aziz/Desktop/FullBacup/PhoneAgent/scratch/mtkclient

echo "========================================================"
echo " Restoring Stock Boot & VBMeta via MTKClient"
echo "========================================================"
echo "Phone instructions:"
echo "1. Turn off the phone (hold Power button for 10 seconds)."
echo "2. Hold Volume Up + Volume Down and plug the USB cable in."
echo "========================================================"

sudo ~/miunlock_env/bin/python3 mtk.py w boot_a,boot_b,vbmeta_a,vbmeta_b \
  /Users/aziz/Desktop/phone-agent-linux/phone_backup/boot_a.img,/Users/aziz/Desktop/phone-agent-linux/phone_backup/boot_a.img,/Users/aziz/Desktop/phone-agent-linux/phone_backup/vbmeta_a.img,/Users/aziz/Desktop/phone-agent-linux/phone_backup/vbmeta_a.img

echo ""
echo "Stock partitions restored! Resetting device..."
sudo ~/miunlock_env/bin/python3 mtk.py reset
echo "Done! Phone will boot normally into HyperOS."
