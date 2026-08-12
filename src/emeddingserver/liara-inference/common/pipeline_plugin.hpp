#pragma once
/**
 * pipeline_plugin.hpp
 * C ABI interface for loadable Instruct / Coder pipeline DLLs.
 *
 * liara_instruct.dll and liara_coder.dll both export this interface.
 * The inference server loads them at runtime via LoadLibrary – no link-time
 * dependency on openvino_genai in the server binary.
 *
 * Compile DLLs with -DLIARA_PIPELINE_EXPORT → dllexport + no loader class.
 * Include in the server (without the define)  → loader class only.
 */

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#ifdef _WIN32
#  ifdef LIARA_PIPELINE_EXPORT
#    define LIARA_API __declspec(dllexport)
#  else
#    define LIARA_API
#  endif
#else
#  define LIARA_API
#endif

// ---------------------------------------------------------------------------
// C ABI – safe across DLL boundary
// ---------------------------------------------------------------------------
extern "C" {

typedef void* LiaraPipelineHandle;

/** Create and load a pipeline. Returns nullptr on failure. */
LIARA_API LiaraPipelineHandle liara_pipeline_create(const char* model_dir,
                                                     const char* device);

/** Destroy and release a pipeline. */
LIARA_API void liara_pipeline_destroy(LiaraPipelineHandle h);

/**
 * Run inference.
 * Returns 0 on success, -1 on error.
 * out_buf receives a null-terminated UTF-8 string; out_ms receives wall-clock
 * generation time in milliseconds.
 */
LIARA_API int liara_pipeline_generate(LiaraPipelineHandle h,
                                       const char* prompt,
                                       int max_tokens,
                                       char* out_buf, int out_buf_size,
                                       double* out_ms);

/** Returns a human-readable label (lifetime = handle lifetime). */
LIARA_API const char* liara_pipeline_label(LiaraPipelineHandle h);

} // extern "C"

// ---------------------------------------------------------------------------
// C++ RAII loader – compiled into the server only, not the DLLs
// ---------------------------------------------------------------------------
#ifndef LIARA_PIPELINE_EXPORT
#ifdef _WIN32

#include <windows.h>
#include <cstring>
#include <stdexcept>
#include <string>

struct PipelinePlugin {
    using FnCreate   = LiaraPipelineHandle(*)(const char*, const char*);
    using FnDestroy  = void(*)(LiaraPipelineHandle);
    using FnGenerate = int(*)(LiaraPipelineHandle, const char*, int,
                              char*, int, double*);
    using FnLabel    = const char*(*)(LiaraPipelineHandle);

    HMODULE             dll        = nullptr;
    LiaraPipelineHandle handle     = nullptr;
    FnCreate            fn_create  = nullptr;
    FnDestroy           fn_destroy = nullptr;
    FnGenerate          fn_generate = nullptr;
    FnLabel             fn_label   = nullptr;

    PipelinePlugin() = default;
    PipelinePlugin(const PipelinePlugin&) = delete;
    PipelinePlugin& operator=(const PipelinePlugin&) = delete;
    ~PipelinePlugin() { unload(); }

    /** Load DLL, create pipeline. Returns true on success. */
    bool load(const std::string& dll_path,
              const std::string& model_dir,
              const std::string& device) {
        dll = LoadLibraryA(dll_path.c_str());
        if (!dll) return false;

        fn_create   = reinterpret_cast<FnCreate>  (GetProcAddress(dll, "liara_pipeline_create"));
        fn_destroy  = reinterpret_cast<FnDestroy> (GetProcAddress(dll, "liara_pipeline_destroy"));
        fn_generate = reinterpret_cast<FnGenerate>(GetProcAddress(dll, "liara_pipeline_generate"));
        fn_label    = reinterpret_cast<FnLabel>   (GetProcAddress(dll, "liara_pipeline_label"));

        if (!fn_create || !fn_destroy || !fn_generate || !fn_label) {
            FreeLibrary(dll);
            dll = nullptr;
            return false;
        }

        handle = fn_create(model_dir.c_str(), device.c_str());
        if (!handle) {
            FreeLibrary(dll);
            dll = nullptr;
            return false;
        }
        return true;
    }

    void unload() {
        if (handle && fn_destroy) { fn_destroy(handle); handle = nullptr; }
        if (dll)                  { FreeLibrary(dll);   dll    = nullptr; }
    }

    bool ready() const { return handle != nullptr; }

    /** Run inference. Throws std::runtime_error on failure. */
    std::string generate(const std::string& prompt, int max_tokens, double& out_ms) {
        std::string buf(65536, '\0');
        int rc = fn_generate(handle, prompt.c_str(), max_tokens,
                             buf.data(), static_cast<int>(buf.size()), &out_ms);
        if (rc != 0)
            throw std::runtime_error("pipeline_generate failed (rc=" + std::to_string(rc) + ")");
        buf.resize(std::strlen(buf.c_str()));
        return buf;
    }

    std::string label() const {
        if (!fn_label || !handle) return "";
        return fn_label(handle);
    }
};

#endif // _WIN32
#endif // !LIARA_PIPELINE_EXPORT
