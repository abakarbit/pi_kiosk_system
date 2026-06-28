#!/bin/bash

# Function to connect to Wi-Fi

ssid="$1"
password="$2"

# Validasi parameter
if [ -z "$ssid" ]; then
    echo "[ERROR] Usage: connect_wifi.sh <SSID> [Password]"
    exit 1
fi
    
echo "Menghapus profil lama..."
sudo nmcli connection delete "$ssid" >/dev/null 2>&1 || true

echo "Menunggu..."
sleep 2

echo "Menghubungkan ke WiFi: $ssid"

if [ -n "$password" ]; then
   sudo nmcli device wifi connect "$ssid" password "$password"
else
   sudo nmcli device wifi connect "$ssid"
fi


# Example usage:
# connect_wifi.sh "Your_SSID" "Your_Password" 
# connect_wifi.sh "Open_SSID"
