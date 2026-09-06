#!/bin/bash
set -e

cd /Users/aziz/Desktop/FullBacup/PhoneAgent/scratch/mtkclient

echo "========================================================"
echo " Flashing Magisk Boot & Disabled VBMeta via MTKClient"
echo "========================================================"
echo "Phone instructions:"
echo "1. Turn off the phone (hold Power button for 10 seconds)."
echo "2. Hold Volume Up + Volume Down and plug the USB cable in."
echo "========================================================"

sudo ~/miunlock_env/bin/python3 mtk.py w boot_a,boot_b,vbmeta_a,vbmeta_b \
  /Users/aziz/Desktop/phone-agent-linux/stock_rom/magisk_patched_boot.img,/Users/aziz/Desktop/phone-agent-linux/stock_rom/magisk_patched_boot.img,/Users/aziz/Desktop/phone-agent-linux/stock_rom/vbmeta_disabled.img,/Users/aziz/Desktop/phone-agent-linux/stock_rom/vbmeta_disabled.img

echo ""
echo "Partitions flashed successfully! Resetting device..."
sudo ~/miunlock_env/bin/python3 mtk.py reset
echo "Done! The phone is now booting into rooted HyperOS."
