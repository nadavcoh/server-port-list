import http.server
import socketserver
import psutil
import html
import csv
import os

PORT = 80
ANNOTATIONS_FILE = 'annotations.csv'
CSV_KEY_FIELDS = ['process', 'port', 'cmdline', 'cwd']
CONFIG_PROCESS_NAME = '##CONFIG##'

def get_key(server_dict):
    """Creates a stable key tuple from a server dictionary."""
    return tuple(server_dict.get(field, 'N/A') for field in CSV_KEY_FIELDS)

def load_config_and_servers_from_csv():
    """Loads config and server records from the CSV file."""
    config = {'hide_columns': set()}
    servers = {}
    if not os.path.exists(ANNOTATIONS_FILE):
        return config, servers
    try:
        with open(ANNOTATIONS_FILE, mode='r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row.get('process') == CONFIG_PROCESS_NAME:
                    # The 'annotation' field of the config row holds comma-separated column names to hide
                    config['hide_columns'] = {col.strip().lower() for col in row.get('annotation', '').split(',')}
                else:
                    servers[get_key(row)] = row
    except (IOError, csv.Error) as e:
        print(f"Error loading CSV data: {e}")
    return config, servers

def save_servers_to_csv(config, servers):
    """Saves config and servers (running or annotated) to the CSV file."""
    try:
        with open(ANNOTATIONS_FILE, mode='w', newline='', encoding='utf-8') as csvfile:
            # Add new control fields to the header
            fieldnames = ['process', 'pid', 'cmdline', 'cwd', 'ip', 'port', 'annotation', 'hidden', 'protocol']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            
            # Write the config row
            writer.writerow({
                'process': CONFIG_PROCESS_NAME,
                'annotation': ','.join(config.get('hide_columns', set()))
            })

            # Filter out transient entries and write server rows
            servers_to_save = [s for s in servers if s.get('status') == 'Running' or s.get('annotation')]
            writer.writerows(servers_to_save)
    except (IOError, csv.Error) as e:
        print(f"Error saving CSV data: {e}")

def get_running_servers():
    """Queries the system for currently listening ports."""
    # (This function is unchanged)
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
                    cwd = p.cwd()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
                servers.append({'ip': conn.laddr.ip, 'port': str(conn.laddr.port), 'pid': str(conn.pid), 'process': process_name, 'cmdline': cmdline, 'cwd': cwd})
    return servers

def generate_html(config, servers, request_host):
    """Compiles the gathered data into an HTML page, respecting display settings."""
    base_host = request_host.split(':')[0] if request_host else "localhost"
    hidden_cols = config.get('hide_columns', set())

    # Define all possible columns
    columns = {
        'status': ('Status', '10%'), 'process': ('Process Name', '12%'), 'pid': ('PID', '5%'),
        'arguments': ('Arguments', '20%'), 'working dir': ('Working Dir', '15%'),
        'annotation': ('Annotation', '15%'), 'local ip': ('Local IP', '8%'),
        'port / links': ('Port / Links', '15%')
    }
    
    html_content = f"""
    <!DOCTYPE html><html><head><title>Dynamic Windows Server Port List</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f4f9; color: #333; padding: 20px; }}
        h1 {{ color: #005a9e; }} p {{ font-size: 0.9em; color: #555; }}
        table {{ table-layout: fixed; border-collapse: collapse; width: 100%; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; word-wrap: break-word; }}
        th {{ background-color: #0078d7; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }} tr:hover {{ background-color: #e1f0fa; }}
        .not-running {{ color: #888; background-color: #fafafa; }}
        .not-running a {{ color: #aaa; pointer-events: none; text-decoration: line-through; }}
        a {{ color: #0078d7; text-decoration: none; font-weight: bold; }} a:hover {{ text-decoration: underline; }}
        .ellipsis {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    </style></head><body>
    <h1>Active Listening Ports</h1>
    <p>Control visibility and links by editing <code>{ANNOTATIONS_FILE}</code>.
       To hide columns, add a row with '##CONFIG##' in the 'process' column and a comma-separated list of column names in the 'annotation' field.
       Valid columns are: {', '.join(columns.keys())}.
    </p>
    <table><tr>
    """
    
    # Generate table headers, respecting hidden columns
    for key, (name, width) in columns.items():
        if key.lower() not in hidden_cols:
            html_content += f'<th style="width: {width};">{name}</th>'
    html_content += "</tr>"

    for s in servers:
        status = s.get('status', 'N/A')
        row_class = 'not-running' if status == 'Not Running' else ''
        
        # Determine the link protocol
        preferred_protocol = s.get('protocol')
        if not preferred_protocol or preferred_protocol.lower() not in ['http', 'https']:
            preferred_protocol = "https" if s.get('port') == "443" else "http"
        
        links_html = html.escape(s.get('port', ''))
        if status == 'Running':
            link_host = "localhost" if s.get('ip') in ["127.0.0.1", "::1"] else base_host
            link = f"{preferred_protocol}://{link_host}:{s.get('port')}"
            links_html = f'<a href="{link}" target="_blank">{s.get("port")}</a> ({preferred_protocol})'
            
        # Data for each cell, pre-escaped
        cell_data = {
            'status': html.escape(status), 'process': html.escape(s.get('process', '')),
            'pid': html.escape(s.get('pid', '')), 'arguments': html.escape(s.get('cmdline', '')),
            'working dir': html.escape(s.get('cwd', '')), 'annotation': html.escape(s.get('annotation', '')),
            'local ip': html.escape(s.get('ip', '')), 'port / links': links_html
        }

        html_content += f'<tr class="{row_class}">'
        for key, _ in columns.items():
            if key.lower() not in hidden_cols:
                # Use a title attribute for ellipsis, and render the data
                title = cell_data[key] if key not in ['port / links'] else ''
                html_content += f'<td class="ellipsis" title="{title}">{cell_data[key]}</td>'
        html_content += "</tr>"
        
    html_content += "</table></body></html>"
    return html_content

class DynamicServerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        host_header = self.headers.get('Host', 'localhost')
        
        config, all_known_servers = load_config_and_servers_from_csv()
        
        for key in all_known_servers:
            all_known_servers[key]['status'] = 'Not Running'
            all_known_servers[key]['pid'] = '' 

        running_servers = get_running_servers()
        
        for r_server in running_servers:
            key = get_key(r_server)
            if key in all_known_servers:
                all_known_servers[key].update(r_server)
                all_known_servers[key]['status'] = 'Running'
            else:
                r_server['status'] = 'Running'
                all_known_servers[key] = r_server
        
        # Filter out hidden rows BEFORE sorting and displaying
        final_server_list = [s for s in all_known_servers.values() if s.get('hidden', '').lower() != 'true']
        
        final_server_list.sort(key=lambda s: (not s.get('annotation'), int(s.get('port', 0))))

        save_servers_to_csv(config, all_known_servers.values())
        
        html_content = generate_html(config, final_server_list, host_header)
        self.wfile.write(html_content.encode('utf-8'))

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] Served dynamic list to {self.client_address[0]}")

if __name__ == "__main__":
    if 'psutil' not in globals():
        print("ERROR: The 'psutil' library is not installed.")
        exit(1)

    try:
        with socketserver.TCPServer(("", PORT), DynamicServerHandler) as httpd:
            print(f"Starting server... Listening on port {PORT}.")
            print(f"Open http://localhost/ in your web browser.")
            print("Press Ctrl+C to stop the server.")
            httpd.serve_forever()
    except PermissionError:
        print(f"\nERROR: Permission denied to bind to port {PORT}.")
    except OSError as e:
        if e.errno == 10048:
            print(f"\nERROR: Port {PORT} is already in use.")
        else:
            print(f"\nOS Error: {e}")
