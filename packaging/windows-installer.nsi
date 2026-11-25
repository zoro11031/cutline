Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma
AllowSkipFiles off

!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "Sections.nsh"

!define APP_NAME "Cutline"
!define APP_PUBLISHER "Cutline contributors"
!define APP_URL "https://github.com/zoro11031/cutline"
!define APP_DESCRIPTION "Design, print and cut with Silhouette cutters"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

Var StageCreated

Name "${APP_NAME}"
OutFile "${INSTALLER}"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey "FileDescription" "${APP_NAME} Setup"
VIAddVersionKey "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "${APP_PUBLISHER}"

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_COMPONENTSPAGE_SMALLDESC

!define MUI_LICENSEPAGE_TEXT_TOP "Cutline is free software licensed under the GNU General Public License, version 3 (GPLv3). Review the license terms before installing."
!define MUI_LICENSEPAGE_TEXT_BOTTOM "If you accept the terms of the GPLv3 license agreement, click I Agree to continue."

!define MUI_FINISHPAGE_RUN "$INSTDIR\app\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "$\"$INSTDIR\app\launcher.py$\""
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APP_NAME}"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\app\Start Here.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Open Start Here notes"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_PRE KeepExistingInstallDir
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE VerifyInstallDir
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

!macro CHECK_APP_NOT_RUNNING LABEL_PREFIX
  ${LABEL_PREFIX}_retry:
  ${If} ${FileExists} "$INSTDIR\app\python313.dll"
    ClearErrors
    FileOpen $1 "$INSTDIR\app\python313.dll" a
    ${If} ${Errors}
      ${If} ${Silent}
        SetErrorLevel 1
        Abort "${APP_NAME} is currently running. Close ${APP_NAME} and try again."
      ${EndIf}
      MessageBox MB_ICONEXCLAMATION|MB_RETRYCANCEL "${APP_NAME} appears to be running in '$INSTDIR'. Close ${APP_NAME} before continuing." /SD IDCANCEL IDRETRY ${LABEL_PREFIX}_retry
      Abort
    ${EndIf}
    FileClose $1
  ${EndIf}
!macroend

Section "Cutline (required)" SecCore
  SectionIn RO

  Call CheckInstallDirWritable
  !insertmacro CHECK_APP_NOT_RUNNING install

  ; Keep the installed copy until extraction completes.
  IfFileExists "$INSTDIR\app.previous\launcher.py" 0 previous_ready
  IfFileExists "$INSTDIR\app\launcher.py" previous_ready
  ClearErrors
  Rename "$INSTDIR\app.previous" "$INSTDIR\app"
  IfErrors replace_failed
  previous_ready:
  IfFileExists "$INSTDIR\app.new\*" 0 start_stage
  IfFileExists "$INSTDIR\app.new\.cutline-stage" 0 replace_failed
  RMDir /r "$INSTDIR\app.new"
  start_stage:
  SetOutPath "$INSTDIR\app.new"
  ClearErrors
  FileOpen $1 "$INSTDIR\app.new\.cutline-stage" w
  IfErrors replace_failed
  FileClose $1
  StrCpy $StageCreated 1
  ClearErrors
  File /r "${PAYLOAD}/*"
  IfErrors replace_failed
  SetOutPath "$INSTDIR"
  !insertmacro CHECK_APP_NOT_RUNNING replace
  IfFileExists "$INSTDIR\app.previous\*" 0 move_installed
  IfFileExists "$INSTDIR\app.previous\launcher.py" 0 replace_failed
  RMDir /r "$INSTDIR\app.previous"
  move_installed:
  ClearErrors
  IfFileExists "$INSTDIR\app\launcher.py" 0 install_staged
  Rename "$INSTDIR\app" "$INSTDIR\app.previous"
  IfErrors replace_failed
  install_staged:
  ClearErrors
  Rename "$INSTDIR\app.new" "$INSTDIR\app"
  IfErrors restore_previous
  ClearErrors
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  IfErrors undo_install
  Delete "$INSTDIR\app\.cutline-stage"
  RMDir /r "$INSTDIR\app.previous"
  Goto install_registered
  undo_install:
  Rename "$INSTDIR\app" "$INSTDIR\app.new"
  restore_previous:
  IfFileExists "$INSTDIR\app.previous\launcher.py" 0 replace_failed
  Rename "$INSTDIR\app.previous" "$INSTDIR\app"
  replace_failed:
  SetOutPath "$TEMP"
  StrCmp $StageCreated 1 0 +2
  RMDir /r "$INSTDIR\app.new"
  SetErrorLevel 1
  Abort "Could not replace the Cutline app files. Close Cutline and run Setup again."
  install_registered:

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "${APP_URL}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\app\cutline.ico"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" $0
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Start Menu shortcut" SecStartMenu
  SetOutPath "$INSTDIR\app"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\app\pythonw.exe" '"$INSTDIR\app\launcher.py"' "$INSTDIR\app\cutline.ico" 0 SW_SHOWNORMAL "" "${APP_DESCRIPTION}"
SectionEnd

Section /o "Desktop shortcut" SecDesktop
  SetOutPath "$INSTDIR\app"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\app\pythonw.exe" '"$INSTDIR\app\launcher.py"' "$INSTDIR\app\cutline.ico" 0 SW_SHOWNORMAL "" "${APP_DESCRIPTION}"
SectionEnd

