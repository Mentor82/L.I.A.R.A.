#!/bin/bash

################################################################################
# AI Validator - Report Aggregation & Analysis Script
# Purpose: Collect, aggregate, and analyze validation reports
# Usage: ./aggregate-reports.sh [reports_dir] [output_dir]
################################################################################

set -e

REPORTS_DIR="${1:-.}"
OUTPUT_DIR="${2:-.}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Ensure directories exist
mkdir -p "$OUTPUT_DIR"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $@"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $@"
}

################################################################################
# Aggregation Functions
################################################################################

# Extract metrics from metrics.txt
extract_metrics() {
    local file="$1"
    local workspace="$2"
    
    if [ ! -f "$file" ]; then
        return
    fi
    
    cat "$file" | grep -E "^(Python|JavaScript|TypeScript|Bash|HTML|CSS|PHP|C/C++).*files:" | \
    while read line; do
        echo "workspace=$workspace $line"
    done
}

# Count issues from JSON reports
count_issues() {
    local file="$1"
    local type="$2"
    
    if [ ! -f "$file" ]; then
        echo "0"
        return
    fi
    
    case "$type" in
        "security")
            # Count Bandit issues
            grep -c '"severity":' "$file" 2>/dev/null || echo "0"
            ;;
        "lint")
            # Count ESLint issues
            grep -c '"message":' "$file" 2>/dev/null || echo "0"
            ;;
        *)
            echo "0"
            ;;
    esac
}

# Generate summary from metrics
generate_summary() {
    local reports_dir="$1"
    local output_file="$2"
    
    cat > "$output_file" << 'EOF'
# AI Validator - Aggregated Report Summary

## Overview

Generated: $(date)

## Workspace Statistics

| Workspace | Python Files | JavaScript Files | Bash Scripts | Total Issues |
|-----------|--------------|------------------|--------------|--------------|
EOF
    
    # Iterate through each workspace directory
    for ws_dir in "$reports_dir"/*/; do
        if [ -d "$ws_dir" ]; then
            ws_name=$(basename "$ws_dir")
            
            # Extract metrics
            python_files=$(grep "^Python files:" "$ws_dir/metrics.txt" 2>/dev/null | grep -o '[0-9]*' | head -1 || echo "0")
            js_files=$(grep "^JavaScript files:" "$ws_dir/metrics.txt" 2>/dev/null | grep -o '[0-9]*' | head -1 || echo "0")
            bash_scripts=$(grep "^Bash scripts:" "$ws_dir/metrics.txt" 2>/dev/null | grep -o '[0-9]*' | head -1 || echo "0")
            
            # Count issues
            security_issues=$(count_issues "$ws_dir/security-bandit.json" "security")
            lint_issues=$(count_issues "$ws_dir/javascript-eslint.json" "lint")
            total_issues=$((security_issues + lint_issues))
            
            echo "| $ws_name | $python_files | $js_files | $bash_scripts | $total_issues |" >> "$output_file"
        fi
    done
    
    cat >> "$output_file" << 'EOF'

## Validation Status

- ✅ Completed: $(date)
- 📊 Reports: Generated from $(ls -d */ | wc -l) workspaces

## Report Files

### By Workspace

EOF
    
    for ws_dir in "$reports_dir"/*/; do
        if [ -d "$ws_dir" ]; then
            ws_name=$(basename "$ws_dir")
            echo "
### $ws_name

**Files:**" >> "$output_file"
            
            ls -1 "$ws_dir" | grep -v "^$" | while read file; do
                echo "- \`$file\`" >> "$output_file"
            done
        fi
    done
}

################################################################################
# Main
################################################################################

log_info "Aggregating reports from: $REPORTS_DIR"
log_info "Output directory: $OUTPUT_DIR"

# Generate summary
generate_summary "$REPORTS_DIR" "$OUTPUT_DIR/SUMMARY.md"
log_success "Summary generated: $OUTPUT_DIR/SUMMARY.md"

# Copy all reports to output
if [ "$REPORTS_DIR" != "$OUTPUT_DIR" ]; then
    cp -r "$REPORTS_DIR"/* "$OUTPUT_DIR/" 2>/dev/null || true
    log_success "Reports copied to output directory"
fi

# Generate metrics JSON
cat > "$OUTPUT_DIR/metrics.json" << 'EOF'
{
  "aggregated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "workspaces": [
EOF

first=true
for ws_dir in "$REPORTS_DIR"/*/; do
    if [ -d "$ws_dir" ]; then
        ws_name=$(basename "$ws_dir")
        
        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$OUTPUT_DIR/metrics.json"
        fi
        
        cat >> "$OUTPUT_DIR/metrics.json" << EOF
    {
      "name": "$ws_name",
      "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
      "files": {
        "python": $(grep "^Python files:" "$ws_dir/metrics.txt" 2>/dev/null | grep -o '[0-9]*' | head -1 || echo "0"),
        "javascript": $(grep "^JavaScript files:" "$ws_dir/metrics.txt" 2>/dev/null | grep -o '[0-9]*' | head -1 || echo "0"),
        "bash": $(grep "^Bash scripts:" "$ws_dir/metrics.txt" 2>/dev/null | grep -o '[0-9]*' | head -1 || echo "0")
      }
    }
