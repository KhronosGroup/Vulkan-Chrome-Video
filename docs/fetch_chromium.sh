#!/bin/bash
export PATH="/data/LinuxExtra/chrome/depot_tools:$PATH"
cd /data/LinuxExtra/chrome
mkdir -p chromium
cd chromium
fetch --nohooks --no-history chromium
