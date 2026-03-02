import http.server
import socketserver
import psutil
import html
import csv
import os

PORT = 80
ANNOTATIONS_FILE = 'annotations.csv'
# Define a stable key for identifying a process across runs
CSV_KEY_FIELDS = ['process', 'port', 'cmdline', 'cwd']

def get_key(server_dict):
    """Creates a stable key tuple from a server dictionary."""
    return tuple(server_dict.get(field, 'N/A') for field in CSV_KEY_FIELDS)

def load_servers_from_csv():
    """Loads all server records from the CSV file into a dictionary keyed by a stable identifier."""
    servers = {}
    if not os.path.exists(ANNOTATIONS_FILE):
        return servers
    try:
        with open(ANNOTATIONS_FILE, mode='r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                servers[get_key(row)] = row
    except (IOError, csv.Error) as e:
        print(f"Error loading annotations: {e}")
    return servers

def save_servers_to_csv(servers):
    """Saves servers that are running or have an annotation to the CSV file."""
    try:
        # Filter out transient entries that are not running and have no annotation
        servers_to_save = [s for s in servers if s.get('status') == 'Running' or s.get('annotation')]
        
        with open(ANNOTATIONS_FILE, mode='w', newline='', encoding='utf-8') as csvfile:
            # The full set of fields to be written to the CSV
            fieldnames = ['process', 'pid', 'cmdline', 'cwd', 'ip', 'port', 'annotation']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(servers_to_save)
    except (IOError, csv.Error) as e:
        print(f"Error saving annotations: {e}")

def get_running_servers():
    """Queries the system for currently listening ports and returns a list of dictionaries."""
    servers = []
    seen = set()

    for conn in psutil.net_connections(kind='inet'):
        if conn.status == psutil.CONN_LISTEN and conn.laddr.port and conn.pid:
            identifier = (conn.pid, conn.laddr.port)
            if identifier not in seen:
                seen.add(identifier)
                
                process_name, cmdline, cwd = "System/Unknown", "N/A", "N/A"
                try:
                    p = psutil.Process(conn.pid)
                    process_name = p.name()
                    cmdline = ' '.join(p.cmdline())
                    try:
                        cwd = p.cwd()
                    except psutil.AccessDenied:
                        cwd = "Access Denied"
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

                servers.append({
                    'ip': conn.laddr.ip,
                    'port': str(conn.laddr.port),
                    'pid': str(conn.pid),
                    'process': process_name,
                    'cmdline': cmdline,
                    'cwd': cwd,
                })
    return servers

def generate_html(servers, request_host):
    """Compiles the gathered data into an HTML page."""
    base_host = request_host.split(':')[0] if request_host else "localhost"

    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Dynamic Windows Server Port List</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f4f9; color: #333; padding: 20px; }
            h1 { color: #005a9e; }
            table { table-layout: fixed; border-collapse: collapse; width: 100%; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            th, td { border: 1px solid #ddd; padding: 12px; text-align: left; word-wrap: break-word; }
            th { background-color: #0078d7; color: white; }
            tr:nth-child(even) { background-color: #f2f2f2; }
            tr:hover { background-color: #e1f0fa; }
            .not-running { color: #888; background-color: #fafafa; }
            .not-running a { color: #888; pointer-events: none; }
            a { color: #0078d7; text-decoration: none; font-weight: bold; }
            a:hover { text-decoration: underline; }
            .ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        </style>
    </head>
    <body>
        <h1>Active Listening Ports</h1>
        <p>Annotations are preserved for processes that are not currently running.</p>
        <table>
            <tr>
                <th style="width: 10%;">Status</th>
                <th style="width: 12%;">Process Name</th>
                <th style="width: 5%;">PID</th>
                <th style="width: 20%;">Arguments</th>
                <th style="width: 15%;">Working Dir</th>
                <th style="width: 15%;">Annotation</th>
                <th style="width: 8%;">Local IP</th>
                <th style="width: 8%;">Port</th>
            </tr>
    """
    
    for s in servers:
        status = s.get('status', 'N/A')
        row_class = 'not-running' if status == 'Not Running' else ''
        
        # Escape data for HTML safety
        process = html.escape(s.get('process', ''))
        pid = html.escape(s.get('pid', ''))
        cmdline = html.escape(s.get('cmdline', ''))
        cwd = html.escape(s.get('cwd', ''))
        annotation = html.escape(s.get('annotation', ''))
        ip = html.escape(s.get('ip', ''))
        port = html.escape(s.get('port', ''))

        link = "#"
        if status == 'Running':
            link_host = "localhost" if ip in ["127.0.0.1", "::1"] else base_host
            protocol = "https" if port == "443" else "http"
            link = f"{protocol}://{link_host}:{port}"

        html_content += f"""
            <tr class="{row_class}">
                <td>{status}</td>
                <td class="ellipsis" title="{process}">{process}</td>
                <td>{pid}</td>
                <td class="ellipsis" title="{cmdline}">{cmdline}</td>
                <td class="ellipsis" title="{cwd}">{cwd}</td>
                <td>{annotation}</td>
                <td>{ip}</td>
                <td><a href="{link}" target="_blank">{port}</a></td>
            </tr>
        """
        
    html_content += "</table></body></html>"
    return html_content

class DynamicServerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        host_header = self.headers.get('Host', 'localhost')
        
        # 1. Load all historical server data from CSV
        all_known_servers = load_servers_from_csv()
        
        # 2. Mark all of them as 'Not Running' initially
        for key in all_known_servers:
            all_known_servers[key]['status'] = 'Not Running'
            # PID is transient, clear it for non-running processes
            all_known_servers[key]['pid'] = '' 

        # 3. Get fresh data for currently running servers
        running_servers = get_running_servers()
        
        # 4. Merge running data into the historical data
        for r_server in running_servers:
            key = get_key(r_server)
            if key in all_known_servers:
                # Update existing entry with fresh (running) data
                all_known_servers[key].update(r_server)
                all_known_servers[key]['status'] = 'Running'
            else:
                # It's a new process not seen before
                r_server['status'] = 'Running'
                all_known_servers[key] = r_server
        
        # 5. Convert back to a list
        final_server_list = list(all_known_servers.values())
        
        # 6. Sort the list: annotated first, then by port number
        final_server_list.sort(key=lambda s: (not s.get('annotation'), int(s.get('port', 0))))

        # 7. Save the updated list back to the CSV
        save_servers_to_csv(final_server_list)
        
        # 8. Generate and serve the HTML
        html_content = generate_html(final_server_list, host_header)
        self.wfile.write(html_content.encode('utf-8'))

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] Served dynamic list to {self.client_address[0]}")

if __name__ == "__main__":
    if 'psutil' not in globals():
        print("ERROR: The 'psutil' library is not installed.")
        print("Please run `setup.bat` or install dependencies manually via `pip install -r requirements.txt`.")
        exit(1)

    try:
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
