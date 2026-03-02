import http.server
import socketserver
import psutil
import html
import csv
import os

PORT = 80
ANNOTATIONS_FILE = 'annotations.csv'

def load_annotations():
    """Loads annotations from the CSV file, keyed by PID."""
    annotations = {}
    if not os.path.exists(ANNOTATIONS_FILE):
        return annotations
    try:
        with open(ANNOTATIONS_FILE, mode='r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                # Use a composite key of pid and port for better accuracy
                key = (row['pid'], row['port'])
                annotations[key] = row.get('annotation', '')
    except (IOError, csv.Error) as e:
        print(f"Error loading annotations: {e}")
    return annotations

def save_annotations(servers):
    """Saves the current server list (with annotations) to the CSV file."""
    try:
        with open(ANNOTATIONS_FILE, mode='w', newline='', encoding='utf-8') as csvfile:
            # Added 'annotation' to fieldnames
            fieldnames = ['process', 'pid', 'cmdline', 'ip', 'port', 'annotation']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(servers)
    except (IOError, csv.Error) as e:
        print(f"Error saving annotations: {e}")

def get_listening_ports():
    """Queries the system for listening ports and their associated process details."""
    servers = []
    seen = set()
    
    # Load existing annotations first
    annotations = load_annotations()

    for conn in psutil.net_connections(kind='inet'):
        if conn.status == psutil.CONN_LISTEN and conn.laddr.port and conn.pid:
            port_str = str(conn.laddr.port)
            pid_str = str(conn.pid)
            
            # Use a composite identifier to avoid duplicates
            identifier = (port_str, pid_str)
            if identifier not in seen:
                seen.add(identifier)
                
                try:
                    p = psutil.Process(conn.pid)
                    process_name = p.name()
                    # Join command-line arguments into a string, escaping for HTML
                    cmdline = ' '.join(map(html.escape, p.cmdline()))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    process_name = "System/Unknown"
                    cmdline = "N/A"

                # Get annotation from the loaded data
                annotation = annotations.get(identifier, "")

                servers.append({
                    'ip': conn.laddr.ip,
                    'port': port_str,
                    'pid': pid_str,
                    'process': process_name,
                    'cmdline': cmdline,
                    'annotation': annotation # Add annotation to the server dict
                })

    # Sort by port number for better readability
    return sorted(servers, key=lambda x: int(x['port']))

def generate_html(servers, request_host):
    """Compiles the gathered data into an HTML page with links."""
    
    # Strip the port from the Host header if it exists (e.g., '192.168.1.5:80' -> '192.168.1.5')
    base_host = request_host.split(':')[0] if request_host else "localhost"

    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Dynamic Windows Server Port List</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f4f9; color: #333; padding: 20px; }
            h1 { color: #005a9e; }
            table { border-collapse: collapse; width: 100%; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
            th { background-color: #0078d7; color: white; }
            tr:nth-child(even) { background-color: #f2f2f2; }
            tr:hover { background-color: #e1f0fa; }
            a { color: #0078d7; text-decoration: none; font-weight: bold; }
            a:hover { text-decoration: underline; }
            .cmdline { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: 'Courier New', monospace; font-size: 0.9em; color: #555; }
            .annotation { max-width: 250px; }
        </style>
    </head>
    <body>
        <h1>Active Listening Ports</h1>
        <p>This list is generated dynamically. Annotations are read from and saved to <code>annotations.csv</code> in the same directory.</p>
        <table>
            <tr>
                <th>Process Name</th>
                <th>PID</th>
                <th>Arguments</th>
                <th>Annotation</th>
                <th>Local IP Bind</th>
                <th>Port / Link</th>
            </tr>
    """
    
    for s in servers:
        ip = s['ip']
        port = s['port']
        
        # If the service is explicitly bound ONLY to the local loopback, force the link to localhost 
        # because it won't be accessible remotely anyway. Otherwise, use the request's hostname/IP.
        if ip in ["127.0.0.1", "::1"]:
            link_host = "localhost"
        else:
            link_host = base_host
            
        protocol = "https" if port == "443" else "http"
        link = f"{protocol}://{link_host}:{port}"
        
        # Display the annotation, escaping it for safety
        annotation_display = html.escape(s.get('annotation', ''))
        
        html_content += f"""
            <tr>
                <td>{s['process']}</td>
                <td>{s['pid']}</td>
                <td class="cmdline" title="{s['cmdline']}">{s['cmdline']}</td>
                <td class="annotation">{annotation_display}</td>
                <td>{ip}</td>
                <td><a href="{link}" target="_blank">Port {port}</a></td>
            </tr>
        """
        
    html_content += """
        </table>
    </body>
    </html>
    """
    return html_content

class DynamicServerHandler(http.server.BaseHTTPRequestHandler):
    """Custom request handler that generates the HTML on every GET request."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        # Capture the 'Host' header sent by the client's browser
        host_header = self.headers.get('Host', 'localhost')
        
        # 1. Get current server data, which now includes loaded annotations
        servers = get_listening_ports()
        
        # 2. Save the potentially updated list back to CSV
        save_annotations(servers)
        
        # 3. Generate and serve the HTML
        html_content = generate_html(servers, host_header)
        
        self.wfile.write(html_content.encode('utf-8'))

    # Suppress default logging to keep the console clean (optional)
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] Served dynamic list to {self.client_address[0]}")

if __name__ == "__main__":
    try:
        # Check for psutil and provide a helpful message if it's missing
        import psutil
    except ImportError:
        print("ERROR: The 'psutil' library is not installed.")
        print("Please run `setup.bat` or install dependencies manually via `pip install -r requirements.txt`.")
        exit(1)

    try:
        # Create the server
        with socketserver.TCPServer(("", PORT), DynamicServerHandler) as httpd:
            print(f"Starting server... Listening on port {PORT}.")
            print("Use `run.bat` to start the server or run `python app.py` manually.")
            print(f"Open http://localhost/ in your web browser.")
            print("Press Ctrl+C to stop the server.")
            httpd.serve_forever()
    except PermissionError:
        print(f"\nERROR: Permission denied to bind to port {PORT}. Try running `run.bat` as an Administrator.")
    except OSError as e:
        if e.errno == 10048:
            print(f"\nERROR: Port {PORT} is already in use. Stop the conflicting service or change the PORT variable in the script.")
        else:
            print(f"\nOS Error: {e}")
