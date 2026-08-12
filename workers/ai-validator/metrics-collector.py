#!/usr/bin/env python3
"""
AI Validator - Metrics Collector & Analysis
Extracts metrics from validation reports for dashboard visualization
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

class MetricsCollector:
    def __init__(self, reports_dir: str):
        self.reports_dir = Path(reports_dir)
        self.metrics = {
            "aggregated_at": datetime.utcnow().isoformat() + "Z",
            "workspaces": [],
            "summary": {
                "total_files": 0,
                "total_issues": 0,
                "by_language": {}
            }
        }

    def collect_from_workspace(self, workspace_path: Path) -> Dict[str, Any]:
        """Extract metrics from a single workspace."""
        ws_name = workspace_path.name
        metrics = {
            "name": ws_name,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "files": {
                "python": 0,
                "javascript": 0,
                "typescript": 0,
                "bash": 0,
                "html": 0,
                "css": 0,
                "php": 0,
                "c": 0,
                "cpp": 0,
            },
            "issues": {
                "python": 0,
                "javascript": 0,
                "bash": 0,
                "security": 0,
                "html": 0,
            },
            "status": "ok"
        }

        # Read metrics.txt
        metrics_file = workspace_path / "metrics.txt"
        if metrics_file.exists():
            with open(metrics_file, 'r') as f:
                content = f.read()
                for line in content.split('\n'):
                    if 'Python files:' in line:
                        try:
                            metrics['files']['python'] = int(line.split(':')[1].strip())
                        except:
                            pass
                    elif 'JavaScript files:' in line:
                        try:
                            metrics['files']['javascript'] = int(line.split(':')[1].strip())
                        except:
                            pass
                    elif 'Bash scripts:' in line:
                        try:
                            metrics['files']['bash'] = int(line.split(':')[1].strip())
                        except:
                            pass
                    elif 'HTML files:' in line:
                        try:
                            metrics['files']['html'] = int(line.split(':')[1].strip())
                        except:
                            pass
                    elif 'CSS files:' in line:
                        try:
                            metrics['files']['css'] = int(line.split(':')[1].strip())
                        except:
                            pass

        # Count issues from JSON reports
        for report_file in workspace_path.glob('*-*.json'):
            try:
                with open(report_file, 'r') as f:
                    data = json.load(f)
                    
                    # Count issues based on report type
                    if 'python-ruff' in report_file.name:
                        if isinstance(data, list):
                            metrics['issues']['python'] += len(data)
                    elif 'javascript-eslint' in report_file.name:
                        if isinstance(data, dict) and isinstance(data.get('results', []), list):
                            for result in data.get('results', []):
                                metrics['issues']['javascript'] += len(result.get('messages', []))
                    elif 'bash-shellcheck' in report_file.name:
                        if isinstance(data, list):
                            metrics['issues']['bash'] += len(data)
                    elif 'security-bandit' in report_file.name:
                        if isinstance(data, dict):
                            metrics['issues']['security'] += len(data.get('results', []))
                    elif 'html-hint' in report_file.name:
                        if isinstance(data, list):
                            metrics['issues']['html'] += len(data)
            except Exception as e:
                print(f"Error reading {report_file}: {e}", file=sys.stderr)

        return metrics

    def collect_all(self) -> Dict[str, Any]:
        """Collect metrics from all workspaces."""
        if not self.reports_dir.exists():
            print(f"Error: Reports directory not found: {self.reports_dir}", file=sys.stderr)
            return self.metrics

        for workspace_path in sorted(self.reports_dir.iterdir()):
            if workspace_path.is_dir() and not workspace_path.name.startswith('.'):
                ws_metrics = self.collect_from_workspace(workspace_path)
                self.metrics['workspaces'].append(ws_metrics)
                
                # Update summary
                for lang, count in ws_metrics['files'].items():
                    self.metrics['summary']['total_files'] += count
                    if lang not in self.metrics['summary']['by_language']:
                        self.metrics['summary']['by_language'][lang] = 0
                    self.metrics['summary']['by_language'][lang] += count
                
                # Sum issues
                for issue_type, count in ws_metrics['issues'].items():
                    self.metrics['summary']['total_issues'] += count

        return self.metrics

    def save_json(self, output_file: str) -> None:
        """Save metrics to JSON file."""
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(self.metrics, f, indent=2)
        
        print(f"✓ Metrics saved to: {output_file}")

    def save_html(self, output_file: str) -> None:
        """Generate HTML report from metrics."""
        html_content = self._generate_html()
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            f.write(html_content)
        
        print(f"✓ HTML report saved to: {output_file}")

    def save_csv(self, output_file: str) -> None:
        """Save metrics to CSV file."""
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            # Header
            f.write("Workspace,Python,JavaScript,Bash,HTML,CSS,Total Files,Python Issues,JavaScript Issues,Bash Issues,Security Issues,Status\n")
            
            # Data rows
            for ws in self.metrics['workspaces']:
                total_files = sum(ws['files'].values())
                total_issues = sum(ws['issues'].values())
                
                f.write(f"{ws['name']},")
                f.write(f"{ws['files'].get('python', 0)},")
                f.write(f"{ws['files'].get('javascript', 0)},")
                f.write(f"{ws['files'].get('bash', 0)},")
                f.write(f"{ws['files'].get('html', 0)},")
                f.write(f"{ws['files'].get('css', 0)},")
                f.write(f"{total_files},")
                f.write(f"{ws['issues'].get('python', 0)},")
                f.write(f"{ws['issues'].get('javascript', 0)},")
                f.write(f"{ws['issues'].get('bash', 0)},")
                f.write(f"{ws['issues'].get('security', 0)},")
                f.write(f"{ws['status']}\n")
        
        print(f"✓ CSV report saved to: {output_file}")

    def _generate_html(self) -> str:
        """Generate enhanced HTML dashboard."""
        total_files = self.metrics['summary']['total_files']
        total_issues = self.metrics['summary']['total_issues']
        
        language_rows = "".join([
            f"<tr><td>{lang}</td><td align='right'>{count}</td></tr>"
            for lang, count in sorted(self.metrics['summary']['by_language'].items(), key=lambda x: x[1], reverse=True)
        ])
        
        workspace_rows = "".join([
            f"<tr>"
            f"<td><strong>{ws['name']}</strong></td>"
            f"<td align='right'>{ws['files'].get('python', 0)}</td>"
            f"<td align='right'>{ws['files'].get('javascript', 0)}</td>"
            f"<td align='right'>{ws['files'].get('bash', 0)}</td>"
            f"<td align='right'>{sum(ws['files'].values())}</td>"
            f"<td align='right'>{sum(ws['issues'].values())}</td>"
            f"<td><span style='color: green; font-weight: bold;'>✓ {ws['status'].upper()}</span></td>"
            f"</tr>"
            for ws in self.metrics['workspaces']
        ])
        
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Validator - Metrics Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f7fa;
            padding: 20px;
        }}
        
        .container {{ max-width: 1200px; margin: 0 auto; }}
        
        header {{
            background: white;
            border-radius: 8px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        
        h1 {{
            color: #2c3e50;
            margin-bottom: 5px;
        }}
        
        .subtitle {{
            color: #7f8c8d;
            font-size: 14px;
        }}
        
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .metric-card {{
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border-left: 4px solid #3498db;
        }}
        
        .metric-value {{
            font-size: 32px;
            font-weight: bold;
            color: #2c3e50;
            margin: 10px 0;
        }}
        
        .metric-label {{
            color: #7f8c8d;
            font-size: 13px;
        }}
        
        .section {{
            background: white;
            border-radius: 8px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        
        h2 {{
            color: #2c3e50;
            margin-bottom: 20px;
            font-size: 18px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        
        th {{
            background: #ecf0f1;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: #2c3e50;
            border-bottom: 2px solid #bdc3c7;
        }}
        
        td {{
            padding: 12px;
            border-bottom: 1px solid #ecf0f1;
        }}
        
        tr:hover {{
            background: #f9f9f9;
        }}
        
        .footer {{
            text-align: center;
            color: #95a5a6;
            font-size: 12px;
            margin-top: 30px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 AI Validator - Metrics Dashboard</h1>
            <p class="subtitle">Generated: {self.metrics['aggregated_at']}</p>
        </header>
        
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Total Files Analyzed</div>
                <div class="metric-value">{total_files}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Total Issues Found</div>
                <div class="metric-value">{total_issues}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Workspaces</div>
                <div class="metric-value">{len(self.metrics['workspaces'])}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Overall Status</div>
                <div class="metric-value" style="color: #27ae60;">✓ OK</div>
            </div>
        </div>
        
        <div class="section">
            <h2>📈 Files by Language</h2>
            <table>
                <thead>
                    <tr><th>Language</th><th align="right">Files</th></tr>
                </thead>
                <tbody>
                    {language_rows}
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <h2>🔍 Workspace Summary</h2>
            <table>
                <thead>
                    <tr>
                        <th>Workspace</th>
                        <th align="right">Python</th>
                        <th align="right">JavaScript</th>
                        <th align="right">Bash</th>
                        <th align="right">Total Files</th>
                        <th align="right">Issues</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {workspace_rows}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>AI Validator v2.0 | Metrics Collector</p>
        </div>
    </div>
</body>
</html>"""

def main():
    if len(sys.argv) < 2:
        print("Usage: metrics-collector.py <reports_dir> [output_dir]")
        sys.exit(1)
    
    reports_dir = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    
    # Create collector
    collector = MetricsCollector(reports_dir)
    
    # Collect metrics
    print(f"📂 Collecting metrics from: {reports_dir}")
    metrics = collector.collect_all()
    
    # Save outputs
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    collector.save_json(str(output_path / "metrics.json"))
    collector.save_html(str(output_path / "metrics-dashboard.html"))
    collector.save_csv(str(output_path / "metrics.csv"))
    
    print(f"\n✓ Metrics collection complete!")
    print(f"  - Total Files: {metrics['summary']['total_files']}")
    print(f"  - Total Issues: {metrics['summary']['total_issues']}")
    print(f"  - Workspaces: {len(metrics['workspaces'])}")

if __name__ == "__main__":
    main()