LangString DESC_SecCore ${LANG_ENGLISH} "Install ${APP_NAME}, bundled Qt and Python runtime, icon, and uninstaller."
LangString DESC_SecStartMenu ${LANG_ENGLISH} "Add a ${APP_NAME} shortcut to the Windows Start Menu."
LangString DESC_SecDesktop ${LANG_ENGLISH} "Add a ${APP_NAME} shortcut to your Desktop."

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore} $(DESC_SecCore)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecStartMenu} $(DESC_SecStartMenu)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} $(DESC_SecDesktop)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function CheckInstallDirWritable
  ${If} "$INSTDIR" == ""
    ${If} ${Silent}
      SetErrorLevel 1
    ${EndIf}
    MessageBox MB_ICONSTOP|MB_OK "Choose an installation folder." /SD IDOK
    Abort
  ${EndIf}

  StrLen $0 "$INSTDIR"
  ${If} $0 <= 3
    StrCpy $1 "$INSTDIR" 1 1
    ${If} $1 == ":"
      ${If} ${Silent}
        SetErrorLevel 1
      ${EndIf}
      MessageBox MB_ICONSTOP|MB_OK "Choose a subfolder for ${APP_NAME} instead of the drive root '$INSTDIR'." /SD IDOK
      Abort
    ${EndIf}
  ${EndIf}

  StrCpy $0 "$INSTDIR"
  StrCpy $3 0
  ${Do}
    StrCpy $5 "$0" 1 -1
    ${If} $5 == "\"
      StrCpy $4 "$0*.*"
    ${Else}
      StrCpy $4 "$0\*.*"
    ${EndIf}
    ${If} ${FileExists} "$4"
      ${ExitDo}
    ${EndIf}
    ; Walk up one directory purely by string scan (GetFullPathName returns "" for non-existent paths).
    StrLen $4 "$0"
    ${DoWhile} $4 > 0
      IntOp $4 $4 - 1
      StrCpy $5 "$0" 1 $4
      ${If} $5 == "\"
        ${ExitDo}
      ${EndIf}
    ${Loop}
    ${If} $4 == 0
      StrCpy $2 ""
    ${Else}
      StrCpy $2 "$0" $4
      StrLen $4 "$2"
      ${If} $4 == 2
        StrCpy $5 "$2" 1 1
        ${If} $5 == ":"
          StrCpy $2 "$2\"
        ${EndIf}
      ${EndIf}
    ${EndIf}
    ${If} $2 == ""
    ${OrIf} $2 == $0
    ${OrIf} $3 >= 64
      ${If} ${Silent}
        SetErrorLevel 1
      ${EndIf}
      MessageBox MB_ICONSTOP|MB_OK "The drive or parent folder for '$INSTDIR' is not accessible. Choose a different folder." /SD IDOK
      Abort
    ${EndIf}
    StrCpy $0 "$2"
    IntOp $3 $3 + 1
  ${Loop}

  ClearErrors
  GetTempFileName $1 "$0"
  ${If} ${Errors}
    ${If} ${Silent}
      SetErrorLevel 1
    ${EndIf}
    MessageBox MB_ICONSTOP|MB_OK "Cutline cannot write to '$INSTDIR'. Choose a folder you have permission to modify, such as under your user profile." /SD IDOK
    Abort
  ${EndIf}
  Delete "$1"
FunctionEnd

Function KeepExistingInstallDir
  IfFileExists "$INSTDIR\app\launcher.py" 0 choose_directory
  Abort
  choose_directory:
FunctionEnd

Function VerifyInstallDir
  Call CheckInstallDirWritable

  ${If} ${FileExists} "$INSTDIR\*.*"
    ${IfNot} ${FileExists} "$INSTDIR\Uninstall.exe"
    ${AndIfNot} ${FileExists} "$INSTDIR\app\launcher.py"
      FindFirst $1 $2 "$INSTDIR\*.*"
      ${DoWhile} $2 != ""
        ${If} $2 != "."
        ${AndIf} $2 != ".."
          FindClose $1
          MessageBox MB_ICONEXCLAMATION|MB_YESNO|MB_DEFBUTTON2 "The folder '$INSTDIR' already exists and is not empty.$\r$\n$\r$\n${APP_NAME} will install into '$INSTDIR\app'. Continue?" /SD IDYES IDYES dir_verified
          Abort
        ${EndIf}
        FindNext $1 $2
      ${Loop}
      FindClose $1
    ${EndIf}
  ${EndIf}
  dir_verified:
FunctionEnd

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Cutline requires 64-bit Windows." /SD IDOK
    SetErrorLevel 1
    Quit
  ${EndIf}
  SetShellVarContext current
  SetRegView 64
  ; Older Setup wrote InstallLocation after switching registry views.
  ; Resolve that view explicitly before opening the directory page.
  IfFileExists "$INSTDIR\app\launcher.py" shortcuts_init
  StrCmp "$INSTDIR" "$LOCALAPPDATA\Programs\${APP_NAME}" 0 shortcuts_init
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  StrCmp $0 "" shortcuts_init
  IfFileExists "$0\app\launcher.py" 0 shortcuts_init
  StrCpy $INSTDIR "$0"
  shortcuts_init:
  IfFileExists "$INSTDIR\app\launcher.py" 0 shortcuts_ready
  IfFileExists "$SMPROGRAMS\${APP_NAME}.lnk" start_menu_ready
  !insertmacro UnselectSection ${SecStartMenu}
  start_menu_ready:
  IfFileExists "$DESKTOP\${APP_NAME}.lnk" 0 shortcuts_ready
  !insertmacro SelectSection ${SecDesktop}
  shortcuts_ready:
FunctionEnd

Function .onInstFailed
  StrCmp $StageCreated 1 0 +3
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR\app.new"
FunctionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
FunctionEnd

Section "Uninstall"
  SetOutPath "$TEMP"
  !insertmacro CHECK_APP_NOT_RUNNING uninstall

  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$INSTDIR\app"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
