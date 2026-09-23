@echo off
cd /d "%~dp0"
echo Registering a daily Windows Scheduled Task called "BudgetBillsReminder"...
echo This makes bill reminders fire once a day even if the app/browser isn't open.
echo Default time is 8:00 AM - edit the /st value below and re-run this file to change it,
echo or adjust it later in Windows Task Scheduler directly.
schtasks /create /tn "BudgetBillsReminder" /tr "python \"%~dp0notify_check.py\"" /sc daily /st 08:00 /f
echo.
echo Done. To remove it later, run remove_scheduled_reminders.bat.
pause
