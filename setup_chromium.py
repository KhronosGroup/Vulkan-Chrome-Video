#!/usr/bin/env python3
"""
Chromium Vulkan Video Integration - Setup Script

This script automates the setup process for building Chromium with Vulkan Video support:
1. Installs required system packages (Ubuntu/Debian)
2. Fetches Google's depot_tools
3. Fetches Chromium source code
4. Prints build instructions

Usage:
    python3 setup_chromium.py [--skip-packages] [--skip-depot-tools] [--skip-chromium]

Requirements:
    - Ubuntu 20.04+ or Debian 11+
    - At least 100GB free disk space
    - At least 16GB RAM (32GB recommended)
    - Fast internet connection
"""

import os
import sys
import subprocess
import argparse
import shutil
from pathlib import Path

# Configuration
SCRIPT_DIR = Path(__file__).parent.resolve()
DEPOT_TOOLS_URL = "https://chromium.googlesource.com/chromium/tools/depot_tools.git"
DEPOT_TOOLS_DIR = SCRIPT_DIR / "depot_tools"
CHROMIUM_DIR = SCRIPT_DIR / "chromium"

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_header(msg: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{msg}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}\n")

def print_step(msg: str):
    print(f"{Colors.CYAN}▶ {msg}{Colors.ENDC}")

def print_success(msg: str):
    print(f"{Colors.GREEN}✓ {msg}{Colors.ENDC}")

def print_warning(msg: str):
    print(f"{Colors.WARNING}⚠ {msg}{Colors.ENDC}")

def print_error(msg: str):
    print(f"{Colors.FAIL}✗ {msg}{Colors.ENDC}")

def run_command(cmd: list, cwd: Path = None, env: dict = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"  {Colors.BLUE}$ {' '.join(cmd)}{Colors.ENDC}")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=merged_env,
        capture_output=False,
        text=True
    )
    
    if check and result.returncode != 0:
        print_error(f"Command failed with exit code {result.returncode}")
        sys.exit(1)
    
    return result

def check_disk_space():
    """Check if there's enough disk space."""
    print_step("Checking disk space...")
    
    stat = os.statvfs(SCRIPT_DIR)
    free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
    
    if free_gb < 100:
        print_warning(f"Only {free_gb:.1f}GB free. Chromium requires ~100GB. Proceed with caution.")
    else:
        print_success(f"{free_gb:.1f}GB free disk space available")

def check_ram():
    """Check available RAM."""
    print_step("Checking system RAM...")
    
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    ram_kb = int(line.split()[1])
                    ram_gb = ram_kb / (1024**2)
                    
                    if ram_gb < 16:
                        print_warning(f"Only {ram_gb:.1f}GB RAM. 16GB minimum, 32GB recommended.")
                    else:
                        print_success(f"{ram_gb:.1f}GB RAM available")
                    break
    except Exception as e:
        print_warning(f"Could not check RAM: {e}")

def install_packages():
    """Install required system packages for building Chromium."""
    print_header("Installing System Packages")
    
    # Check if we're on a Debian-based system
    if not shutil.which('apt-get'):
        print_error("This script requires apt-get (Ubuntu/Debian)")
        print("For other distributions, please install dependencies manually.")
        print("See: https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md")
        return False
    
    # Essential build tools
    packages = [
        # Build essentials
        "build-essential",
        "clang",
        "lld",
        "cmake",
        "ninja-build",
        "pkg-config",
        "python3",
        "python3-pip",
        
        # Required libraries
        "libcups2-dev",
        "libdbus-1-dev",
        "libdrm-dev",
        "libgbm-dev",
        "libgtk-3-dev",
        "libkrb5-dev",
        "libnss3-dev",
        "libpango1.0-dev",
        "libpci-dev",
        "libpulse-dev",
        "libudev-dev",
        "libva-dev",
        "libxcomposite-dev",
        "libxdamage-dev",
        "libxkbcommon-dev",
        "libxrandr-dev",
        "libxshmfence-dev",
        "libxt-dev",
        "libxtst-dev",
        
        # Vulkan development
        "libvulkan-dev",
        "vulkan-tools",
        "mesa-vulkan-drivers",
        
        # Additional dependencies
        "curl",
        "git",
        "git-lfs",
        "lsb-release",
        "file",
        "fonts-liberation",
        "libatk-bridge2.0-dev",
        "libatspi2.0-dev",
        "libcurl4-openssl-dev",
        "libffi-dev",
        "libglib2.0-dev",
        "libnspr4-dev",
        "libsqlite3-dev",
        "libssl-dev",
        "libxcb1-dev",
        "uuid-dev",
        "wget",
        "xz-utils",
    ]
    
    print_step("Updating package lists...")
    run_command(["sudo", "apt-get", "update"])
    
    print_step(f"Installing {len(packages)} packages...")
    run_command(["sudo", "apt-get", "install", "-y"] + packages)
    
    print_success("System packages installed successfully")
    return True

