Unicode true
RequestExecutionLevel user
SetCompressor /SOLID zlib

!include "MUI2.nsh"
!include "x64.nsh"
!include "Sections.nsh"
!include "FileFunc.nsh"

!define APP_NAME "Cutline"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Cutline"

Name "${APP_NAME}"
OutFile "${INSTALLER}"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} Setup"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "Cutline contributors"

Var Window

!define MUI_ABORTWARNING
!define MUI_COMPONENTSPAGE_TEXT_TOP "Choose the window Cutline opens with. Both use the same engine; the GTK window is experimental on Windows."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "-Cutline" SecCore
  ; A reinstall may switch windows: remove the runtime from before.
  RMDir /r "$INSTDIR\app"
  SetOutPath "$INSTDIR\app"
  File /r "${PAYLOAD}/core/*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Qt window (recommended)" SecQt
  SetOutPath "$INSTDIR\app\qt"
  File /r "${PAYLOAD}/qt/*"
  SetOutPath "$INSTDIR\app"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\app\qt\pythonw.exe" '"$INSTDIR\app\launcher.py"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\app\qt\pythonw.exe"
SectionEnd

!ifdef WITH_GTK
Section /o "GTK window (experimental)" SecGtk
  SetOutPath "$INSTDIR\app\gtk"
  File /r "${PAYLOAD}/gtk/*"
  ; MSYS2 builds these caches in its post-install hooks; do the same here.
  DetailPrint "Preparing the GTK runtime…"
  nsExec::ExecToLog '"$INSTDIR\app\gtk\bin\gdk-pixbuf-query-loaders.exe" --update-cache'
  Pop $0
  nsExec::ExecToLog '"$INSTDIR\app\gtk\bin\gtk4-update-icon-cache.exe" -q -t -f "$INSTDIR\app\gtk\share\icons\hicolor"'
  Pop $0
  SetOutPath "$INSTDIR\app"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\app\gtk\bin\pythonw.exe" '"$INSTDIR\app\launcher.py"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\app\gtk\bin\pythonw.exe"
SectionEnd
!endif

LangString DESC_Qt ${LANG_ENGLISH} "The Qt window. Python and Qt are included."
!ifdef WITH_GTK
LangString DESC_Gtk ${LANG_ENGLISH} "The GTK 4 window, with its own Python and GTK runtime. Experimental on Windows."
!endif
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecQt} $(DESC_Qt)
!ifdef WITH_GTK
  !insertmacro MUI_DESCRIPTION_TEXT ${SecGtk} $(DESC_Gtk)
!endif
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Cutline requires 64-bit Windows." /SD IDOK
    SetErrorLevel 1
    Quit
  ${EndIf}
  SetShellVarContext current
  SetRegView 64
  StrCpy $Window ${SecQt}
!ifdef WITH_GTK
  ; Silent installs choose with /GTK, e.g. Setup.exe /S /GTK
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/GTK" $R1
  ${IfNot} ${Errors}
    !insertmacro UnselectSection ${SecQt}
    !insertmacro SelectSection ${SecGtk}
    StrCpy $Window ${SecGtk}
  ${EndIf}
!endif
FunctionEnd

; One window at a time: choosing one clears the other.
Function .onSelChange
!ifdef WITH_GTK
  !insertmacro StartRadioButtons $Window
    !insertmacro RadioButton ${SecQt}
    !insertmacro RadioButton ${SecGtk}
  !insertmacro EndRadioButtons
!endif
FunctionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
FunctionEnd

Section "Uninstall"
  SetOutPath "$TEMP"
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  RMDir /r "$INSTDIR\app"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
