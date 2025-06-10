import sys
import os
import json
import time
import logging

# This example assumes it's located in the 'rhino_mcp' directory,
# and 'programmatic_client.py' is also in this 'rhino_mcp' directory.
# To run this example:
# 1. Ensure the rhino-mcp package is installed, preferably in editable mode.
#    From the project root directory (the one containing the 'rhino_mcp' folder that has pyproject.toml):
#    python -m pip install -e ./rhino_mcp
#    (This assumes pyproject.toml is inside your 'rhino_mcp' folder)
# 2. Then run this script from the project root:
#    python rhino_mcp/run_client_example.py

from rhino_mcp.programmatic_client import MCPClient


# Setup basic logging for the example
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MCPClientExample")

def main():
    logger.info("--- Starting Rhino-MCP Programmatic Client Example ---")

    # --- Configuration ---
    # If the rhino-mcp package is installed in a specific Python environment,
    # provide the path to that Python executable. Otherwise, it uses sys.executable.
    # This python_exe MUST have the rhino-mcp package installed/available.
    # Example: python_exe = "/path/to/your/conda/envs/rhino_mcp_env/bin/python"
    python_exe = sys.executable

    # Instantiate the client
    client = MCPClient(python_executable=python_exe)

    try:
        # Start the MCP server (this will run rhino_mcp.rhino_mcp.server)
        logger.info("Attempting to start the MCP server...")
        client.start_server()
        logger.info("MCP server starting process initiated. Waiting a few seconds for initialization...")
        logger.info("The MCP server logs (MCP_SERVER_STDOUT/STDERR from client's logger) should show its status and connection attempts.")
        time.sleep(5) # Give server time to initialize and attempt connections

        if not client.mcp_server_process or client.mcp_server_process.poll() is not None:
            logger.error("MCP server process failed to start or terminated prematurely. Exiting example.")
            logger.error("Please ensure the rhino-mcp package is installed in the Python environment:")
            logger.error(f"  Current Python: {python_exe}")
            logger.error(f"  To install (editable mode from project root containing the 'rhino_mcp' folder which has pyproject.toml):")
            logger.error(f"    python -m pip install -e ./rhino_mcp") # Assuming pyproject.toml is in ./rhino_mcp/ relative to project root
            logger.error("Also check server logs printed by the MCPClient's logger for more specific errors.")
            return

        logger.info("MCP Server process appears to be running.")
        logger.info("--- Important Note ---")
        logger.info("For the following Rhino/Grasshopper commands to have an effect,")
        logger.info("Rhino must be running with 'rhino_mcp_client.py' active, and")
        logger.info("Grasshopper must have 'grasshopper_mcp_client.gh' open with the server component active.")
        logger.info("Otherwise, the MCP server will report connection errors for those tools, which is expected.")
        logger.info("-----------------------")


        # --- Rhino Examples (Illustrative) ---
        try:
            logger.info("Attempting to get Rhino layers...")
            response = client.get_rhino_layers()
            # The actual result is often inside a 'result' key in the response, or the structure varies.
            logger.info(f"Rhino Get Layers Full Response: {json.dumps(response, indent=2)}")
            if response.get("error"):
                 logger.warning(f"Error from get_rhino_layers: {response.get('error')}. This is expected if Rhino is not connected to the MCP server.")
        except RuntimeError as e:
            logger.error(f"RuntimeError calling get_rhino_layers: {e}")
            logger.warning("This may be expected if the MCP server isn't fully connected to Rhino.")

        try:
            logger.info("Attempting to execute a simple command in Rhino (create a point)...")
            rhino_code = """
import rhinoscriptsyntax as rs
pt_id = rs.AddPoint(1, 2, 3)
if pt_id:
    # The add_object_metadata function is injected by the MCP server tools
    add_object_metadata(pt_id, "ProgrammaticPoint", "Point created by programmatic client example")
    result = "Created point with ID: %s" % str(pt_id) # Using % formatting for IronPython 2.7
else:
    result = "Failed to create point"
"""
            response = client.execute_rhino_code(rhino_code)
            logger.info(f"Rhino Execute Code Response: {json.dumps(response, indent=2)}")
            if response.get("error"):
                 logger.warning(f"Error from execute_rhino_code: {response.get('error')}. Expected if Rhino not connected.")
        except RuntimeError as e:
            logger.error(f"RuntimeError calling execute_rhino_code: {e}")

        # --- Grasshopper Examples (Illustrative) ---
        try:
            logger.info("Attempting to check Grasshopper server availability...")
            response = client.is_gh_server_available()
            logger.info(f"Grasshopper Server Available Full Response: {json.dumps(response, indent=2)}")
            if response.get("result") is True: # is_server_available tool returns a boolean directly in 'result'
                logger.info("Grasshopper server is available!")
            elif response.get("error") or response.get("result") is False : # Check error or explicit False
                 logger.warning(f"Grasshopper server not available or error reported: {response.get('error', 'Result was False')}. Expected if GH client script not active.")
        except RuntimeError as e:
            logger.error(f"RuntimeError calling is_gh_server_available: {e}")

        try:
            logger.info("Attempting to get Grasshopper context (simplified)...")
            response = client.get_gh_context(simplified=True)
            logger.info(f"Grasshopper Get Context Full Response: {json.dumps(response, indent=2)}")
            if response.get("error"):
                 logger.warning(f"Error from get_gh_context: {response.get('error')}. Expected if GH not connected.")
        except RuntimeError as e:
            logger.error(f"RuntimeError calling get_gh_context: {e}")

        logger.info("Example commands attempted. Check logs for details and server output.")
        logger.info("Waiting for a few more seconds before shutting down...")
        time.sleep(10)

    except Exception as e:
        logger.error(f"An unexpected error occurred in the example: {e}", exc_info=True)
    finally:
        logger.info("Attempting to stop the MCP server...")
        client.stop_server()
        logger.info("MCP server stopped.")
        logger.info("--- Rhino-MCP Programmatic Client Example Finished ---")

if __name__ == "__main__":
    main()
