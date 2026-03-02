# Dynamic Server Port List

A simple Python-based web server that provides a dynamically generated HTML page showing all TCP ports currently in a LISTENING state on the local machine.

It's especially useful for developers, testers, or system administrators who need a quick, browser-based view of what services are running and which ports they are using.

## Features

- **Real-time Port Scanning**: The list is generated on each page refresh.
- **Process Information**: Displays the Process ID (PID), Process Name, and command-line arguments for the service listening on each port.
- **Clickable Links**: Automatically generates `http://` or `https://` links to open the service in a new tab.
- **Intelligent Linking**: Uses `localhost` for loopback-only services and the server's IP/hostname for others.
- **No External Dependencies (almost!)**: Runs with standard Python libraries, plus `psutil`.

## Prerequisites

- Python 3.x
- `psutil` library

## Installation & Usage

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd server-port-list
    ```

2.  **Install the required dependency:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the server:**
    ```bash
    python app.py
    ```
    > **Note**: On Windows, you may need to run this as an Administrator to allow the server to bind to port 80.

4.  **Open your browser:**
    Navigate to `http://localhost/` to view the list.

## How It Works

The script uses `psutil` to efficiently query the system's network connections and running processes. It then starts a lightweight web server on port 80 using Python's built-in `http.server`. When a request is received, it generates an HTML page on-the-fly with the collected port and process information.