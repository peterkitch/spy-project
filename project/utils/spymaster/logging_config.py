"""
Logging configuration for spymaster application
"""

import logging
import sys
import locale
import os
import io
import ctypes


def ensure_utf8_stdio():
    """
    Make stdout/stderr UTF-8 across common Windows hosts.
    - Reconfigure text wrappers (py>=3.7) with errors='replace' to avoid hard failures.
    - Set console code pages to 65001 when available.
    No-op on non-Windows.
    """
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def _console_supports_unicode():
    """Check if console supports Unicode characters"""
    enc = (getattr(sys.stdout, "encoding", None) or locale.getpreferredencoding(False) or "").lower()
    return "utf" in enc


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    # Custom colors for PROJECT 9
    NEON_GREEN = '\033[38;5;82m'
    BRIGHT_GREEN = '\033[38;5;46m'
    DIM_GREEN = '\033[38;5;22m'
    YELLOW = '\033[38;5;226m'
    ORANGE = '\033[38;5;208m'
    PURPLE = '\033[38;5;141m'
    CYAN = '\033[38;5;51m'


def setup_logging(app_name=__name__, level=logging.INFO):
    """
    Configure logging for the application
    
    Args:
        app_name: Name of the application logger
        level: Logging level (default: INFO)
    
    Returns:
        Logger instance configured for the application
    """
    # Remove any existing handlers to avoid duplicates
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    # Create a custom logger for the application
    logger = logging.getLogger(app_name)
    logger.setLevel(level)
    
    # Suppress various library logs to reduce noise
    logging.getLogger('werkzeug').setLevel(logging.ERROR)  # HTTP requests
    logging.getLogger('flask.app').setLevel(logging.ERROR)  # Flask logs
    logging.getLogger('yfinance').setLevel(logging.ERROR)  # yfinance logs
    logging.getLogger('urllib3').setLevel(logging.ERROR)  # urllib3 logs
    logging.getLogger('dash').setLevel(logging.WARNING)  # Dash framework logs
    
    return logger


def print_startup_banner():
    """Print the PROJECT 9 startup banner"""
    banner = f"""
{Colors.NEON_GREEN}{Colors.BOLD}
════════════════════════════════════════════════════════════════════════════════{Colors.ENDC}
{Colors.OKGREEN}
{Colors.BRIGHT_GREEN}{Colors.BOLD}                 ██████╗ ██████╗      ██╗ ██████╗████████╗ █████╗               {Colors.ENDC}
{Colors.BRIGHT_GREEN}{Colors.BOLD}                 ██╔══██╗██╔══██╗     ██║██╔════╝╚══██╔══╝██╔══██╗              {Colors.ENDC}
{Colors.NEON_GREEN}{Colors.BOLD}                 ██████╔╝██████╔╝     ██║██║        ██║   ╚██████║              {Colors.ENDC}
{Colors.NEON_GREEN}{Colors.BOLD}                 ██╔═══╝ ██╔══██╗██   ██║██║        ██║    ╚═══██║              {Colors.ENDC}
{Colors.BRIGHT_GREEN}{Colors.BOLD}                 ██║     ██║  ██║╚█████╔╝╚██████╗   ██║    █████╔╝              {Colors.ENDC}
{Colors.BRIGHT_GREEN}{Colors.BOLD}                 ╚═╝     ╚═╝  ╚═╝ ╚════╝  ╚═════╝   ╚═╝    ╚════╝               {Colors.ENDC}
{Colors.OKGREEN}
{Colors.YELLOW}                       Advanced Trading Analysis Platform                       {Colors.ENDC}
{Colors.CYAN}                            Built by Rebel Atom LLC                             {Colors.ENDC}
{Colors.OKGREEN}
{Colors.NEON_GREEN}{Colors.BOLD}════════════════════════════════════════════════════════════════════════════════
{Colors.ENDC}"""
    print(banner)


def print_server_info(port=8050):
    """Print server startup information"""
    gear   = "⚙️" if _console_supports_unicode() else "*"
    globe  = "🌐" if _console_supports_unicode() else "*"
    stop   = "🛑" if _console_supports_unicode() else "*"
    line   = "─" if _console_supports_unicode() else "-"
    print(f"{Colors.CYAN}[{gear}] Starting Dash server...{Colors.ENDC}")
    print(f"{Colors.CYAN}[{globe}] Server URL: {Colors.YELLOW}http://127.0.0.1:{port}{Colors.ENDC}")
    print(f"{Colors.CYAN}[{stop}] Stop server: {Colors.YELLOW}Press Ctrl+C{Colors.ENDC}")
    print(f"{Colors.DIM_GREEN}{line * 80}{Colors.ENDC}\n")