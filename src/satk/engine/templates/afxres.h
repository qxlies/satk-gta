/* satk shim for MFC's afxres.h (SPEC 4.12.2, verified V5 / report 28).
 * The MTA client .rc files include <afxres.h> but use only symbols that winres.h
 * from the Windows SDK provides (IDOK, IDCANCEL; AFX_* are #if guards).
 * Lives OUTSIDE the fork ({{WORKSPACE}}\engine\shims) and is added to
 * ResourceCompile include paths by {{WORKSPACE}}\engine\Directory.Build.targets.
 * If the VS ATLMFC component is ever installed, this shim is still harmless. */
#pragma once
#include <winres.h>
#ifndef IDC_STATIC
#define IDC_STATIC (-1)
#endif
