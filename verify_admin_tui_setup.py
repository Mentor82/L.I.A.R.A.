#!/usr/bin/env python3
"""
LIARA Admin TUI - Quick Setup & Verification Script

This script validates that the Admin TUI is properly installed and can start.
"""

import sys
from pathlib import Path

def check_dependencies():
    """Check if required packages are installed."""
    print("Checking dependencies...")
    
    required = {
        'textual': 'Textual (TUI framework)',
        'dataclasses': 'dataclasses (built-in)',
    }
    
    missing = []
    for module, desc in required.items():
        try:
            __import__(module)
            print(f"  ✓ {desc}")
        except ImportError:
            print(f"  ✗ {desc} - MISSING")
            missing.append(module)
    
    return len(missing) == 0

def check_module_structure():
    """Check if all required Admin TUI modules exist."""
    print("\nChecking Admin TUI module structure...")
    
    repo_root = Path(__file__).parent
    admin_tui_dir = repo_root / "frontend" / "admin_tui"
    
    required_files = [
        "__init__.py",
        "app.py",
        "models.py",
        "data_layer.py",
        "validation.py",
        "screens_threshold_editor.py",
        "demo.py",
    ]
    
    all_exist = True
    for fname in required_files:
        fpath = admin_tui_dir / fname
        if fpath.exists():
            print(f"  ✓ {fname}")
        else:
            print(f"  ✗ {fname} - MISSING")
            all_exist = False
    
    return all_exist

def check_launcher():
    """Check if launcher script exists."""
    print("\nChecking launcher script...")
    
    repo_root = Path(__file__).parent
    launcher = repo_root / "run_admin_tui.py"
    
    if launcher.exists():
        print(f"  ✓ run_admin_tui.py exists")
        return True
    else:
        print(f"  ✗ run_admin_tui.py - MISSING")
        return False

def check_config():
    """Check if config directory and thresholds exist."""
    print("\nChecking configuration...")
    
    repo_root = Path(__file__).parent
    config_dir = repo_root / "config"
    thresholds_file = config_dir / "thresholds.json"
    
    if config_dir.exists():
        print(f"  ✓ config/ directory exists")
    else:
        print(f"  ✗ config/ directory - MISSING")
        return False
    
    if thresholds_file.exists():
        print(f"  ✓ thresholds.json exists")
        return True
    else:
        print(f"  ℹ thresholds.json - not yet created (will be auto-created on first save)")
        return True

def check_tests():
    """Check if test files exist."""
    print("\nChecking tests...")
    
    repo_root = Path(__file__).parent
    tests_dir = repo_root / "tests" / "unit"
    
    test_files = [
        "test_admin_tui_models.py",
        "test_admin_tui_validation.py",
    ]
    
    all_exist = True
    for fname in test_files:
        fpath = tests_dir / fname
        if fpath.exists():
            print(f"  ✓ {fname}")
        else:
            print(f"  ✗ {fname} - MISSING")
            all_exist = False
    
    return all_exist

def main():
    """Run all verification checks."""
    print("=" * 60)
    print("LIARA Admin TUI - Setup Verification")
    print("=" * 60)
    
    checks = [
        ("Dependencies", check_dependencies),
        ("Module Structure", check_module_structure),
        ("Launcher", check_launcher),
        ("Configuration", check_config),
        ("Tests", check_tests),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"  ✗ Error: {e}")
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    
    all_passed = True
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status:8} {name}")
        if not result:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n✓ All checks passed! Admin TUI is ready.")
        print("\nTo start the dashboard:")
        print("  python run_admin_tui.py")
        print("\nOr run tests:")
        print("  pytest tests/unit/test_admin_tui_models.py -v")
        print("  pytest tests/unit/test_admin_tui_validation.py -v")
        return 0
    else:
        print("\n✗ Some checks failed. Please review the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
