$ws = New-Object -comObject WScript.Shell
$sc = $ws.CreateShortcut("C:\Users\Ephre\OneDrive\Desktop\Start Food Ordering System.lnk")
$sc.TargetPath = "C:\Users\Ephre\OneDrive\Desktop\Food ordering system\Start Server.bat"
$sc.WorkingDirectory = "C:\Users\Ephre\OneDrive\Desktop\Food ordering system"
$sc.IconLocation = "C:\Windows\System32\shell32.dll,23"
$sc.Save()
Write-Host "Shortcut created on Desktop"
