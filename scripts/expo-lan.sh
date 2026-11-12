#!/usr/bin/env bash
# Expo dev server for a phone on the same Wi-Fi when the code runs in WSL2.
# Needs scripts/wsl-expose.ps1 (port forwarding) to have been run on Windows.
# Usage: scripts/expo-lan.sh [windows-wifi-ip]
set -euo pipefail
cd "$(dirname "$0")/../mobile"
IP="${1:-$(powershell.exe -NoProfile -Command \
  "(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias 'Wi-Fi*' | Select-Object -First 1).IPAddress" \
  2>/dev/null | tr -d '\r')}"
[ -n "$IP" ] || { echo "Wi-Fi IP not found, pass it as an argument"; exit 1; }
echo "Expo Go -> Metro at $IP:8081, app -> backend at http://$IP:8000"
REACT_NATIVE_PACKAGER_HOSTNAME="$IP" exec npx expo start
