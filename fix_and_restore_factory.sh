#!/bin/bash
set -e

DIR="/Users/aziz/Desktop/backup12c"
cd "$DIR/scratch/mtkclient"

echo "================================================================"
echo " REDMI 12C - RESTORE STOCK FIRMWARE & FIX 'SYSTEM DESTROYED'"
echo "================================================================"
echo "This will:"
echo " 1. Flash verified stock boot_a, boot_b from backup12c"
echo " 2. Flash verified stock vbmeta_a, vbmeta_b from backup12c"
echo " 3. Relock seccfg to factory state"
echo " 4. Erase metadata & md_udc (clearing DM-verity corruption flags)"
echo " 5. Reset and reboot into clean Stock HyperOS"
echo "================================================================"
echo "Phone instructions:"
echo " 1. Disconnect USB cable."
echo " 2. Power off phone (hold Power button 10-15 seconds until screen is off)."
echo " 3. Hold [VOLUME UP + VOLUME DOWN] and connect the USB cable."
echo "================================================================"

sudo ~/miunlock_env/bin/python3 mtk.py multi \
  "w boot_a,boot_b,vbmeta_a,vbmeta_b $DIR/boot.img,$DIR/boot.img,$DIR/vbmeta.img,$DIR/vbmeta.img;da seccfg lock;e metadata,md_udc;reset"

echo ""
echo "================================================================"
echo "[✓] RESTORE & RELOCK COMPLETE! Phone is booting into HyperOS."
echo "================================================================"
