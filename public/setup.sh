#!/bin/bash
set -e

echo ""
echo "  ============================================"
echo "   Starlight RDT - Agent Setup"
echo "  ============================================"
echo ""

# Detect OS
OS="$(uname -s)"

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "  [ERROR] Python 3 is not installed."
    echo ""
    if [ "$OS" = "Darwin" ]; then
        echo "  Install with: brew install python3"
        echo "  Or download from https://www.python.org/downloads/"
    else
        echo "  Install with: sudo apt install python3 python3-pip"
    fi
    echo ""
    exit 1
fi
echo "  [OK] Python 3 found: $(python3 --version)"
echo ""
echo "  Installing dependencies..."
echo ""

pip3 install mss pyautogui websockets Pillow pystray pyperclip --quiet

if [ "$OS" = "Darwin" ]; then
    echo "  Installing macOS tray support..."
    pip3 install rumps --quiet
elif [ "$OS" = "Linux" ]; then
    echo "  Installing Linux tray support..."
    sudo apt-get install -y -q gir1.2-appindicator3-0.1 python3-gi 2>/dev/null || true
fi

echo ""
echo "  [OK] Dependencies installed"
echo ""
echo "  ============================================"
echo "   Starting Starlight RDT Agent..."
echo "  ============================================"
echo ""
echo "  Your session code will appear in a moment."
echo "  Share it at: https://nicepeople67.github.io/Starlight-RDT/login.html"
echo ""
echo "  Press Ctrl+C to stop sharing."
echo ""

# Find agent.py relative to this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$OS" = "Darwin" ]; then
    # macOS: need to allow screen recording - open System Preferences if first run
    if [ ! -f "$HOME/.starlight-rdt/config.json" ]; then
        echo "  [NOTE] On first run, macOS will ask for Screen Recording permission."
        echo "         Go to System Settings → Privacy & Security → Screen Recording"
        echo "         and enable Terminal or the Starlight RDT app."
        echo ""
    fi
fi

python3 "$SCRIPT_DIR/agent/agent.py"