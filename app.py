import subprocess
import http.server
import socketserver

PORT = 80

def get_listening_ports():
    """Queries Windows for listening ports and their associated process names."""
    # 1. Get process IDs and names using tasklist
    try:
        task_output = subprocess.check_output(['tasklist', '/fo', 'csv', '/nh']).decode('utf-8', errors='ignore')
    except subprocess.CalledProcessError:
        task_output = ""

    pid_to_name = {}
    for line in task_output.splitlines():
        if not line.strip():
            continue
        parts = line.replace('"', '').split(',')
        if len(parts) >= 2:
            pid_to_name[parts[1]] = parts[0]

    # 2. Get active listening ports using netstat
    try:
        netstat_output = subprocess.check_output(['netstat', '-ano']).decode('utf-8', errors='ignore')
    except subprocess.CalledProcessError:
        netstat_output = ""
    
    servers = []
    seen = set()

    for line in netstat_output.splitlines():
        if 'LISTENING' in line and 'TCP' in line:
            parts = line.split()
            # netstat format: Protocol, Local Address, Foreign Address, State, PID
            if len(parts) >= 5:
                local_addr = parts[1]
                pid = parts[4]
                
                if ':' in local_addr:
                    ip, port = local_addr.rsplit(':', 1)
                    
                    # Avoid duplicates (e.g., listening on both IPv4 and IPv6)
                    identifier = (port, pid)
                    if identifier not in seen:
                        seen.add(identifier)
                        process_name = pid_to_name.get(pid, "System/Unknown")
                        servers.append({
                            'ip': ip, 
                            'port': port, 
                            'pid': pid, 
                            'process': process_name
                        })
            
    # Sort by port number for better readability
    return sorted(servers, key=lambda x: int(x['port']))

def generate_html(servers):
    """Compiles the gathered data into an HTML page with links."""
    html = """
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
        </style>
    </head>
    <body>
        <h1>Active Listening Ports</h1>
        <p>This list is generated dynamically. Refresh the page to see real-time updates.</p>
        <table>
            <tr>
                <th>Process Name</th>
                <th>PID</th>
                <th>Local IP Bind</th>
                <th>Port / Link</th>
            </tr>
    """
    
    for s in servers:
        ip = s['ip']
        port = s['port']
        
        # If bound to 0.0.0.0 or [::], format the link to use localhost so it resolves in the browser
        link_ip = "localhost" if ip in ["0.0.0.0", "[::]", "127.0.0.1", "::1"] else ip
        
        # Guess protocol (basic assumption for links)
        protocol = "https" if port == "443" else "http"
        link = f"{protocol}://{link_ip}:{port}"
        
        html += f"""
            <tr>
                <td>{s['process']}</td>
                <td>{s['pid']}</td>
                <td>{ip}</td>
                <td><a href="{link}" target="_blank">Port {port}</a></td>
            </tr>
        """
        
    html += """
        </table>
    </body>
    </html>
    """
    return html

class DynamicServerHandler(http.server.BaseHTTPRequestHandler):
    """Custom request handler that generates the HTML on every GET request."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        # Dynamically fetch ports and generate HTML on every page load
        servers = get_listening_ports()
        html_content = generate_html(servers)
        
        self.wfile.write(html_content.encode('utf-8'))

    # Suppress default logging to keep the console clean (optional)
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] Served dynamic list to {self.client_address[0]}")

if __name__ == "__main__":
    try:
        # Create the server
        with socketserver.TCPServer(("", PORT), DynamicServerHandler) as httpd:
            print(f"Starting server... Listening on port {PORT}.")
            print(f"Open http://localhost/ in your web browser.")
            print("Press Ctrl+C to stop the server.")
            httpd.serve_forever()
    except PermissionError:
        print(f"ERROR: Permission denied. You must run this script as an Administrator to bind to port {PORT}.")
    except OSError as e:
        if e.errno == 10048:
            print(f"ERROR: Port {PORT} is already in use. If IIS, Apache, or another service is running on port 80, you will need to stop it or change the PORT variable in this script.")
        else:
            print(f"OS Error: {e}")
