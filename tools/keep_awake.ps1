# Keeps Windows from going to sleep while this window is open (started by serve_live.bat).
# The screen may still turn off; that is fine. Closing the laptop lid can still sleep it: keep the lid open.
Add-Type -Name Power -Namespace Win32 -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);
'@
$continuous = [uint32]2147483648   # ES_CONTINUOUS
$systemRequired = [uint32]1        # ES_SYSTEM_REQUIRED
Write-Host "Keeping this computer awake for the live link. Close this window to allow sleep again."
while ($true) {
    [void][Win32.Power]::SetThreadExecutionState($continuous -bor $systemRequired)
    Start-Sleep -Seconds 60
}
