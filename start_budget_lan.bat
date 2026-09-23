@echo off
cd /d "%~dp0"
echo ============================================================
echo  WARNING: This makes the app reachable from OTHER DEVICES
echo  on your network (so your phone can open it and you can
echo  "Add to Home Screen"). There is no login/password on this
echo  app, so anyone on the same WiFi could open your financial
echo  data while this is running. Only use this on a network you
echo  trust (e.g. your home WiFi) - not a coffee shop, hotel, or
echo  shared/public network.
echo ============================================================
echo.
echo Your PC's local network address(es):
ipconfig | findstr /R /C:"IPv4"
echo.
echo On your phone (same WiFi), open: http://YOUR-PC-IP-ABOVE:PORT
echo (PORT is 5000, unless the app below says that's taken and it picked another one.)
echo Then use your browser's "Add to Home Screen" option.
echo.
set BUDGET_APP_HOST=0.0.0.0
python app.py
pause
