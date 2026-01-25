# CGRU Batch Submitter

A desktop application designed to streamline the process of batch submitting Blender render jobs to the CGRU/Afanasy render farm manager.

## Overview

The CGRU Batch Submitter provides a user-friendly interface to scan directories for Blender files and submit them to a render farm. It handles the complexity of invoking Blender's Python API for submission, allowing users to use default or custom submission scripts.

## Features

-   **Blender Version Detection**: Automatically detects installed Blender versions on your system.
-   **Batch Scanning**: Quickly scan folders for `.blend` files while filtering out unwanted files (backup files, text logs, etc.).
-   **Custom Submission Scripts**: Use the built-in default submission script or provide your own custom Python code for advanced submission logic.
-   **Real-time Logging**: Tracks submission status, errors, and system events with a persistent log viewer.
-   **Drag-and-Drop Support**: Simply drag folders into the application to start scanning.

## Installation

### Prerequisites

-   Windows OS
-   Python 3.12+
-   [CGRU/Afanasy](http://cgru.info/) installed and configured

### Setup

1.  Clone the repository:
    ```bash
    git clone https://github.com/your-repo/batch_submitter.git
    cd batch_submitter
    ```

2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3.  (Optional) Create a virtual environment:
    ```bash
    python -m venv .venvBatchSubmit
    .venvBatchSubmit\Scripts\activate
    pip install -r requirements.txt
    ```

## Usage

### Running the Application from Source

To start the application, simply run `main.py`:

```bash
python main.py
```

### Building the Executable

This project includes a PyInstaller spec file (`build.spec`) and a build script (`build.sh` or `build.bat` equivalent) to create a standalone `.exe`.

To build the application:

```bash
pyinstaller build.spec
```

The executable will be generated in the `dist` folder.

## Configuration

-   **Scripts**: The default submission script is located in `scripts/cgru_submitDEFAULT.py`. You can modify this file to change the default submission behavior.
-   **Logs**: Application logs are stored in the `logs` directory.

## Project Structure

-   `main.py`: Entry point for the application, initializes the PyWebView window.
-   `backend.py`: Contains the core logic for file scanning, Blender detection, and job submission.
-   `static/`: Contains the frontend assets (HTML, CSS, JS).
-   `scripts/`: Stores default submission scripts.
-   `logs/`: Stores runtime logs.
