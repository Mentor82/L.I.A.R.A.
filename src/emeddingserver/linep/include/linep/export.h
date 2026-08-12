#pragma once

// ── DLL export/import macro ──────────────────────────────────────────────────
// Define LINEP_SHARED when building or consuming a shared library.
// The build system sets LINEP_EXPORTS when compiling the library itself.

#ifdef LINEP_SHARED
#  ifdef _WIN32
#    ifdef LINEP_EXPORTS
#      define LINEP_API __declspec(dllexport)
#    else
#      define LINEP_API __declspec(dllimport)
#    endif
#  else
#    define LINEP_API __attribute__((visibility("default")))
#  endif
#else
#  define LINEP_API   // static build — no decoration needed
#endif
