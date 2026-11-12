# Forward the backend (8000) and Expo Metro (8081) from WSL2 to this PC's
# network so a phone on the same Wi-Fi can reach them.
# Run in an *Administrator* PowerShell. The WSL IP changes after a reboot:
# run it again then. Undo:  netsh interface portproxy reset
#                           Remove-NetFirewallRule -DisplayName "dostava-bg dev"
$ports = 8000, 8081
$wslIp = (wsl hostname -I).Trim().Split(' ')[0]
foreach ($p in $ports) {
  netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 | Out-Null
  netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$wslIp
}
if (-not (Get-NetFirewallRule -DisplayName "dostava-bg dev" -ErrorAction SilentlyContinue)) {
  # Private profile only: mark your home Wi-Fi as a private network
  New-NetFirewallRule -DisplayName "dostava-bg dev" -Direction Inbound -Protocol TCP `
    -LocalPort $ports -Action Allow -Profile Private | Out-Null
}
netsh interface portproxy show v4tov4
Write-Host "WSL $wslIp forwarded. Phone: http://<this PC's Wi-Fi IP>:8000"
