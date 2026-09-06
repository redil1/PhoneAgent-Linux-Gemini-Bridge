#!/usr/bin/env python3
"""
test_voice.py
=============
Quickly preview and audition the hyper-realistic human neural voices.

Usage:
  python3 test_voice.py                     # Preview default voice (Ava)
  python3 test_voice.py --voice Andrew      # Preview Andrew (male)
  python3 test_voice.py --voice Brian       # Preview Brian (British male)
  python3 test_voice.py --list              # List all available human voices
  python3 test_voice.py --text "Custom phrase to speak"
"""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile

try:
    import edge_tts
except ImportError:
    print("Please install edge-tts: pip install edge-tts")
    sys.exit(1)

VOICES = {
    # US English
    "Ava": {
        "id": "en-US-AvaMultilingualNeural",
        "gender": "Female",
        "desc": "Warm, expressive, natural conversational tone (Top recommendation)",
    },
    "Andrew": {
        "id": "en-US-AndrewMultilingualNeural",
        "gender": "Male",
        "desc": "Calm, pleasant, natural conversational male",
    },
    "Jenny": {
        "id": "en-US-JennyNeural",
        "gender": "Female",
        "desc": "Energetic, clear, friendly, and articulate",
    },
    "Guy": {
        "id": "en-US-GuyNeural",
        "gender": "Male",
        "desc": "Relaxed, casual conversational American male",
    },
    "Aria": {
        "id": "en-US-AriaNeural",
        "gender": "Female",
        "desc": "Dynamic, expressive, versatile female",
    },
    "Christopher": {
        "id": "en-US-ChristopherNeural",
        "gender": "Male",
        "desc": "Authoritative, deep, news/explainer style",
    },
    "Eric": {
        "id": "en-US-EricNeural",
        "gender": "Male",
        "desc": "Positive, friendly corporate narrator",
    },
    "Michelle": {
        "id": "en-US-MichelleNeural",
        "gender": "Female",
        "desc": "Soft, calm, empathetic narration",
    },
    # UK English
    "Brian": {
        "id": "en-US-BrianMultilingualNeural",
        "gender": "Male",
        "desc": "Natural British English, refined and articulate",
    },
    "Sonia": {
        "id": "en-GB-SoniaNeural",
        "gender": "Female",
        "desc": "Polite, warm British English female",
    },
    "Ryan": {
        "id": "en-GB-RyanNeural",
        "gender": "Male",
        "desc": "Modern, conversational British English male",
    },
    # Multilingual & International
    "Vivienne": {
        "id": "fr-FR-VivienneMultilingualNeural",
        "gender": "Female",
        "desc": "French, expressive and fluent",
    },
    "Remy": {
        "id": "fr-FR-RemyMultilingualNeural",
        "gender": "Male",
        "desc": "French, natural conversational male",
    },
    "Hamed": {
        "id": "ar-SA-HamedNeural",
        "gender": "Male",
        "desc": "Arabic (Saudi Arabia), natural male",
    },
    "Zariyah": {
        "id": "ar-SA-ZariyahNeural",
        "gender": "Female",
        "desc": "Arabic (Saudi Arabia), natural female",
    },
}


async def speak(text: str, voice_name: str, rate: str = "+0%", pitch: str = "+0Hz"):
    voice_info = VOICES.get(voice_name.capitalize())
    if not voice_info:
        # Check if user passed full identifier
        voice_id = voice_name
    else:
        voice_id = voice_info["id"]

    print(f"Synthesizing with \033[1;32m{voice_name}\033[0m ({voice_id})...")
    tmp = tempfile.mktemp(suffix=".mp3")
    comm = edge_tts.Communicate(text, voice_id, rate=rate, pitch=pitch)
    await comm.save(tmp)

    print("Playing audio via system speakers...")
    subprocess.run(["afplay", tmp])
    try:
        os.remove(tmp)
    except Exception:
        pass


def list_voices():
    print("\n\033[1;36m=== Curated Hyper-Realistic Human Neural Voices ===\033[0m\n")
    print(f"{'Name':15s} | {'Gender':8s} | {'Description':50s} | Full Voice ID")
    print("-" * 110)
    for name, v in VOICES.items():
        print(f"{name:15s} | {v['gender']:8s} | {v['desc']:50s} | {v['id']}")
    print("\nTo test any voice, run:")
    print("  python3 test_voice.py --voice <Name>\n")


def main():
    parser = argparse.ArgumentParser(description="Audition hyper-realistic human voices.")
    parser.add_argument("--voice", "-v", default="Ava", help="Voice name (default: Ava)")
    parser.add_argument("--text", "-t", default="Hello! I am a hyper-realistic neural human voice powered by modern AI. How can I help you today?", help="Text to speak")
    parser.add_argument("--rate", default="+0%", help="Speed adjustment, e.g. '+10%%' or '-15%%'")
    parser.add_argument("--pitch", default="+0Hz", help="Pitch adjustment, e.g. '+5Hz' or '-5Hz'")
    parser.add_argument("--list", "-l", action="store_true", help="List all curated human voices")
    args = parser.parse_args()

    if args.list:
        list_voices()
        return

    asyncio.run(speak(args.text, args.voice, args.rate, args.pitch))


if __name__ == "__main__":
    main()