def fetch_depot_tools():
    """Clone depot_tools from Google."""
    print_header("Fetching depot_tools")
    
    if DEPOT_TOOLS_DIR.exists():
        print_step("depot_tools directory exists, updating...")
        run_command(["git", "pull"], cwd=DEPOT_TOOLS_DIR)
    else:
        print_step("Cloning depot_tools...")
        run_command(["git", "clone", DEPOT_TOOLS_URL, str(DEPOT_TOOLS_DIR)])
    
    print_success(f"depot_tools ready at: {DEPOT_TOOLS_DIR}")
    return True

def fetch_chromium():
    """Fetch Chromium source code using depot_tools."""
    print_header("Fetching Chromium Source Code")
    
    print_warning("This will download ~30GB and may take several hours!")
    
    # Set up environment
    env = {"PATH": f"{DEPOT_TOOLS_DIR}:{os.environ.get('PATH', '')}"}
    
    # Create chromium directory
    CHROMIUM_DIR.mkdir(exist_ok=True)
    
    src_dir = CHROMIUM_DIR / "src"
    
    if src_dir.exists():
        print_step("Chromium source exists, syncing...")
        run_command(["gclient", "sync", "--nohooks"], cwd=CHROMIUM_DIR, env=env)
    else:
        print_step("Fetching Chromium (this will take a while)...")
        run_command(["fetch", "--nohooks", "--no-history", "chromium"], cwd=CHROMIUM_DIR, env=env)
    
    # Run hooks
    print_step("Running gclient hooks...")
    run_command(["gclient", "runhooks"], cwd=CHROMIUM_DIR, env=env)
    
    # Install additional build dependencies using Chromium's script
    install_deps_script = src_dir / "build" / "install-build-deps.sh"
    if install_deps_script.exists():
        print_step("Running Chromium's install-build-deps.sh...")
        run_command(["bash", str(install_deps_script), "--no-prompt"], cwd=src_dir)
    
    print_success(f"Chromium source ready at: {src_dir}")
    return True

def print_build_instructions():
    """Print instructions for building Chromium."""
    print_header("Build Instructions")
    
    src_dir = CHROMIUM_DIR / "src"
    
    instructions = f"""
{Colors.BOLD}To build Chromium with Vulkan Video support:{Colors.ENDC}

{Colors.CYAN}1. Set up environment:{Colors.ENDC}
   export PATH="{DEPOT_TOOLS_DIR}:$PATH"
   cd {src_dir}

{Colors.CYAN}2. Generate build files with Vulkan enabled:{Colors.ENDC}
   gn gen out/Default --args='
     is_debug=false
     is_component_build=false
     symbol_level=0
     enable_nacl=false
     blink_symbol_level=0
     use_vulkan=true
     enable_vulkan_validation_layers=true
     use_vaapi=true
     proprietary_codecs=true
     ffmpeg_branding="Chrome"
   '

{Colors.CYAN}3. Build Chrome:{Colors.ENDC}
   autoninja -C out/Default chrome

{Colors.CYAN}4. Run Chrome with Vulkan:{Colors.ENDC}
   ./out/Default/chrome --enable-features=Vulkan,VaapiVideoDecoder

{Colors.WARNING}Build Tips:{Colors.ENDC}
   - Full build takes 2-6 hours depending on hardware
   - Use 'autoninja' for automatic parallel job detection
   - Add '-j N' to limit to N parallel jobs (useful for memory)
   - Incremental builds are much faster

{Colors.WARNING}For Vulkan Video development:{Colors.ENDC}
   - See docs/VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md
   - See docs/VULKAN_VIDEO_DEVELOPMENT_PLAN.md

{Colors.GREEN}Quick build script:{Colors.ENDC}
   export CHROME_SRC_AND_TOOLS_DIR="{SCRIPT_DIR}"
   ./build-chrome.sh
"""
    print(instructions)