EOF
    fi
done

cat >> "$OUTPUT_DIR/metrics.json" << 'EOF'
  ]
}
EOF

log_success "Metrics JSON generated: $OUTPUT_DIR/metrics.json"

# Generate HTML report
cat > "$OUTPUT_DIR/index.html" << 'EOFHTML'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Validator - Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        header {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 10px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
        }
        
        h1 {
            color: #667eea;
            margin-bottom: 10px;
        }
        
        .subtitle {
            color: #666;
            font-size: 14px;
        }
        
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
        
        .reports-table {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 10px;
            padding: 30px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: #667eea;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }
        
        td {
            padding: 12px;
            border-bottom: 1px solid #eee;
        }
        
        tr:hover {
            background: #f5f5f5;
        }
        
        .status-ok {
            color: #27ae60;
            font-weight: bold;
        }
        
        .status-warning {
            color: #f39c12;
            font-weight: bold;
        }
        
        .status-error {
            color: #e74c3c;
            font-weight: bold;
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
            <h1>🔍 AI Validator - Dashboard</h1>
            <p class="subtitle">Multi-Language Code Validation Reports</p>
        </header>
        
        <div class="metrics-grid" id="metrics">
            <div class="metric-card">
                <div class="metric-label">Total Workspaces</div>
                <div class="metric-value" id="workspace-count">-</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Total Files Analyzed</div>
                <div class="metric-value" id="file-count">-</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Issues Found</div>
                <div class="metric-value" id="issue-count">-</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Last Updated</div>
                <div style="font-size: 14px; color: #666; margin-top: 10px;" id="last-updated">-</div>
            </div>
        </div>
        
        <div class="reports-table">
            <h2>📊 Workspace Summary</h2>
            <table id="workspaces-table">
                <thead>
                    <tr>
                        <th>Workspace</th>
                        <th>Python Files</th>
                        <th>JavaScript Files</th>
                        <th>Bash Scripts</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody id="table-body">
                    <tr><td colspan="5" style="text-align: center; color: #999;">Loading metrics...</td></tr>
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>AI Validator v2.0 | Generated by aggregation system</p>
        </div>
    </div>
    
    <script>
        // Load metrics
        fetch('metrics.json')
            .then(response => response.json())
            .then(data => {
                const workspaces = data.workspaces;
                let totalFiles = 0;
                let totalIssues = 0;
                
                // Update header metrics
                document.getElementById('workspace-count').textContent = workspaces.length;
                document.getElementById('last-updated').textContent = new Date(data.aggregated_at).toLocaleString();
                
                // Populate table
                const tbody = document.getElementById('table-body');
                tbody.innerHTML = '';
                
                workspaces.forEach(ws => {
                    const files = ws.files.python + ws.files.javascript + ws.files.bash;
                    totalFiles += files;
                    
                    const row = tbody.insertRow();
                    row.innerHTML = `
                        <td><strong>${ws.name}</strong></td>
                        <td>${ws.files.python}</td>
                        <td>${ws.files.javascript}</td>
                        <td>${ws.files.bash}</td>
                        <td><span class="status-ok">✓ OK</span></td>
                    `;
                });
                
                document.getElementById('file-count').textContent = totalFiles.toLocaleString();
                document.getElementById('issue-count').textContent = totalIssues || '0';
            })
            .catch(error => {
                console.error('Error loading metrics:', error);
                document.getElementById('table-body').innerHTML = 
                    '<tr><td colspan="5" style="text-align: center; color: #e74c3c;">Error loading metrics</td></tr>';
            });
    </script>
</body>
</html>
EOFHTML

log_success "HTML dashboard generated: $OUTPUT_DIR/index.html"

echo ""
log_success "Aggregation complete!"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "📁 Output directory: ${YELLOW}$OUTPUT_DIR${NC}"
echo -e "📄 Summary: ${YELLOW}$OUTPUT_DIR/SUMMARY.md${NC}"
echo -e "📊 Metrics JSON: ${YELLOW}$OUTPUT_DIR/metrics.json${NC}"
echo -e "🌐 Dashboard: ${YELLOW}$OUTPUT_DIR/index.html${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
