#![windows_subsystem = "windows"]

use std::env;
use std::ffi::OsString;
use std::fs;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;
use std::process::Command;

const LAUNCHER_NAME: &str = "run-liara-server-manager";
const LAUNCHER_VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(windows)]
const ATTACH_PARENT_PROCESS: u32 = 0xFFFFFFFF;

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn AttachConsole(dwProcessId: u32) -> i32;
}

fn print_version() {
    #[cfg(windows)]
    unsafe {
        let _ = AttachConsole(ATTACH_PARENT_PROCESS);
    }

    println!("{} {}", LAUNCHER_NAME, LAUNCHER_VERSION);
}

fn ensure_dir(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|e| format!("create dir failed ({}): {}", path.display(), e))
}

fn ensure_file(path: &Path) -> Result<(), String> {
    if !path.exists() {
        fs::write(path, b"").map_err(|e| format!("create file failed ({}): {}", path.display(), e))?;
    }
    Ok(())
}

fn build_path_with_prefixes(app_dir: &Path) -> OsString {
    let mut combined = OsString::new();
    combined.push(app_dir.join("bin").as_os_str());
    combined.push(";");
    combined.push(app_dir.join("lib").as_os_str());

    if let Some(existing) = env::var_os("PATH") {
        combined.push(";");
        combined.push(existing);
    }

    combined
}

fn append_launcher_stamp(log_file: &Path, manager_exe: &Path) {
    if let Ok(mut handle) = OpenOptions::new().create(true).append(true).open(log_file) {
        let _ = writeln!(
            handle,
            "[launcher] {} {} -> {}",
            LAUNCHER_NAME,
            LAUNCHER_VERSION,
            manager_exe.display()
        );
    }
}

fn run() -> Result<i32, String> {
    let mut args = env::args_os();
    let _ = args.next();
    if let Some(arg) = args.next() {
        let arg = arg.to_string_lossy();
        if arg == "--version" || arg == "-V" {
            print_version();
            return Ok(0);
        }
    }

    let exe_path = env::current_exe().map_err(|e| format!("current_exe failed: {}", e))?;
    let app_dir = exe_path
        .parent()
        .ok_or_else(|| "cannot determine launcher directory".to_string())?
        .to_path_buf();

    let cache_dir = app_dir.join("cache");
    let logs_ui_dir = app_dir.join("logs").join("ui");
    let log_file = logs_ui_dir.join("server-manager.log");
    let config_file = app_dir.join("config").join("server-manager.json");
    let schema_dir = app_dir.join("config").join("glib-2.0").join("schemas");
    let mut project_root = app_dir.clone();
    for _ in 0..3 {
        if !project_root.pop() {
            return Err("cannot derive LIARA_PROJECT_ROOT from launcher location".to_string());
        }
    }
    let manager_exe = app_dir.join("bin").join("liara-server-manager.exe");

    ensure_dir(&cache_dir)?;
    ensure_dir(&logs_ui_dir)?;
    ensure_file(&log_file)?;

    if !manager_exe.exists() {
        return Err(format!("missing target executable: {}", manager_exe.display()));
    }

    append_launcher_stamp(&log_file, &manager_exe);

    let status = Command::new(&manager_exe)
        .current_dir(&app_dir)
        .env("LIARA_PROJECT_ROOT", project_root)
        .env("LIARA_SERVER_MANAGER_CONFIG", config_file)
        .env("LIARA_SERVER_MANAGER_LOG", log_file)
        .env("GTK_USE_PORTAL", "0")
        .env("XDG_CACHE_HOME", cache_dir)
        .env("XDG_DATA_DIRS", app_dir.join("config"))
        .env("GSETTINGS_SCHEMA_DIR", schema_dir)
        .env("PATH", build_path_with_prefixes(&app_dir))
        .status()
        .map_err(|e| format!("launch failed: {}", e))?;

    Ok(status.code().unwrap_or(1))
}

fn main() {
    let code = match run() {
        Ok(code) => code,
        Err(_) => 1,
    };

    std::process::exit(code);
}
