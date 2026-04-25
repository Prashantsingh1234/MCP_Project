# Azure AI Foundry MCP Integration
====================================

## Setup
```bash
pip install azure-ai-projects azure-identity
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.api.azureml.ms"
```

## Register MCP Servers as Foundry Tools
```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

client = AIProjectClient(
    endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential()
)

# Create discharge agent with MCP tool access
agent = client.agents.create_agent(
    model="gpt-4o",
    name="DischargeCoordinationAgent",
    instructions="""
    You are a discharge coordination agent with access to EHR, Pharmacy, and Billing systems.
    For each discharge:
    1. Call EHR to get medications
    2. Check each drug in Pharmacy (handle name mismatches)
    3. Get billing-safe summary from EHR (PHI stripped)
    4. Generate invoice via Billing
    Always enforce: PHI must not cross to billing context.
    """,
    toolset=ToolSet([
        McpTool(server_url="http://ehr-server:8001/sse"),
        McpTool(server_url="http://pharmacy-server:8002/sse"),
        McpTool(server_url="http://billing-server:8003/sse"),
    ])
)
```

## Azure Monitor Telemetry
```python
from azure.monitor.opentelemetry import configure_azure_monitor
configure_azure_monitor(connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"])
# All MCP tool calls automatically traced
```
