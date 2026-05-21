' Launches run.bat invisibly (no terminal window) for autostart.
' Also launches the watchdog process that will auto-restart on crash.
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
WshShell.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.Environment("Process").Item("WISPR_SILENT") = "1"
WshShell.Run "cmd /c run.bat", 0, False
' Watchdog runs separately and respects its own single-instance lock
WshShell.Run "cmd /c .venv\Scripts\python.exe -m src.watchdog", 0, False
