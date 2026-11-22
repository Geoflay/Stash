; Explorer's "Edit with Stash" on video and sound files: opens them in the Edit tab.
; Installed for the current user (Tauri's default install mode), so under HKCU.

!define STASH_VERB "shell\Stash.Edit"

; STASH_EXTENSIONS: calls a macro for every extension the Edit tab opens, written by
; build.rs from src/features/edit/extensions.json.
!include "${__FILEDIR__}\extensions.nsh"

; Under SystemFileAssociations, so the menu shows whatever app opens the file.
!macro STASH_ADD_VERB EXT
  WriteRegStr HKCU "Software\Classes\SystemFileAssociations\${EXT}\${STASH_VERB}" "" "Edit with Stash"
  WriteRegStr HKCU "Software\Classes\SystemFileAssociations\${EXT}\${STASH_VERB}" "Icon" "$INSTDIR\${MAINBINARYNAME}.exe,0"
  WriteRegStr HKCU "Software\Classes\SystemFileAssociations\${EXT}\${STASH_VERB}\command" "" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\" --edit $\"%1$\""
!macroend

!macro STASH_REMOVE_VERB EXT
  DeleteRegKey HKCU "Software\Classes\SystemFileAssociations\${EXT}\${STASH_VERB}"
!macroend

; Explorer reads the menus again.
!macro STASH_REFRESH_EXPLORER
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

!macro NSIS_HOOK_POSTINSTALL
  !insertmacro STASH_EXTENSIONS STASH_ADD_VERB
  !insertmacro STASH_REFRESH_EXPLORER
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  !insertmacro STASH_EXTENSIONS STASH_REMOVE_VERB
  !insertmacro STASH_REFRESH_EXPLORER
!macroend
