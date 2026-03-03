import http.server
import socketserver
import psutil
import html
import csv
import os
import re
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
            fieldnames = ['process', 'pid', 'cmdline', 'cwd', 'ip', 'port', 'annotation', 'hidden', 'protocol', 'iconurl']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            
            # Write the config row
            writer.writerow({
                'process': CONFIG_PROCESS_NAME,
                'annotation': ','.join(config.get('hide_columns', set()))
            })

            # Filter out transient entries and write server rows
            servers_to_save = [s for s in servers if s.get('status') == 'Running' or s.get('annotation') or s.get('hidden', '').lower() == 'true']
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

def get_favicon_url(link):
    """
    Attempts to find the largest icon URL for a given server link.
    Strategy:
      1. Fetch the page HTML and parse all <link rel="icon|apple-touch-icon|..."> tags.
         Pick the one with the largest declared size (e.g. sizes="192x192").
      2. Fall back to /favicon.ico if no <link> icons found.
      3. Fall back to Google's favicon service as a last resort.
    """
    def _parse_size(sizes_attr):
        """Return the largest dimension integer from a sizes attribute like '32x32 64x64'."""
        best = 0
        for token in sizes_attr.lower().split():
            if 'x' in token:
                try:
                    w, h = token.split('x', 1)
                    best = max(best, int(w), int(h))
                except ValueError:
                    pass
        return best

    def _make_absolute(href, base):
        if href.startswith('http://') or href.startswith('https://'):
            return href
        if href.startswith('//'):
            scheme = base.split('://')[0]
            return f"{scheme}:{href}"
        if href.startswith('/'):
            parts = base.split('://', 1)
            host_part = parts[1].split('/')[0] if len(parts) > 1 else ''
            return f"{parts[0]}://{host_part}{href}"
        return f"{base.rstrip('/')}/{href}"

    try:
        response = requests.get(link, timeout=2, allow_redirects=True, verify=False)
        if response.status_code == 200:
            page_html = response.text
            # Use the final URL after any redirects as the base for resolving relative hrefs
            final_base = response.url

            full_link_tags = re.findall(r'<link\b[^>]*>', page_html, re.IGNORECASE)

            best_url = None
            best_size = -1

            for tag in full_link_tags:
                rel_match = re.search(r'\brel=["\']([^"\']*)["\']', tag, re.IGNORECASE)
                if not rel_match:
                    continue
                rel = rel_match.group(1).lower()
                if not any(k in rel for k in ('icon', 'shortcut', 'apple-touch')):
                    continue

                href_match = re.search(r'\bhref=["\']([^"\']+)["\']', tag, re.IGNORECASE)
                if not href_match:
                    continue
                href = href_match.group(1)

                sizes_match = re.search(r'\bsizes=["\']([^"\']+)["\']', tag, re.IGNORECASE)
                size = _parse_size(sizes_match.group(1)) if sizes_match else 0

                if size > best_size:
                    best_size = size
                    best_url = _make_absolute(href, final_base)

            if best_url:
                return best_url
    except requests.RequestException:
        pass

    # Fallback 1: /favicon.ico
    try:
        favicon_url = f"{link}/favicon.ico"
        response = requests.get(favicon_url, timeout=1, verify=False)
        if response.status_code == 200 and response.content:
            return favicon_url
    except requests.RequestException:
        pass

    # Fallback 2: Google favicon service
    try:
        domain = link.split('//')[1].split(':')[0].split('/')[0]
        google_favicon_url = f"https://www.google.com/s2/favicons?sz=64&domain={domain}"
        response = requests.get(google_favicon_url, timeout=1, verify=False)
        if response.status_code == 200 and response.content:
            return google_favicon_url
    except requests.RequestException:
        pass

    return None

