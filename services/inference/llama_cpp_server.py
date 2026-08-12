"""llama-server subprocess manager for local inference."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import subprocess
import time
from typing import Optional

from services.config import Settings

logger = logging.getLogger(__name__)


class LlamaCppServerManager:
    """Manages llama-server subprocess lifecycle."""

    # Available llama.cpp builds (ordered by performance preference)
    AVAILABLE_BUILDS = [
        "sycl-fp16-intel-arc",  # Best for Intel Arc GPU: ~30 t/s prompt, ~9.6 t/s gen
        "vulkan-cross-gpu",      # Best for cross-vendor GPU (NVIDIA/AMD/Intel): ~49.2 t/s prompt, ~20.2 t/s gen
        "cpu-avx2-f16c",         # CPU-only fallback: ~107.8 t/s prompt, ~54.8 t/s gen
    ]

    @classmethod
    def build_base_dir(cls) -> pathlib.Path:
        """Build base directory, resolved from Settings at runtime."""
        return pathlib.Path(Settings.LLAMA_CPP_BUILD_BASE_DIR)

    def __init__(
        self,
        base_url: str | None = None,
        model_path: str | None = None,
        timeout_seconds: float = 120,
        build_variant: str | None = None,
    ):
        """Initialize llama-server manager.
        
        Args:
            base_url: Server URL (default from LLAMA_CPP_BASE_URL setting)
            model_path: Path to GGUF model (default from LLAMA_CPP_MODEL setting)
            timeout_seconds: Startup timeout in seconds
            build_variant: Which llama.cpp build to use (default: auto-detect available)
        """
        self.base_url = base_url or Settings.LLAMA_CPP_BASE_URL
        self.model_path = model_path or Settings.LLAMA_CPP_MODEL
        self.timeout_seconds = timeout_seconds
        self.build_variant = build_variant
        
        self._process: Optional[subprocess.Popen] = None
        self._startup_time: Optional[float] = None

    @classmethod
    def get_build_path(cls, variant: str) -> pathlib.Path:
        """Get path to llama-server binary for given variant."""
        build_dir = cls.build_base_dir() / variant
        binary_name = "llama-server.exe" if os.name == "nt" else "llama-server"
        binary_path = build_dir / binary_name
        if not binary_path.exists():
            raise FileNotFoundError(f"llama-server binary not found: {binary_path}")
        return binary_path

    # Keep instance alias for backward compat
    def _get_build_path(self, variant: str) -> pathlib.Path:
        return self.get_build_path(variant)

    @classmethod
    def find_available_build(cls, preferred_variant: str = "auto") -> tuple[str, pathlib.Path]:
        """Find first available build, ordered by performance.

        Args:
            preferred_variant: explicit build name, or ``"auto"`` to auto-detect.

        Returns:
            (variant_name, path_to_binary)
        """
        candidates = (
            cls.AVAILABLE_BUILDS
            if preferred_variant == "auto"
            else [preferred_variant]
        )
        for variant in candidates:
            try:
                path = cls.get_build_path(variant)
                logger.info(f"[llama-server] Selected {variant} build: {path}")
                return variant, path
            except FileNotFoundError:
                continue
        raise FileNotFoundError(
            f"No llama.cpp builds found in {cls.build_base_dir()}. "
            f"Tried: {candidates}"
        )

    # Keep instance alias
    def _find_available_build(self) -> tuple[str, pathlib.Path]:
        return self.find_available_build(
            preferred_variant=self.build_variant or Settings.LLAMA_CPP_BUILD_VARIANT
        )

    @staticmethod
    def _kill_stale_processes(port: int, *, verbose: bool = True) -> int:
        """Kill any existing llama-server processes already bound to the given port.

        Returns the number of processes killed.
        """
        killed = 0
        try:
            import subprocess as _sp
            result = _sp.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -match 'llama-server' -and $_.CommandLine -match '{port}' }} | Select-Object -ExpandProperty ProcessId"],
                capture_output=True, text=True, timeout=10,
            )
            pids = [int(p.strip()) for p in result.stdout.splitlines() if p.strip().isdigit()]
            for pid in pids:
                try:
                    import os as _os
                    _sp.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                    killed += 1
                    if verbose:
                        logger.warning(f"[llama-server] Killed stale process PID {pid} on port {port}")
                except Exception as e:
                    logger.warning(f"[llama-server] Could not kill stale PID {pid}: {e}")
        except Exception as e:
            logger.warning(f"[llama-server] Stale process scan failed: {e}")
        return killed

    async def start(self, *, verbose: bool = True) -> bool:
        """Start llama-server subprocess.
        
        Returns:
            True if started successfully, False otherwise
        """
        if self._process is not None:
            logger.warning("[llama-server] Already running, skipping start")
            return True

        # Resolve build and model paths
        if not pathlib.Path(self.model_path).exists():
            logger.error(f"[llama-server] Model not found: {self.model_path}")
            return False

        variant, binary_path = self._find_available_build()

        # Parse base_url to extract host and port
        # Format: http://127.0.0.1:8000
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8000
        except Exception as e:
            logger.error(f"[llama-server] Failed to parse base_url {self.base_url}: {e}")
            return False

        # Build command
        cmd = [
            str(binary_path),
            "--host", host,
            "--port", str(port),
            "--model", str(self.model_path),
            "--threads", str(os.cpu_count() or 4),
            "--ctx-size", "8192",
            "--n-gpu-layers", "99",  # Use GPU as much as possible
            "-ngl", "99",             # Alternative flag for GPU layers
        ]

        # Kill any stale llama-server processes on the same port before starting
        killed = self._kill_stale_processes(port, verbose=verbose)
        if killed and verbose:
            logger.info(f"[llama-server] Cleaned up {killed} stale process(es) on port {port}")

        if verbose:
            logger.info(f"[llama-server] Starting {variant} build...")
            logger.info(f"[llama-server] Command: {' '.join(cmd)}")

        # Setup environment with Intel oneAPI if using IntelLLVM-built variants.
        env = os.environ.copy()
        if any(tag in variant.lower() for tag in ("sycl", "vulkan", "cpu-avx2-f16c")):
            setvars_path = pathlib.Path("C:\\Program Files (x86)\\Intel\\oneAPI\\setvars.bat")
            if setvars_path.exists() and os.name == "nt":
                if verbose:
                    logger.info(f"[llama-server] Loading Intel oneAPI environment for {variant}...")
                try:
                    # Run setvars.bat in a thread pool to avoid blocking the event loop
                    import subprocess as sp_module
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        lambda: sp_module.run(
                            f'"{setvars_path}" intel64 & set',
                            shell=True,
                            capture_output=True,
                            text=True,
                        )
                    )
                    # Parse environment variables from output
                    for line in result.stdout.split('\n'):
                        if '=' in line:
                            key, val = line.split('=', 1)
                            env[key.strip()] = val.strip()
                    if verbose:
                        logger.info(f"[llama-server] ✓ oneAPI environment loaded")
                except Exception as e:
                    logger.warning(f"[llama-server] Could not load oneAPI environment: {e}")
            else:
                if verbose:
                    logger.info(f"[llama-server] (setvars.bat not found or not Windows, skipping oneAPI setup)")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            self._startup_time = time.time()
            
            if verbose:
                logger.info(f"[llama-server] Process started (PID: {self._process.pid})")
            
            # Wait for server to be ready
            await self._wait_for_ready(verbose=verbose)
            return True
            
        except Exception as e:
            logger.error(f"[llama-server] Failed to start: {e}", exc_info=True)
            self._process = None
            return False

    async def _wait_for_ready(self, *, verbose: bool = True, max_retries: int = 120) -> bool:
        """Wait for server to respond to health checks.
        
        Note: Large model files may take 1-2+ minutes to load into GPU memory.
        """
        import httpx
        
        health_url = f"{self.base_url}/health"
        start_time = time.time()
        
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(health_url)
                    if response.status_code == 200:
                        elapsed = time.time() - start_time
                        if verbose:
                            logger.info(f"[llama-server] ✓ Ready after {elapsed:.1f}s")
                        return True
            except Exception:
                pass
            
            # Exponential backoff: 0.5, 1, 2, 4, ... up to 5 seconds
            wait_time = min(0.5 * (2 ** (attempt - 1)), 5.0)
            await asyncio.sleep(wait_time)
            
            elapsed = time.time() - start_time
            if elapsed > self.timeout_seconds:
                logger.error(
                    f"[llama-server] Health check timeout after {elapsed:.1f}s "
                    f"({attempt} attempts). Process may still be initializing."
                )
                return False
            
            if attempt % 10 == 0 and verbose:
                logger.info(f"[llama-server] Waiting for startup... ({attempt} checks, {elapsed:.1f}s elapsed)")
        
        return False

    async def stop(self, *, verbose: bool = True) -> bool:
        """Stop llama-server subprocess gracefully.
        
        Returns:
            True if stopped successfully, False otherwise
        """
        if self._process is None:
            return True

        try:
            if verbose:
                logger.info(f"[llama-server] Stopping (PID: {self._process.pid})...")
            
            # Try graceful termination first
            self._process.terminate()
            
            try:
                self._process.wait(timeout=5.0)
                if verbose:
                    logger.info("[llama-server] ✓ Stopped gracefully")
                return True
            except subprocess.TimeoutExpired:
                # Force kill if graceful termination times out
                if verbose:
                    logger.warning("[llama-server] Graceful termination timed out, killing...")
                self._process.kill()
                self._process.wait(timeout=2.0)
                if verbose:
                    logger.info("[llama-server] ✓ Killed")
                return True
                
        except Exception as e:
            logger.error(f"[llama-server] Failed to stop: {e}", exc_info=True)
            return False
        finally:
            self._process = None
            self._startup_time = None

    def is_running(self) -> bool:
        """Check if server is currently running."""
        if self._process is None:
            return False
        
        return self._process.poll() is None

    async def health_check(self) -> dict[str, bool | str]:
        """Perform health check on running server."""
        import httpx
        
        if not self.is_running():
            return {"running": False}
        
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return {
                    "running": True,
                    "status_code": response.status_code,
                    "healthy": response.status_code == 200,
                }
        except Exception as e:
            return {
                "running": True,
                "error": str(e),
                "healthy": False,
            }


# Global singleton instance
_manager: Optional[LlamaCppServerManager] = None


def get_llama_cpp_server_manager() -> LlamaCppServerManager:
    """Get or create global llama-server manager."""
    global _manager
    if _manager is None:
        _manager = LlamaCppServerManager()
    return _manager


async def start_llama_cpp_server() -> bool:
    """Start llama-server for API startup."""
    manager = get_llama_cpp_server_manager()
    return await manager.start(verbose=True)


async def stop_llama_cpp_server() -> bool:
    """Stop llama-server for API shutdown."""
    manager = get_llama_cpp_server_manager()
    return await manager.stop(verbose=True)
