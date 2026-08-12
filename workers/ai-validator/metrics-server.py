#!/usr/bin/env python3
"""
AI Validator - Metrics Server
Simple REST API and dashboard server for metrics visualization
"""

import json
import sys
from pathlib import Path
from datetime import datetime

try:
    from flask import Flask, jsonify, render_template_string, send_file
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

app = Flask(__name__)

class MetricsServer:
    def __init__(self, reports_dir: str = "."):
        self.reports_dir = Path(reports_dir)
        self.metrics = None
        self.load_metrics()
    
    def load_metrics(self):
        """Load metrics from metrics.json if it exists."""
        metrics_file = self.reports_dir / "metrics.json"
        if metrics_file.exists():
            try:
                with open(metrics_file, 'r') as f:
                    self.metrics = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load metrics.json: {e}")
                self.metrics = {"error": "metrics not found"}
        else:
            self.metrics = {"error": "metrics not found"}
    
    def get_summary(self):
        """Get summary metrics."""
        if not self.metrics or "error" in self.metrics:
            return None
        
        return {
            "aggregated_at": self.metrics.get("aggregated_at", ""),
            "workspace_count": len(self.metrics.get("workspaces", [])),
            "summary": self.metrics.get("summary", {})
        }

# Create server instance
server = None

@app.route('/')
def index():
    """Serve dashboard HTML."""
    summary = server.get_summary()
    if not summary:
        return "Metrics not available. Run metrics-collector.py first.", 404
    
    html = generate_dashboard_html(summary)
    return html

@app.route('/api/metrics')
def api_metrics():
    """JSON API for metrics."""
    if not server.metrics or "error" in server.metrics:
        return jsonify({"error": "Metrics not available"}), 404
    return jsonify(server.metrics)

@app.route('/api/summary')
def api_summary():
    """JSON API for summary."""
    summary = server.get_summary()
    if not summary:
        return jsonify({"error": "Metrics not available"}), 404
    return jsonify(summary)

@app.route('/api/workspaces')
def api_workspaces():
    """JSON API for workspaces."""
    if not server.metrics or "error" in server.metrics:
        return jsonify({"error": "Metrics not available"}), 404
    
    workspaces = server.metrics.get("workspaces", [])
    return jsonify({"count": len(workspaces), "workspaces": workspaces})

@app.route('/metrics.json')
def get_metrics_json():
    """Serve metrics.json file."""
    metrics_file = server.reports_dir / "metrics.json"
    if metrics_file.exists():
        return send_file(metrics_file, mimetype='application/json')
    return jsonify({"error": "Metrics file not found"}), 404

def generate_dashboard_html(summary):
    """Generate dashboard HTML."""
    ws_count = summary.get("workspace_count", 0)
    total_files = summary.get("summary", {}).get("total_files", 0)
    total_issues = summary.get("summary", {}).get("total_issues", 0)
    
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Validator - Live Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container { max-width: 1200px; margin: 0 auto; }
        
        header {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 10px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
        }
        
        h1 { color: #667eea; margin-bottom: 10px; }
        .subtitle { color: #666; font-size: 14px; }
        
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .metric-card {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
            border-left: 4px solid #667eea;
        }
        
        .metric-value {
            font-size: 36px;
            font-weight: bold;
            color: #667eea;
            margin: 10px 0;
        }
        
        .metric-label {
            color: #666;
            font-size: 14px;
        }
        
        .section {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 10px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
        }
        
        h2 { color: #667eea; margin-bottom: 20px; }
        
        .api-docs {
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            padding: 15px;
            margin: 20px 0;
            border-radius: 4px;
            font-family: monospace;
            font-size: 13px;
        }
        
        .endpoint {
            color: #667eea;
            font-weight: bold;
            margin: 10px 0 5px 0;
        }
        
        .footer {
            color: rgba(255, 255, 255, 0.8);
            text-align: center;
            margin-top: 30px;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 AI Validator - Live Dashboard</h1>
            <p class="subtitle">Real-time metrics monitoring and reporting</p>
        </header>
        
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Workspaces</div>
                <div class="metric-value">""" + str(ws_count) + """</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Files Analyzed</div>
                <div class="metric-value">""" + str(total_files) + """</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Issues Found</div>
                <div class="metric-value">""" + str(total_issues) + """</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Status</div>
                <div class="metric-value" style="color: #27ae60;">✓ OK</div>
            </div>
        </div>
        
        <div class="section">
            <h2>🔌 REST API Endpoints</h2>
            <p>Access metrics programmatically:</p>
            
            <div class="api-docs">
                <div class="endpoint">GET /api/metrics</div>
                <p>Full metrics data (JSON)</p>
            </div>
            
            <div class="api-docs">
                <div class="endpoint">GET /api/summary</div>
                <p>Summary metrics only</p>
            </div>
            
            <div class="api-docs">
                <div class="endpoint">GET /api/workspaces</div>
                <p>List all workspaces</p>
            </div>
            
            <div class="api-docs">
                <div class="endpoint">GET /metrics.json</div>
                <p>Raw metrics.json file</p>
            </div>
        </div>
        
        <div class="section">
            <h2>📡 Usage Examples</h2>
            <pre style="background: #f8f9fa; padding: 15px; border-radius: 4px; overflow-x: auto;">
# Get metrics
curl http://localhost:5000/api/metrics

# Get summary only
curl http://localhost:5000/api/summary

# Get workspaces
curl http://localhost:5000/api/workspaces

# Use with jq for pretty-printing
curl http://localhost:5000/api/metrics | jq '.'
            </pre>
        </div>
        
        <div class="footer">
            <p>AI Validator v2.0 | Metrics Server</p>
        </div>
    </div>
</body>
</html>"""

def main():
    global server
    
    if not HAS_FLASK:
        print("Error: Flask is not installed.")
        print("Install it with: pip install flask")
        sys.exit(1)
    
    reports_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    
    # Initialize server
    server = MetricsServer(reports_dir)
    
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║         AI Validator - Metrics Server Starting               ║
╚═══════════════════════════════════════════════════════════════╝

📂 Reports Directory: {reports_dir}
🌐 Dashboard: http://localhost:{port}

📡 REST API Endpoints:
  • http://localhost:{port}/api/metrics
  • http://localhost:{port}/api/summary
  • http://localhost:{port}/api/workspaces
  • http://localhost:{port}/metrics.json

⚠️  Press Ctrl+C to stop the server
""")
    
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    main()
