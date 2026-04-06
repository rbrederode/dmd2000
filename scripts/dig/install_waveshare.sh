#!/bin/bash
set -e

SRC_DIR="$(cd "$(dirname "$0")/../../dist/waveshare" && pwd)"
LIB_DST="/usr/local/lib"
INC_DST="/usr/local/include"

# 1. Copy libftd2xx.1.4.24.dylib if not present or different
if ! cmp -s "$SRC_DIR/libftd2xx.1.4.24.dylib" "$LIB_DST/libftd2xx.1.4.24.dylib"; then
    echo "Copying libftd2xx.1.4.24.dylib to $LIB_DST"
    sudo cp "$SRC_DIR/libftd2xx.1.4.24.dylib" "$LIB_DST/libftd2xx.1.4.24.dylib"
fi

# 2. Create/update symlink
if [ ! -L "$LIB_DST/libftd2xx.dylib" ] || [ "$(readlink $LIB_DST/libftd2xx.dylib)" != "libftd2xx.1.4.24.dylib" ]; then
    echo "Creating symlink libftd2xx.dylib -> libftd2xx.1.4.24.dylib"
    sudo ln -sf "$LIB_DST/libftd2xx.1.4.24.dylib" "$LIB_DST/libftd2xx.dylib"
fi

# 3. Copy ftd2xx.h if not present or different
if ! cmp -s "$SRC_DIR/ftd2xx.h" "$INC_DST/ftd2xx.h"; then
    echo "Copying ftd2xx.h to $INC_DST"
    sudo cp "$SRC_DIR/ftd2xx.h" "$INC_DST/ftd2xx.h"
fi

# 4. Copy WinTypes.h if not present or different
if ! cmp -s "$SRC_DIR/WinTypes.h" "$INC_DST/WinTypes.h"; then
    echo "Copying WinTypes.h to $INC_DST"
    sudo cp "$SRC_DIR/WinTypes.h" "$INC_DST/WinTypes.h"
fi

echo "Waveshare driver installation complete."