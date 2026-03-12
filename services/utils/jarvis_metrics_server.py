"""
J.A.R.V.I.S Metrics Server
HTTP server for exposing performance metrics
"""
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import uvicorn

from services.utils.jarvis_monitoring import get_performance_monitor
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-METRICS-SERVER")

# Create FastAPI app
app = FastAPI(
    title="J.A.R.V.I.S Metrics API",
    description="Real-time performance monitoring for J.A.R.V.I.S",
    version="1.0.0"
)

# Global monitor instance
monitor = get_performance_monitor()


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Metrics dashboard HTML"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>J.A.R.V.I.S Performance Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; background: #1a1a1a; color: #fff; }
            .container { max-width: 1200px; margin: 0 auto; }
            .header { text-align: center; margin-bottom: 30px; }
            .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
            .metric-card { background: #2a2a2a; padding: 20px; border-radius: 10px; border: 1px solid #444; }
            .metric-value { font-size: 2em; font-weight: bold; color: #00ff88; }
            .metric-label { font-size: 0.9em; color: #888; margin-bottom: 10px; }
            .chart-container { background: #2a2a2a; padding: 20px; border-radius: 10px; margin: 20px 0; }
            .status-healthy { color: #00ff88; }
            .status-warning { color: #ffaa00; }
            .status-critical { color: #ff4444; }
            .refresh-btn { background: #00ff88; color: #000; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; }
            .refresh-btn:hover { background: #00cc66; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 J.A.R.V.I.S Performance Dashboard</h1>
                <button class="refresh-btn" onclick="loadData()">Refresh Data</button>
            </div>
            
            <div class="metrics-grid" id="metricsGrid">
                <!-- Metrics will be populated here -->
            </div>
            
            <div class="chart-container">
                <h3>System Performance Over Time</h3>
                <canvas id="performanceChart"></canvas>
            </div>
            
            <div class="chart-container">
                <h3>Application Metrics</h3>
                <canvas id="applicationChart"></canvas>
            </div>
        </div>
        
        <script>
            let performanceChart, applicationChart;
            
            async function loadData() {
                try {
                    const response = await fetch('/api/metrics/current');
                    const data = await response.json();
                    updateMetricsDisplay(data);
                    updateCharts(data);
                } catch (error) {
                    console.error('Error loading data:', error);
                }
            }
            
            function updateMetricsDisplay(data) {
                const grid = document.getElementById('metricsGrid');
                const summary = data.summary || {};
                
                grid.innerHTML = `
                    <div class="metric-card">
                        <div class="metric-label">System Status</div>
                        <div class="metric-value status-${summary.status}">${summary.status?.toUpperCase()}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">CPU Usage</div>
                        <div class="metric-value">${summary.system?.cpu_percent?.toFixed(1) || 0}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Memory Usage</div>
                        <div class="metric-value">${summary.system?.memory_percent?.toFixed(1) || 0}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Total Requests</div>
                        <div class="metric-value">${summary.application?.total_requests || 0}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Error Rate</div>
                        <div class="metric-value">${summary.application?.error_rate?.toFixed(1) || 0}%</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Avg Response Time</div>
                        <div class="metric-value">${summary.application?.avg_response_time?.toFixed(0) || 0}ms</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">LLM Tokens</div>
                        <div class="metric-value">${summary.llm?.total_tokens || 0}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Uptime</div>
                        <div class="metric-value">${summary.uptime_hours?.toFixed(1) || 0}h</div>
                    </div>
                `;
            }
            
            function updateCharts(data) {
                // Update performance chart
                const ctx1 = document.getElementById('performanceChart').getContext('2d');
                if (performanceChart) performanceChart.destroy();
                
                performanceChart = new Chart(ctx1, {
                    type: 'line',
                    data: {
                        labels: ['Now'],
                        datasets: [{
                            label: 'CPU %',
                            data: [data.system?.cpu_percent || 0],
                            borderColor: '#00ff88',
                            backgroundColor: 'rgba(0, 255, 136, 0.1)',
                        }, {
                            label: 'Memory %',
                            data: [data.system?.memory_percent || 0],
                            borderColor: '#ff6b6b',
                            backgroundColor: 'rgba(255, 107, 107, 0.1)',
                        }]
                    },
                    options: {
                        responsive: true,
                        scales: {
                            y: { beginAtZero: true, max: 100 }
                        }
                    }
                });
                
                // Update application chart
                const ctx2 = document.getElementById('applicationChart').getContext('2d');
                if (applicationChart) applicationChart.destroy();
                
                applicationChart = new Chart(ctx2, {
                    type: 'bar',
                    data: {
                        labels: ['Requests', 'Errors', 'LLM Calls'],
                        datasets: [{
                            label: 'Count',
                            data: [
                                data.summary?.application?.total_requests || 0,
                                data.summary?.application?.error_count || 0,
                                data.summary?.llm?.api_calls || 0
                            ],
                            backgroundColor: ['#00ff88', '#ff6b6b', '#4dabf7']
                        }]
                    },
                    options: {
                        responsive: true,
                        scales: {
                            y: { beginAtZero: true }
                        }
                    }
                });
            }
            
            // Auto-refresh every 5 seconds
            setInterval(loadData, 5000);
            
            // Initial load
            loadData();
        </script>
    </body>
    </html>
    """


@app.get("/api/metrics/current")
async def get_current_metrics():
    """Get current performance metrics"""
    try:
        metrics = monitor.get_current_metrics()
        metrics["summary"] = monitor.get_performance_summary()
        return metrics
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error getting current metrics: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/metrics/history")
async def get_metrics_history(minutes: int = 60):
    """Get metrics history"""
    try:
        if minutes > 1440:  # Limit to 24 hours
            raise HTTPException(status_code=400, detail="Maximum history is 1440 minutes (24 hours)")
        
        history = monitor.get_metrics_history(minutes)
        return {
            "minutes": minutes,
            "data": history,
            "summary": monitor.get_performance_summary()
        }
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error getting metrics history: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/metrics/summary")
async def get_performance_summary():
    """Get performance summary"""
    try:
        return monitor.get_performance_summary()
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error getting performance summary: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    try:
        summary = monitor.get_performance_summary()
        status_code = 200 if summary["status"] == "healthy" else 503
        
        return {
            "status": summary["status"],
            "timestamp": datetime.now().isoformat(),
            "uptime_hours": summary["uptime_hours"]
        }, status_code
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error in health check: %s", e)
        return {
            "status": "error",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }, 500


@app.post("/api/metrics/export")
async def export_metrics(minutes: int = 60):
    """Export metrics to file"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"jarvis_metrics_{timestamp}.json"
        
        monitor.export_metrics(filename, minutes)
        
        return {
            "message": f"Metrics exported to {filename}",
            "filename": filename,
            "minutes": minutes
        }
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error exporting metrics: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/monitoring/start")
async def start_monitoring():
    """Start performance monitoring"""
    try:
        monitor.start_monitoring()
        return {"message": "Performance monitoring started"}
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error starting monitoring: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/monitoring/stop")
async def stop_monitoring():
    """Stop performance monitoring"""
    try:
        monitor.stop_monitoring()
        return {"message": "Performance monitoring stopped"}
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Error stopping monitoring: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


class MetricsServer:
    """Metrics server wrapper"""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        self.host = host
        self.port = port
        self.server = None
    
    async def start(self):
        """Start the metrics server"""
        logger.info("🌐 Starting metrics server on http://%s:%s", self.host, self.port)
        
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="info"
        )
        self.server = uvicorn.Server(config)
        await self.server.serve()
    
    def stop(self):
        """Stop the metrics server"""
        if self.server:
            self.server.should_exit = True
            logger.info("🛑 Metrics server stopped")


# Global metrics server instance
_metrics_server = None

def get_metrics_server(host: str = "127.0.0.1", port: int = 8000) -> MetricsServer:
    """Get global metrics server instance"""
    global _metrics_server # pylint: disable=global-statement
    if _metrics_server is None:
        _metrics_server = MetricsServer(host, port)
    return _metrics_server

async def start_metrics_server(host: str = "127.0.0.1", port: int = 8000):
    """Start metrics server"""
    server = get_metrics_server(host, port)
    await server.start()

if __name__ == "__main__":
    # Run server directly
    asyncio.run(start_metrics_server())