def create_env_script():
    """Create a shell script to set up the environment."""
    env_script = SCRIPT_DIR / "setup_env.sh"
    
    content = f"""#!/bin/bash
# Chromium Vulkan Video - Environment Setup
# Source this file: source setup_env.sh

export CHROME_SRC_AND_TOOLS_DIR="{SCRIPT_DIR}"
export PATH="{DEPOT_TOOLS_DIR}:$PATH"
export CHROMIUM_SRC="{CHROMIUM_DIR}/src"

echo "Environment configured:"
echo "  CHROME_SRC_AND_TOOLS_DIR=$CHROME_SRC_AND_TOOLS_DIR"
echo "  CHROMIUM_SRC=$CHROMIUM_SRC"
echo "  depot_tools added to PATH"

cd "$CHROMIUM_SRC" 2>/dev/null && echo "Changed to Chromium source directory"
"""
    
    with open(env_script, 'w') as f:
        f.write(content)
    
    env_script.chmod(0o755)
    print_success(f"Created environment script: {env_script}")
    print(f"  Usage: {Colors.CYAN}source setup_env.sh{Colors.ENDC}")

def main():
    parser = argparse.ArgumentParser(
        description="Set up Chromium development environment for Vulkan Video integration"
    )
    parser.add_argument('--skip-packages', action='store_true',
                        help='Skip installing system packages')
    parser.add_argument('--skip-depot-tools', action='store_true',
                        help='Skip fetching depot_tools')
    parser.add_argument('--skip-chromium', action='store_true',
                        help='Skip fetching Chromium source')
    parser.add_argument('--instructions-only', action='store_true',
                        help='Only print build instructions')
    
    args = parser.parse_args()
    
    print_header("Chromium Vulkan Video Integration Setup")
    print(f"Project directory: {SCRIPT_DIR}")
    
    if args.instructions_only:
        print_build_instructions()
        return 0
    
    # Pre-flight checks
    check_disk_space()
    check_ram()
    
    # Install packages
    if not args.skip_packages:
        if not install_packages():
            print_warning("Package installation had issues, continuing anyway...")
    else:
        print_step("Skipping package installation")
    
    # Fetch depot_tools
    if not args.skip_depot_tools:
        if not fetch_depot_tools():
            print_error("Failed to fetch depot_tools")
            return 1
    else:
        print_step("Skipping depot_tools fetch")
    
    # Fetch Chromium
    if not args.skip_chromium:
        if not fetch_chromium():
            print_error("Failed to fetch Chromium")
            return 1
    else:
        print_step("Skipping Chromium fetch")
    
    # Create helper scripts
    create_env_script()
    
    # Print build instructions
    print_build_instructions()
    
    print_header("Setup Complete!")
    print(f"{Colors.GREEN}Next steps:{Colors.ENDC}")
    print(f"  1. Source the environment: {Colors.CYAN}source setup_env.sh{Colors.ENDC}")
    print(f"  2. Follow the build instructions above")
    print(f"  3. Read the documentation in {Colors.CYAN}docs/{Colors.ENDC}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

