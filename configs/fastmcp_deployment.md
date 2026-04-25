# FastMCP HTTP Server Deployment
================================

## Installation
```bash
pip install fastmcp
```

## Run All Three Servers
```bash
# EHR Server (port 8001)
python src/servers/mcp_servers.py --server ehr

# Pharmacy Server (port 8002)  
python src/servers/mcp_servers.py --server pharmacy

# Billing Server (port 8003)
python src/servers/mcp_servers.py --server billing
```

## MCP Client Connection
```python
from mcp import ClientSession
from mcp.client.sse import sse_client

async def call_ehr(patient_id: str, role: str):
    async with sse_client("http://localhost:8001/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(
                "get_discharge_medications",
                {"patient_id": patient_id, "caller_role": role}
            )
            return result.content[0].text
```

## Docker Compose
```yaml
version: '3.8'
services:
  ehr-server:
    build: .
    command: python src/servers/mcp_servers.py --server ehr
    ports: ["8001:8001"]
    environment:
      DATA_DIR: /app/data
  pharmacy-server:
    build: .
    command: python src/servers/mcp_servers.py --server pharmacy
    ports: ["8002:8002"]
  billing-server:
    build: .
    command: python src/servers/mcp_servers.py --server billing
    ports: ["8003:8003"]
```