def generate_html(config, servers, request_host):
    """Renders servers as a visual app-picker / home-screen grid."""
    base_host = request_host.split(':')[0] if request_host else "localhost"

    cards_html = ""
    for s in servers:
        status   = s.get('status', 'N/A')
        running  = status == 'Running'
        port     = s.get('port', '')
        protocol = s.get('protocol', '').lower()
        ip       = s.get('ip', '')
        label    = html.escape(s.get('annotation') or s.get('process', 'Unknown'))
        process  = html.escape(s.get('process', ''))
        pid      = html.escape(s.get('pid', ''))
        cmdline  = html.escape(s.get('cmdline', ''))
        cwd      = html.escape(s.get('cwd', ''))

        link_host = "localhost" if ip in ["127.0.0.1", "::1"] else base_host
        favicon_url = None
        primary_link = ""
        proto_badge = ""

        if running:
            if protocol in ('http', 'https'):
                primary_link = f"{protocol}://{link_host}:{port}"
                # Icon fetch always uses localhost — the fetch runs on the server itself.
                # base_host (the request hostname) may not resolve locally.
                fetch_link = f"{protocol}://localhost:{port}"
                if s.get('hidden', '').lower() != 'true':
                    favicon_url = s.get('iconurl') or get_favicon_url(fetch_link)
                    if favicon_url:
                        s['iconurl'] = favicon_url  # stored with localhost, rewritten at render time
                        # Rewrite local/private hosts to base_host so any client on the network can reach the icon.
                        _LOCAL = re.compile(r'^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)$')
                        def _rewrite_host(m):
                            host = m.group(2)
                            return m.group(1) + (base_host if _LOCAL.match(host) else host)
                        favicon_url = re.sub(r'(https?://)([^/:]+)', _rewrite_host, favicon_url, count=1)
                proto_badge = protocol.upper()
            else:
                # Unknown protocol — offer both
                primary_link = f"http://{link_host}:{port}"
                proto_badge = "HTTP/S"

        # Icon: image if we have one, otherwise a coloured initial
        if favicon_url:
            icon_html = f'<img class="app-icon-img" src="{html.escape(favicon_url)}" alt="" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
            # Fallback initial shown via onerror
            initial = label[0].upper() if label else '?'
            icon_html += f'<div class="app-icon-initial" style="display:none">{initial}</div>'
        else:
            initial = label[0].upper() if label else '?'
            icon_html = f'<div class="app-icon-initial">{initial}</div>'

        # Status dot
        dot_class = "dot-running" if running else "dot-offline"
        dot_title = "Running" if running else "Not Running"

        # Tooltip detail
        tooltip = f"Port: {port}"
        if pid:      tooltip += f" | PID: {pid}"
        if process:  tooltip += f" | {process}"
        if cwd and cwd != 'N/A': tooltip += f" | {cwd}"
        if cmdline and cmdline != 'N/A': tooltip += f" | {cmdline}"

        # Protocol links for unknown-protocol running servers
        extra_links = ""
        if running and protocol not in ('http', 'https'):
            http_l  = f"http://{link_host}:{port}"
            https_l = f"https://{link_host}:{port}"
            extra_links = (f'<div class="proto-links">'
                           f'<a href="{http_l}" target="_blank">http</a>'
                           f'<a href="{https_l}" target="_blank">https</a>'
                           f'</div>')

        card_class = "app-card" + ("" if running else " app-card-offline")

        if primary_link and running and protocol in ('http', 'https'):
            card_inner = (f'<a class="{card_class}" href="{primary_link}" target="_blank" title="{html.escape(tooltip)}">'
                          f'  <span class="status-dot {dot_class}" title="{dot_title}"></span>'
                          f'  <div class="app-icon">{icon_html}</div>'
                          f'  <div class="app-label">{label}</div>'
                          f'  <div class="app-port">:{port}'
                          f'    {f"<span class=\'proto-tag\'>{proto_badge}</span>" if proto_badge else ""}'
                          f'  </div>'
                          f'</a>')
        else:
            card_inner = (f'<div class="{card_class}" title="{html.escape(tooltip)}">'
                          f'  <span class="status-dot {dot_class}" title="{dot_title}"></span>'
                          f'  <div class="app-icon">{icon_html}</div>'
                          f'  <div class="app-label">{label}</div>'
                          f'  <div class="app-port">:{port}'
                          f'    {f"<span class=\'proto-tag\'>{proto_badge}</span>" if proto_badge else ""}'
                          f'  </div>'
                          f'  {extra_links}'
                          f'</div>')

        cards_html += card_inner

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>App Launcher</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0f0f13;
    color: #e8e8f0;
    min-height: 100vh;
    padding: 32px 24px 48px;
  }}

  header {{
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 32px;
  }}
  header h1 {{
    font-size: 1.25rem;
    font-weight: 600;
    color: #fff;
    letter-spacing: .02em;
  }}
  header span {{
    font-size: .8rem;
    color: #555;
  }}

  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
    gap: 18px;
  }}

  /* ── shared card base ── */
  .app-card, a.app-card {{
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 20px 10px 16px;
    border-radius: 18px;
    background: #1a1a24;
    border: 1px solid #2a2a38;
    text-decoration: none;
    color: inherit;
    cursor: default;
    transition: transform .15s, background .15s, box-shadow .15s;
    user-select: none;
  }}
  a.app-card {{
    cursor: pointer;
  }}
  a.app-card:hover {{
    transform: translateY(-4px) scale(1.03);
    background: #22223a;
    box-shadow: 0 8px 32px rgba(0,0,0,.45);
    border-color: #4a4aff44;
  }}
  a.app-card:active {{
    transform: translateY(-1px) scale(1.01);
  }}

  .app-card-offline {{
    opacity: .45;
    filter: grayscale(.6);
  }}

  /* ── icon area ── */
  .app-icon {{
    width: 64px;
    height: 64px;
    border-radius: 14px;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #25253a;
    flex-shrink: 0;
  }}
  .app-icon-img {{
    width: 64px;
    height: 64px;
    object-fit: contain;
    border-radius: 14px;
  }}
  .app-icon-initial {{
    width: 64px;
    height: 64px;
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.8rem;
    font-weight: 700;
    color: #fff;
    background: linear-gradient(135deg, #4a4aff, #9b59b6);
    flex-shrink: 0;
  }}

  /* ── labels ── */
  .app-label {{
    font-size: .78rem;
    font-weight: 500;
    text-align: center;
    color: #d0d0e8;
    line-height: 1.3;
    max-width: 100%;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }}
  .app-port {{
    font-size: .7rem;
    color: #666;
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
    justify-content: center;
  }}

  /* ── proto tag ── */
  .proto-tag {{
    font-size: .6rem;
    font-weight: 700;
    letter-spacing: .05em;
    padding: 1px 5px;
    border-radius: 4px;
    background: #2a2a50;
    color: #7878ff;
    text-transform: uppercase;
  }}

  /* ── proto links (no-protocol cards) ── */
  .proto-links {{
    display: flex;
    gap: 6px;
    margin-top: 2px;
  }}
  .proto-links a {{
    font-size: .65rem;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 5px;
    background: #1e1e38;
    border: 1px solid #3a3a5a;
    color: #7878ff;
    text-decoration: none;
    cursor: pointer;
  }}
  .proto-links a:hover {{ background: #2a2a50; }}

  /* ── status dot ── */
  .status-dot {{
    position: absolute;
    top: 10px;
    right: 10px;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }}
  .dot-running  {{ background: #22c55e; box-shadow: 0 0 6px #22c55e88; }}
  .dot-offline  {{ background: #444; }}

  /* ── hint ── */
  .hint {{
    margin-top: 40px;
    font-size: .72rem;
    color: #333;
    text-align: center;
  }}
  .hint code {{ color: #555; }}
</style>
</head>
<body>
<header>
  <h1>App Launcher</h1>
  <span>edit <code>{ANNOTATIONS_FILE}</code> to annotate &amp; hide entries</span>
</header>
<div class="grid">
{cards_html}
</div>
<p class="hint">Tip: set <code>annotation</code> for a friendly name · set <code>protocol</code> to http or https to make a card clickable · set <code>hidden</code> to true to suppress an entry</p>
</body>
</html>"""

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
        try:
            self.wfile.write(html_content.encode('utf-8'))
        except (ConnectionAbortedError, BrokenPipeError) as e:
            print(f"[{self.log_date_time_string()}] Connection dropped by {self.client_address[0]}: {e}")

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
