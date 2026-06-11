#!/bin/bash

# Function to connect to Wi-Fi

local ssid="$1"
local password="$2"

# Validasi parameter
if [ -z "$ssid" ] || [ -z "$password" ]; then
    echo "Usage: connect_wifi <SSID> <Password>"
    exit 1
fi
    
echo "Menghapus profil lama..."
nmcli connection delete "$ssid" >/dev/null 2>&1 || true

echo "Menunggu..."
sleep 2

echo "Menghubungkan ke WiFi: $ssid"

if [ -n "$password" ]; then
   nmcli device wifi connect "$ssid" password "$password"
else
   nmcli device wifi connect "$ssid"
fi


# Example usage:
# connect_wifi "Your_SSID" "Your_Password" 

