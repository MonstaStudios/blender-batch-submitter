#!/bin/bash
# CGRU Batch Submitter - Build Script
# This script helps build the executable with PyInstaller

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE} CGRU Batch Submitter Build Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo

# Function to print colored status
print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Check if PyInstaller is installed
echo -e "${BLUE}Checking dependencies...${NC}"
if ! command -v pyinstaller &> /dev/null; then
    print_error "PyInstaller not found. Please install with: pip install pyinstaller"
    exit 1
else
    print_status "PyInstaller found"
fi

# Check if Python modules are available
echo -e "${BLUE}Checking dependencies...${NC}"
if ! python -c "import pyinstaller; print('OK')" &> /dev/null; then
    print_error "PyInstaller not found. Please install with: pip install pyinstaller"
    exit 1
else
    print_status "PyInstaller found"
fi

print_status "Dependencies verified"

# Clean previous builds
echo -e "${BLUE}Cleaning previous builds...${NC}"
if [ -d "build" ]; then
    rm -rf build
    print_status "Removed old build directory"
fi

if [ -d "dist" ]; then
    rm -rf dist
    print_status "Removed old dist directory"
fi

# Build the application
echo -e "${BLUE}Building executable...${NC}"
echo

case "${BUILD_MODE:-}" in
    "debug")
        echo -e "${YELLOW}Debug build mode${NC}"
        python -m pyinstaller build.spec --debug --console --noconfirm
        ;;
    "console")
        echo -e "${YELLOW}Console build mode${NC}"
        python -m pyinstaller build.spec --console --noconfirm
        ;;
    "clean")
        echo -e "${YELLOW}Clean build only${NC}"
        python -m pyinstaller build.spec --noconfirm --clean
        ;;
    *)
        echo -e "${YELLOW}Production build mode${NC}"
        python -m pyinstaller build.spec --noconfirm
        ;;;
    *)
        echo -e "${YELLOW}Production build mode${NC}"
        python -m pyinstaller build.spec --noconfirm
        ;;
esac

# Check if build was successful
if [ $? -eq 0 ]; then
    print_status "Build completed successfully!"
    
    if [ -d "dist" ]; then
        echo -e "${BLUE}Output files:${NC}"
        ls -la dist/
        echo -e "${GREEN}Executable location: dist/CGRUBatchSubmitter.exe${NC}"
    fi
else
    print_error "Build failed!"
    exit 1
fi

# Optional: Run the executable
if [ "${2:-}" = "run" ] && [ -f "dist/CGRUBatchSubmitter.exe" ]; then
    echo -e "${BLUE}Running application...${NC}"
    ./dist/CGRUBatchSubmitter.exe
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Build process completed!${NC}"